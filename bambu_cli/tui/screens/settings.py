"""Advanced slice settings: a grouped form plus a browser over every profile key.

"A slicer without the slicer": the same surface the CLI exposes (~25 named
``slice`` flags plus ``--set`` / ``--set-filament`` reaching every installed
OrcaSlicer process/filament setting), rendered as a form. Nothing here knows
what a setting *means* — the field table, parsing, filtering, editor inference
and bucket selection all live in ``tui/settings_model.py``, and the safety bounds
stay in ``slicer.options._validate_slice_options`` (reached through
``interactive.core.overrides_problem``).

Two ways in:

* **The form** — named fields; blank leaves the profile default alone, exactly
  as an unset CLI flag does. Fields with a closed option set are dropdowns, so
  there is nothing to mistype.
* **The browser** — search every key the installed profiles contain, pick one,
  and edit *just its value*. Picking a key fills in its name, pins the bucket to
  whichever profile the key came from (the ``filament_flow_ratio`` silent-no-op
  is why the bucket is a fact and not a guess), shows the profile's own value as
  the starting point, and chooses the control from the values that key actually
  takes across the installed profiles: a toggle for ``0``/``1``, a dropdown for a
  short closed set, a number box, or free text.

Free text stays reachable for everything the profiles cannot describe — an
unknown key, or a value like a custom g-code block that no picker can represent.
With no profiles readable (``--sim`` on a machine with no slicer configured) the
browser is empty and the name and bucket are entered by hand; the CLI's
warn-but-pass handling of unknown keys already tolerates that.

Applying validates: type errors from the form and printer-safety refusals from
``_validate_slice_options`` (nozzle 999 °C) render inline and keep the screen
open. Nothing leaves this screen unvalidated.
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    Switch,
)
from textual.widgets.option_list import Option

from bambu_cli.interactive.core import SliceOverrides, overrides_problem
from bambu_cli.tui.settings_model import (
    EDITOR_NUMBER,
    EDITOR_SELECT,
    EDITOR_SWITCH,
    FILAMENT,
    PROCESS,
    SETTING_FIELDS,
    CatalogEntry,
    SettingField,
    bucket_for_key,
    collect_field_overrides,
    fields_by_group,
    filter_catalog,
    load_catalog,
)

_BROWSER_ROWS = 200
_NO_OVERRIDE_PROMPT = "(profile default)"
# Sentinel dropdown entry that reveals the free-text box. The observed values of
# a key are what the *installed* profiles happen to use, which is a subset of what
# OrcaSlicer accepts -- profiles that only ever say ``grid`` must not make
# ``gyroid`` unreachable. The dropdown is a shortcut, never a cage.
_CUSTOM_VALUE = "\x00custom"
_CUSTOM_LABEL = "(type a custom value…)"


# NOTE: ``Optional[...]`` and not ``SliceOverrides | None``. A class base is a
# runtime expression -- ``from __future__ import annotations`` does not defer it
# -- and ``type | None`` is a TypeError before Python 3.10. tests/python_compat_smoke.py
# guards this; see the class-base rule there.
class SettingsScreen(Screen[Optional[SliceOverrides]]):
    """Collect advanced slice overrides; dismisses with them, or None on cancel."""

    # Header watches screen.sub_title; without this every screen claimed to be
    # the printer dashboard.
    SUB_TITLE = "advanced slice settings"

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("f1", "app.help", "Help"),
    ]

    def __init__(self, overrides: SliceOverrides | None = None, profiles_dir: str | None = None) -> None:
        super().__init__()
        start = overrides or SliceOverrides()
        # Work on copies: cancelling must leave the caller's overrides untouched.
        self._fields: dict[str, Any] = dict(start.fields)
        self._process: dict[str, str] = dict(start.process)
        self._filament: dict[str, str] = dict(start.filament)
        self._profiles_dir = profiles_dir
        self._catalog: list[CatalogEntry] = []
        self._browser_matches: list[CatalogEntry] = []
        # Which key the value editor is currently configured for, and the option
        # set the dropdown holds. Both exist to make a repeat configure a no-op:
        # assigning to Input.value posts Changed *asynchronously*, so the handler
        # lands after we have already prefilled and must not undo it.
        self._editor_key: str | None = None
        self._select_values: tuple[str, ...] = ()

    # --- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="settings-body"):
            yield Static(
                "Blank fields keep the profile default — exactly like leaving a slice flag off.",
                id="settings-intro",
                markup=False,
            )
            for group, fields in fields_by_group():
                yield Label(group, classes="settings-group")
                for field in fields:
                    # Label and control share one row: the form is 25 fields long
                    # and stacking them put four on a screen.
                    with Horizontal(classes="settings-row"):
                        yield Label(field.label, classes="settings-label")
                        if field.kind == "choice" and field.choices:
                            yield Select(
                                [(choice, choice) for choice in field.choices],
                                prompt=_NO_OVERRIDE_PROMPT,
                                value=self._initial_choice(field.dest, field.choices),
                                id=field.widget_id,
                                classes="settings-input",
                            )
                        else:
                            yield Input(
                                value=_as_text(self._fields.get(field.dest)),
                                placeholder=field.hint,
                                id=field.widget_id,
                                classes="settings-input",
                            )

            yield Label("All settings", classes="settings-group")
            yield Static("", id="browser-status", markup=False)
            yield Input(placeholder="search every profile setting…", id="browser-search")
            yield OptionList(id="browser-list")

            yield Label("Add an override", classes="settings-group")
            with Horizontal(classes="settings-row"):
                yield Label("Setting", classes="settings-label")
                yield Input(
                    placeholder="pick one above, or type a key",
                    id="override-key",
                    classes="settings-input",
                )
            with Horizontal(classes="settings-row"):
                yield Label("Applies to", classes="settings-label")
                yield Select(
                    [("process", PROCESS), ("filament", FILAMENT)],
                    prompt="process",
                    value=PROCESS,
                    allow_blank=False,
                    id="override-bucket",
                    classes="settings-input",
                )
            # All three editors exist from the start and are shown one at a time.
            # Swapping `display` avoids mounting/removing widgets mid-interaction,
            # which is where Textual timing bugs live. A hidden widget takes no
            # space, so they can share one row.
            with Horizontal(classes="settings-row"):
                yield Label("Value", classes="settings-label")
                yield Select([], prompt="choose a value", id="override-select", classes="settings-input")
                yield Switch(id="override-switch", classes="settings-switch")
                yield Input(placeholder="value", id="override-value", classes="settings-input")
            yield Static("", id="override-default", markup=False)
            with Horizontal(id="settings-buttons"):
                yield Button("Add / update", id="override-add")
                yield Button("Remove selected", id="override-remove")
                yield Button("Clear all", id="override-clear")
            yield Label("Pending overrides", classes="settings-label")
            yield OptionList(id="override-current")

            yield Static("", id="settings-error", markup=False)
            with Horizontal(id="settings-actions"):
                yield Button("Apply", id="settings-apply", variant="primary")
                yield Button("Cancel", id="settings-cancel")
        yield Footer()

    def _initial_choice(self, dest: str, choices: tuple[str, ...]) -> Any:
        """Pre-selected dropdown value, or BLANK when there is no override."""
        current = self._fields.get(dest)
        text = "" if current is None else str(current)
        return text if text in choices else Select.BLANK

    def on_mount(self) -> None:
        self._catalog = load_catalog(self._profiles_dir)
        status = self.query_one("#browser-status", Static)
        if self._catalog:
            status.update(f"{len(self._catalog)} settings found in the installed profiles.")
        else:
            status.update("No profiles readable here — type a setting name and pick its bucket.")
        self._refresh_browser("")
        self._show_editor(None)
        self._refresh_override_list()

    # --- browser ------------------------------------------------------------

    def _refresh_browser(self, query: str) -> None:
        option_list = self.query_one("#browser-list", OptionList)
        option_list.clear_options()
        matches = filter_catalog(self._catalog, query, limit=_BROWSER_ROWS)
        # Text(), not str: a str prompt is parsed as Rich markup, which silently
        # ate the "[process]"/"[filament]" tag and would eat any bracketed
        # profile value (list-valued settings render as "[0.98]").
        option_list.add_options([Option(Text(entry.label), id=f"{entry.kind}:{entry.key}") for entry in matches])
        self._browser_matches = matches

    def _entry_for(self, key: str) -> CatalogEntry | None:
        return next((e for e in self._catalog if e.key == key), None)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "browser-search":
            self._refresh_browser(event.value)
        elif event.input.id == "override-key":
            # Typing a known key adopts its bucket and editor, same as picking it.
            self._configure_for_key(event.value.strip(), prefill=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "browser-list":
            option_id = event.option.id or ""
            _kind, _, key = option_id.partition(":")
            key_input = self.query_one("#override-key", Input)
            # Assigning to .value fires Changed -> _configure_for_key(prefill=False);
            # the explicit call below then prefills the profile's own value.
            key_input.value = key
            self._configure_for_key(key, prefill=True)
        elif event.option_list.id == "override-current":
            self._load_pending(event.option.id or "")

    # --- the value editor ---------------------------------------------------

    def _show_editor(self, editor: str | None) -> None:
        """Show exactly one value control; free text is the fallback for all else."""
        self.query_one("#override-select", Select).display = editor == EDITOR_SELECT
        self.query_one("#override-switch", Switch).display = editor == EDITOR_SWITCH
        value_input = self.query_one("#override-value", Input)
        value_input.display = editor not in (EDITOR_SELECT, EDITOR_SWITCH)
        value_input.placeholder = "number" if editor == EDITOR_NUMBER else "value"

    @staticmethod
    def _set_note(box: Static, text: str) -> None:
        """Write the profile-value note, collapsing the line when it is empty.

        An empty Static still occupies its padding, which left a gap in the
        middle of the editor before a key was chosen.
        """
        box.update(text)
        box.display = bool(text)

    def _configure_for_key(self, key: str, prefill: bool) -> None:
        """Point the editor at ``key``: bucket, profile default, and control.

        A repeat call for the same key without ``prefill`` is a no-op, which is
        what makes the async ``Input.Changed`` that follows a programmatic
        ``key_input.value = key`` harmless instead of destructive.
        """
        if key == self._editor_key and not prefill:
            return
        self._editor_key = key
        entry = self._entry_for(key)
        default_box = self.query_one("#override-default", Static)
        bucket_select = self.query_one("#override-bucket", Select)

        if entry is None:
            note = "" if not key else f"{key} is not in the installed profiles — it will be sent as typed."
            self._set_note(default_box, note)
            self._select_values = ()
            self._show_editor(None)
            # An unclassifiable key starts at process, the bucket a bare `--set`
            # uses. Inheriting the previous key's bucket would silently send a
            # process setting as a filament override -- the mirror of the
            # no-op this split exists to prevent.
            bucket_select.value = PROCESS
            return

        # The source profile decides the bucket; it is a fact, not a preference.
        bucket_select.value = entry.kind
        self._set_note(default_box, f"Profile value: {entry.example}   ({entry.kind} setting)")
        editor = entry.editor
        self._show_editor(editor)

        if editor == EDITOR_SELECT:
            select = self.query_one("#override-select", Select)
            select.set_options([(value, value) for value in entry.values] + [(_CUSTOM_LABEL, _CUSTOM_VALUE)])
            self._select_values = entry.values
            if prefill and entry.example in entry.values:
                select.value = entry.example
        elif editor == EDITOR_SWITCH:
            self._select_values = ()
            if prefill:
                self.query_one("#override-switch", Switch).value = entry.example == "1"
        else:
            self._select_values = ()
            if prefill:
                self.query_one("#override-value", Input).value = entry.example

    def on_select_changed(self, event: Select.Changed) -> None:
        """Choosing "custom" in the dropdown reveals the free-text box."""
        if event.select.id == "override-select":
            self.query_one("#override-value", Input).display = event.value == _CUSTOM_VALUE

    def _editor_value(self) -> str | None:
        """The visible control's value, or ``None`` when nothing was chosen.

        ``None`` is only possible from an untouched dropdown: blank there means
        "not chosen yet", never "set this to the empty string". An empty *text*
        value is a real value — clearing a setting (a custom g-code block, say)
        is a legitimate override — so it comes back as ``""``.
        """
        select = self.query_one("#override-select", Select)
        if select.display:
            if select.value is Select.BLANK:
                return None
            if select.value == _CUSTOM_VALUE:
                return self.query_one("#override-value", Input).value.strip()
            return str(select.value)
        switch = self.query_one("#override-switch", Switch)
        if switch.display:
            return "1" if switch.value else "0"
        return self.query_one("#override-value", Input).value.strip()

    # --- events -------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("override-key", "override-value"):
            self._add_override()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "override-add":
            self._add_override()
        elif event.button.id == "override-remove":
            self._remove_selected()
        elif event.button.id == "override-clear":
            self._process.clear()
            self._filament.clear()
            self._refresh_override_list()
        elif event.button.id == "settings-apply":
            self.action_apply()
        elif event.button.id == "settings-cancel":
            self.action_cancel()

    def _add_override(self) -> None:
        key = self.query_one("#override-key", Input).value.strip()
        error_box = self.query_one("#settings-error", Static)
        if not key:
            error_box.update("Pick a setting above, or type its name.")
            return
        value = self._editor_value()
        if value is None:  # an untouched dropdown -- see _editor_value
            error_box.update(f"Choose a value for {key}.")
            return
        error_box.update("")
        bucket_select = self.query_one("#override-bucket", Select)
        chosen = bucket_select.value
        # An unknown key keeps whatever bucket the user picked; a known key is
        # pinned to the profile it came from.
        bucket = bucket_for_key(self._catalog, key, default=str(chosen))
        target = self._filament if bucket == FILAMENT else self._process
        target[key] = value
        # Adding to one bucket must not leave a stale copy in the other.
        other = self._process if bucket == FILAMENT else self._filament
        other.pop(key, None)
        self._refresh_override_list()

    def _remove_selected(self) -> None:
        option_list = self.query_one("#override-current", OptionList)
        index = option_list.highlighted
        if index is None:
            self.query_one("#settings-error", Static).update("Select a pending override to remove.")
            return
        option = option_list.get_option_at_index(index)
        self._forget(option.id or "")
        self._refresh_override_list()

    def _forget(self, option_id: str) -> None:
        bucket, _, key = option_id.partition(":")
        (self._filament if bucket == FILAMENT else self._process).pop(key, None)

    def _load_pending(self, option_id: str) -> None:
        """Clicking a pending override loads it back into the editor to edit."""
        bucket, _, key = option_id.partition(":")
        if not key:
            return
        source = self._filament if bucket == FILAMENT else self._process
        self.query_one("#override-key", Input).value = key
        self._configure_for_key(key, prefill=False)
        self.query_one("#override-bucket", Select).value = bucket
        current = source.get(key, "")
        select = self.query_one("#override-select", Select)
        switch = self.query_one("#override-switch", Switch)
        if select.display:
            if current in self._select_values:
                select.value = current
            else:
                # A value the profiles never showed round-trips through custom.
                select.value = _CUSTOM_VALUE
                self.query_one("#override-value", Input).value = current
        elif switch.display:
            switch.value = current == "1"
        else:
            self.query_one("#override-value", Input).value = current

    def _refresh_override_list(self) -> None:
        option_list = self.query_one("#override-current", OptionList)
        option_list.clear_options()
        options = [Option(Text(f"[{PROCESS}] {k}={v}"), id=f"{PROCESS}:{k}") for k, v in sorted(self._process.items())]
        options += [
            Option(Text(f"[{FILAMENT}] {k}={v}"), id=f"{FILAMENT}:{k}") for k, v in sorted(self._filament.items())
        ]
        option_list.add_options(options)

    # --- apply / cancel -----------------------------------------------------

    def _field_text(self, field: SettingField) -> str:
        """The form's current text for one field, whichever control renders it."""
        if field.kind == "choice" and field.choices:
            select = self.query_one(f"#{field.widget_id}", Select)
            return "" if select.value is Select.BLANK else str(select.value)
        return self.query_one(f"#{field.widget_id}", Input).value

    def action_apply(self) -> None:
        raw = {field.dest: self._field_text(field) for field in SETTING_FIELDS}
        parsed, errors = collect_field_overrides(raw)
        error_box = self.query_one("#settings-error", Static)
        if errors:
            error_box.update("\n".join(errors))
            return
        overrides = SliceOverrides(fields=parsed, process=dict(self._process), filament=dict(self._filament))
        # The printer-safety bounds, checked by the same code the CLI runs.
        problem = overrides_problem(overrides)
        if problem:
            error_box.update(problem)
            return
        error_box.update("")
        self.dismiss(overrides)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)
