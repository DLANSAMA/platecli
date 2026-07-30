"""Prepare flow: source → material/quality/supports presets → slice → preview.

The view layer here holds no domain logic (plan §4.1): the source string is
validated by ``interactive.core.validate_source``, the AMS default comes from
``interactive.core.detect_ams_material``, the download/extract/slice sequence is
the wizard's own ``run_prepare_pipeline`` driven through ``PipelineService``,
and the preview rows come from ``interactive.core.preview_rows`` — so the TUI
and ``plate go`` cannot drift apart (in particular the pre-sliced caveat, which
must never imply the chosen material was applied to a file we did not slice).

Both blocking calls (AMS status read, prepare pipeline) run in Textual *thread*
workers and post their result back through ``App.call_from_thread``; neither can
raise into the UI (``PipelineService.prepare`` returns failures as values), so a
download or slicer failure renders inline instead of crashing the app.

Phase 2 stops at the preview: the print action is present but disabled — the
confirmation modal that may set ``confirm=True`` is Phase 3.
"""

from __future__ import annotations

import argparse
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RadioButton, RadioSet, Static

from bambu_cli.interactive.core import (
    DETECTED_AMS_TAG,
    MATERIAL_CHOICES,
    MATERIAL_GUIDANCE,
    QUALITY_CHOICES,
    QUALITY_GUIDANCE,
    detect_ams_material,
    make_workdir,
    validate_source,
)
from bambu_cli.tui.services import PrepareResult

_SETUP_HINT = "Run 'plate setup' in a terminal, then start the TUI again."


class PreflightErrorScreen(Screen):
    """Shown instead of the form when the configuration cannot slice or print.

    The TUI deliberately does not embed the setup wizard (plan §10 Q2): it names
    the problem and points at ``plate setup``.
    """

    BINDINGS = [
        ("escape", "back", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, problem: str) -> None:
        super().__init__()
        self._problem = problem

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="preflight-body"):
            yield Static("Not ready to prepare a print", id="preflight-title")
            yield Static(self._problem, id="preflight-problem", markup=False)
            yield Static(_SETUP_HINT, id="preflight-hint")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()


