"""Advanced slice settings: a grouped form plus a browser over every profile key.

"A slicer without the slicer": the same surface the CLI exposes (~25 named
``slice`` flags plus ``--set`` / ``--set-filament`` reaching every installed
OrcaSlicer process/filament setting), rendered as a form. Nothing here knows
what a setting *means* — the field table, parsing, filtering, and bucket
selection all live in ``tui/settings_model.py``, and the safety bounds stay in
``slicer.options._validate_slice_options`` (reached through
``interactive.core.overrides_problem``).

Two ways in:

* **The form** — named fields; blank leaves the profile default alone, exactly
  as an unset CLI flag does.
* **The browser** — search every key the installed profiles contain and add it
  as a ``KEY=VALUE`` override. The bucket comes from the profile the key was
  found in, so a filament key is never sent as a process override (the
  ``filament_flow_ratio`` silent-no-op). With no profiles readable (``--sim``
  on a machine with no slicer configured) the list is empty and free-form
  ``KEY=VALUE`` entry still works — unknown keys are warn-but-pass in the CLI.

Applying validates: type errors from the form and printer-safety refusals from
``_validate_slice_options`` (nozzle 999 °C) render inline and keep the screen
open. Nothing leaves this screen unvalidated.
"""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from bambu_cli.interactive.core import SliceOverrides, overrides_problem
from bambu_cli.tui.settings_model import (
    FILAMENT,
    PROCESS,
    SETTING_FIELDS,
    CatalogEntry,
    bucket_for_key,
    collect_field_overrides,
    fields_by_group,
    filter_catalog,
    load_catalog,
    parse_override_entry,
)

_BROWSER_ROWS = 200


# NOTE: ``Optional[...]`` and not ``SliceOverrides | None``. A class base is a
# runtime expression -- ``from __future__ import annotations`` does not defer it
# -- and ``type | None`` is a TypeError before Python 3.10. tests/python_compat_smoke.py
# guards this; see the class-base rule there.
class SettingsScreen(Screen[Optional[SliceOverrides]]):
    """Collect advanced slice overrides; dismisses with them, or None on cancel."""

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
                    yield Label(field.label, classes="settings-label")
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
            yield Input(placeholder="KEY=VALUE  (prefix filament: to force a filament setting)", id="override-entry")
            with Horizontal(id="settings-buttons"):
                yield Button("Add override", id="override-add")
                yield Button("Clear overrides", id="override-clear")
            yield Static("", id="override-list", markup=False)
            yield Static("", id="settings-error", markup=False)
            with Horizontal(id="settings-actions"):
                yield Button("Apply", id="settings-apply", variant="primary")
                yield Button("Cancel", id="settings-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self._catalog = load_catalog(self._profiles_dir)
        status = self.query_one("#browser-status", Static)
        if self._catalog:
            status.update(f"{len(self._catalog)} settings found in the installed profiles.")
        else:
            status.update("No profiles readable here — type overrides as KEY=VALUE below.")
        self._refresh_browser("")
        self._refresh_override_list()

    # --- browser ------------------------------------------------------------

    def _refresh_browser(self, query: str) -> None:
        option_list = self.query_one("#browser-list", OptionList)
        option_list.clear_options()
        matches = filter_catalog(self._catalog, query, limit=_BROWSER_ROWS)
        option_list.add_options([Option(entry.label, id=f"{entry.kind}:{entry.key}") for entry in matches])
        self.matches = matches

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "browser-search":
            self._refresh_browser(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Picking a key pre-fills the editor with ``key=<current example>``."""
        option_id = event.option.id or ""
        _kind, _, key = option_id.partition(":")
        entry = next((e for e in self._catalog if e.key == key), None)
        editor = self.query_one("#override-entry", Input)
        editor.value = f"{key}={entry.example if entry else ''}"
        editor.focus()

    # --- events -------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "override-entry":
            self._add_override()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "override-add":
            self._add_override()
        elif event.button.id == "override-clear":
            self._process.clear()
            self._filament.clear()
            self._refresh_override_list()
        elif event.button.id == "settings-apply":
            self.action_apply()
        elif event.button.id == "settings-cancel":
            self.action_cancel()

    def _add_override(self) -> None:
        editor = self.query_one("#override-entry", Input)
        key, value, bucket_hint, error = parse_override_entry(editor.value)
        error_box = self.query_one("#settings-error", Static)
        if error or key is None:
            error_box.update(error or "Invalid override.")
            return
        error_box.update("")
        # An explicit "filament:" / "process:" prefix wins; otherwise the profile
        # the key was found in decides (and unknown keys default to process).
        bucket = bucket_hint or bucket_for_key(self._catalog, key)
        target = self._filament if bucket == FILAMENT else self._process
        target[key] = value or ""
        editor.value = ""
        self._refresh_override_list()

    def _refresh_override_list(self) -> None:
        lines = [f"[{PROCESS}] {k}={v}" for k, v in sorted(self._process.items())]
        lines += [f"[{FILAMENT}] {k}={v}" for k, v in sorted(self._filament.items())]
        self.query_one("#override-list", Static).update("\n".join(lines))

    # --- apply / cancel -----------------------------------------------------

    def action_apply(self) -> None:
        raw = {field.dest: self.query_one(f"#{field.widget_id}", Input).value for field in SETTING_FIELDS}
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
