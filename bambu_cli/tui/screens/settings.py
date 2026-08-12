"""Advanced slice settings: a grouped form plus a KEY=VALUE escape hatch.

The same surface the CLI exposes, rendered as a form: the ~25 named ``slice``
flags, plus ``--set`` / ``--set-filament`` for anything the named flags do not
cover. Nothing here knows what a setting *means* — the field table and parsing
live in ``tui/settings_model.py``, and the safety bounds stay in
``slicer.options._validate_slice_options`` (reached through
``interactive.core.overrides_problem``).

Two ways in:

* **The form** — named fields; blank leaves the profile default alone, exactly
  as an unset CLI flag does. Fields with a closed option set are dropdowns, so
  there is nothing to mistype.
* **Add an override** — a key, a bucket, and a value, for every setting the
  named flags do not name. Deliberately literal: the value is sent as typed and
  the bucket is the user's choice, which is exactly what ``--set`` /
  ``--set-filament`` do. Use ``slice --list-settings`` to see what your installed
  profiles actually accept.

The bucket matters and cannot be guessed: OrcaSlicer silently ignores a filament
setting (``filament_flow_ratio``) sent as a process override, so the *Applies to*
dropdown is a real choice, not a formality. It resets to process for each new
key rather than carrying the last one over.

Applying validates: type errors from the form and printer-safety refusals from
``_validate_slice_options`` (nozzle 999 °C) render inline and keep the screen
open. Nothing leaves this screen unvalidated.
"""

from __future__ import annotations

from typing import Any

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
)
from textual.widgets.option_list import Option

from bambu_cli.interactive.core import SliceOverrides, overrides_problem
from bambu_cli.tui.settings_model import (
    FILAMENT,
    PROCESS,
    SETTING_FIELDS,
    SettingField,
    collect_field_overrides,
    fields_by_group,
)

_NO_OVERRIDE_PROMPT = "(profile default)"


class SettingsScreen(Screen[SliceOverrides | None]):
    """Collect advanced slice overrides; dismisses with them, or None on cancel."""

    # Header watches screen.sub_title; without this every screen claimed to be
    # the printer dashboard.
    SUB_TITLE = "advanced slice settings"

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("f1", "app.help", "Help"),
    ]

    def __init__(self, overrides: SliceOverrides | None = None) -> None:
        super().__init__()
        start = overrides or SliceOverrides()
        # Work on copies: cancelling must leave the caller's overrides untouched.
        self._fields: dict[str, Any] = dict(start.fields)
        self._process: dict[str, str] = dict(start.process)
        self._filament: dict[str, str] = dict(start.filament)
        # The key the bucket dropdown currently describes. Typing a *different*
        # key resets the bucket (see _reset_bucket_for_new_key); ``_load_pending``
        # sets this first so the async Changed it triggers is a no-op and the
        # reloaded bucket survives.
        self._bucket_key: str | None = None

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

            yield Label("Add an override", classes="settings-group")
            yield Static(
                "For settings the fields above do not cover. `plate slice --list-settings` "
                "prints every key your profiles accept.",
                id="override-help",
                markup=False,
            )
            with Horizontal(classes="settings-row"):
                yield Label("Setting", classes="settings-label")
                yield Input(
                    placeholder="e.g. filament_flow_ratio",
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
            with Horizontal(classes="settings-row"):
                yield Label("Value", classes="settings-label")
                yield Input(placeholder="value", id="override-value", classes="settings-input")
            yield Static(
                "A filament setting sent as a process override is silently ignored by the slicer.",
                id="override-note",
                markup=False,
            )
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
        return text if text in choices else Select.NULL

    def on_mount(self) -> None:
        self._refresh_override_list()

    # --- events -------------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "override-current":
            self._load_pending(event.option.id or "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "override-key":
            self._reset_bucket_for_new_key(event.value.strip())

    def _reset_bucket_for_new_key(self, key: str) -> None:
        """Start each new key at process — never inherit the last key's bucket.

        Carrying the previous choice over would send a process setting as a
        filament override the moment the user follows a filament key with a
        process one, and OrcaSlicer ignores a misbucketed override *silently*.
        Process is the bucket a bare ``--set`` uses, so it is the safe default.
        """
        if key == self._bucket_key:
            return
        self._bucket_key = key
        self.query_one("#override-bucket", Select).value = PROCESS

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

    # --- the override editor ------------------------------------------------

    def _add_override(self) -> None:
        key = self.query_one("#override-key", Input).value.strip()
        error_box = self.query_one("#settings-error", Static)
        if not key:
            error_box.update("Type the name of the setting to override.")
            return
        error_box.update("")
        # The value is sent as typed -- an empty one included. Clearing a setting
        # (a custom g-code block, say) is a legitimate override, and this is what
        # `--set key=` does on the command line.
        value = self.query_one("#override-value", Input).value.strip()
        bucket = str(self.query_one("#override-bucket", Select).value)
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
        # Claim the key first: assigning to Input.value posts Changed
        # *asynchronously*, and that handler would otherwise land after this and
        # reset the bucket we are about to restore.
        self._bucket_key = key
        self.query_one("#override-key", Input).value = key
        self.query_one("#override-bucket", Select).value = bucket
        self.query_one("#override-value", Input).value = source.get(key, "")

    def _refresh_override_list(self) -> None:
        option_list = self.query_one("#override-current", OptionList)
        option_list.clear_options()
        # Text(), not str: a str prompt is parsed as Rich markup, which silently
        # ate the "[process]"/"[filament]" tag and would eat any bracketed
        # value (list-valued settings render as "[0.98]").
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
            return "" if select.is_blank() else str(select.value)
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