class PrepareScreen(Screen):
    """Collect a source + presets, then download/slice and show the preview."""

    BINDINGS = [
        ("escape", "back", "Back"),
    ]

    def __init__(self, args: argparse.Namespace, deps: Any) -> None:
        super().__init__()
        self._args = args
        self._deps = deps
        self._preparing = False
        self._detected_material: str | None = None
        self._detected_slot: int | None = None
        self.result: PrepareResult | None = None
        # The temp workdir is created HERE, before the worker starts, so this
        # screen can delete it even when the pipeline finishes after the user
        # has already left (the result never comes back in that case).
        self._workdir: str | None = None
        self._closed = False
        # True once the user has touched the material control: a slow AMS read
        # that lands afterwards must never re-pick for them.
        self._material_touched = False
        self._detection_button: RadioButton | None = None

    # --- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="prepare-body"):
            yield Label("Model source — URL or local file path")
            yield Input(
                placeholder="https://… or ~/models/cube.stl",
                id="source-input",
            )
            # markup=False: these render domain strings (paths, slicer errors)
            # verbatim — a "[" in a filename must not be parsed as Rich markup.
            yield Static("", id="source-error", markup=False)
            yield Label("Material")
            yield RadioSet(
                *[
                    RadioButton(
                        _material_label(name),
                        value=(name == MATERIAL_CHOICES[0]),
                        id=f"material-{name.lower()}",
                    )
                    for name in MATERIAL_CHOICES
                ],
                id="material-set",
            )
            yield Label("Quality")
            yield RadioSet(
                *[
                    RadioButton(
                        f"{name} — {QUALITY_GUIDANCE[name]}",
                        value=(name == "standard"),
                        id=f"quality-{name}",
                    )
                    for name in QUALITY_CHOICES
                ],
                id="quality-set",
            )
            yield Checkbox("Supports (big overhangs)", id="supports-check")
            yield Button("Prepare", id="prepare-button", variant="primary")
            yield Static("", id="prepare-status", markup=False)
            yield Static("", id="preview", markup=False)
            yield Button("Start print", id="print-button", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#source-input", Input).focus()
        # The AMS read blocks on MQTT; do it off the UI thread and apply the
        # pre-selection when (and only when) it comes back with a known material.
        self.run_worker(
            self._detect_worker,
            thread=True,
            exclusive=True,
            group="ams-detect",
            exit_on_error=False,
        )

    def on_unmount(self) -> None:
        # The prepare run owns a temp workdir; leaving the screen must not leak
        # it — including when a pipeline worker is still running, in which case
        # its late result is discarded (see _apply_result) and this deletes the
        # directory the worker was filling. cleanup_workdir tolerates a
        # directory that has already been removed, so the double path is safe.
        self._closed = True
        self._discard_workdir()

    def _discard_workdir(self) -> None:
        """Delete the temp workdir this screen created, if it still owns one."""
        workdir = self._workdir
        self._workdir = None
        self.result = None
        if workdir:
            self._deps.get_pipeline().cleanup_workdir(workdir)

    # --- AMS detection ------------------------------------------------------

    def _detect_worker(self) -> None:
        detector = self._deps.get_ams_detector()
        material, slot = detect_ams_material(detector, self._args)
        self.app.call_from_thread(self._apply_detection, material, slot)

    def _apply_detection(self, material: str | None, slot: int | None) -> None:
        if self._closed:
            return
        self._detected_material = material
        self._detected_slot = slot
        if material is None:
            return
        button = self.query_one(f"#material-{material.lower()}", RadioButton)
        # Always tag what the AMS reports, even when we leave the choice alone.
        button.label = _material_label(material, detected=True)
        if self._material_touched:
            # The user already chose while the (blocking) AMS read was in
            # flight — silently flipping their material would slice the wrong
            # filament profile.
            return
        # Remember the button we are about to press so the Changed message this
        # produces is not mistaken for a user interaction (Textual delivers it
        # asynchronously, so a transient flag around the assignment would be
        # cleared long before the handler runs).
        self._detection_button = button
        button.value = True

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "material-set":
            return
        if event.pressed is self._detection_button:
            self._detection_button = None
            return
        self._material_touched = True

    # --- selection readers (view state → plain values) ----------------------

    def selected_material(self) -> str:
        return _pressed(self.query_one("#material-set", RadioSet), MATERIAL_CHOICES)

    def selected_quality(self) -> str:
        return _pressed(self.query_one("#quality-set", RadioSet), QUALITY_CHOICES, default="standard")

    def selected_supports(self) -> bool:
        return bool(self.query_one("#supports-check", Checkbox).value)

    # --- events -------------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "source-input":
            self.query_one("#source-error", Static).update("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "source-input":
            self.start_prepare()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "prepare-button":
            self.start_prepare()

    # --- prepare ------------------------------------------------------------

    def start_prepare(self) -> None:
        """Validate the source, then run the pipeline in a thread worker."""
        if self._preparing:
            return
        raw = self.query_one("#source-input", Input).value
        source, error = validate_source(raw)
        if source is None:
            self.query_one("#source-error", Static).update(error or "Invalid source.")
            return
        self.query_one("#source-error", Static).update("")

        # A previous run's temp workdir is dead the moment we start another one.
        self._discard_workdir()
        # Create the workdir on this side of the worker: the screen must be able
        # to delete it on unmount without waiting for a result that may never
        # arrive (Escape mid-prepare).
        self._workdir = make_workdir(prefix="bambu-tui-")

        self._preparing = True
        self.query_one("#prepare-button", Button).disabled = True
        self.query_one("#prepare-status", Static).update("Downloading and slicing — this can take a minute or two…")
        self.run_worker(
            partial(
                self._prepare_worker,
                self._workdir,
                source,
                self.selected_material(),
                self.selected_quality(),
                self.selected_supports(),
            ),
            thread=True,
            exclusive=True,
            group="prepare",
            exit_on_error=False,
        )

    def _prepare_worker(self, workdir: str, source: str, material: str, quality: str, supports: bool) -> None:
        result = self._deps.get_pipeline().prepare(
            self._args,
            workdir=workdir,
            source=source,
            material=material,
            quality=quality,
            supports=supports,
            detected_material=self._detected_material,
            detected_slot=self._detected_slot,
        )
        self.app.call_from_thread(self._apply_result, result)

    def _apply_result(self, result: PrepareResult) -> None:
        if self._closed:
            # The user left while the pipeline ran: there is nobody to show this
            # to and the widgets are gone. Drop the products of the run rather
            # than touching an unmounted screen.
            self._deps.get_pipeline().cleanup(result.state)
            return
        self._preparing = False
        self.query_one("#prepare-button", Button).disabled = False
        status = self.query_one("#prepare-status", Static)
        preview = self.query_one("#preview", Static)
        if not result.ok:
            self.result = None
            status.update(result.error or "Preparing the model failed.")
            preview.update("")
            return
        self.result = result
        status.update("Ready.")
        preview.update("\n".join(f"{label:<11}{value}" for label, value in result.rows))


def _material_label(name: str, *, detected: bool = False) -> str:
    """Radio-button label for a material, tagged when the AMS has it loaded."""
    tag = f"  {DETECTED_AMS_TAG}" if detected else ""
    return f"{name} — {MATERIAL_GUIDANCE[name]}{tag}"


def _pressed(radio_set: RadioSet, choices: list[str], default: str | None = None) -> str:
    """Return the chosen value of a radio set, falling back to a safe default."""
    index = radio_set.pressed_index
    if index is None or index < 0 or index >= len(choices):
        return default if default is not None else choices[0]
    return choices[index]
