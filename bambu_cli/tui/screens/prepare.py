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

From the preview the user can open the confirmation modal, which is the only
code path that may start a print. Opening it *transfers* ownership of the temp
workdir (see ``screens/confirm.py``'s docstring for the full ownership rules):
this screen deletes the workdir only while it still holds it.
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
        # Route through the app so the in-flight-job guard applies here too
        # (unreachable with a job running today, but there is exactly one quit
        # policy and every path goes through it).
        self.app.action_quit()


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
        # NOTE: not ``_closed``/``_running`` — those are MessagePump internals;
        # shadowing them silently breaks Textual's own message dispatch.
        self._left_screen = False
        # True once the user has touched the material control: a slow AMS read
        # that lands afterwards must never re-pick for them.
        self._material_touched = False
        self._detection_button: RadioButton | None = None
        # Set while the confirm modal owns the run, so a "back" outcome can put
        # the preview (and the workdir handle) back exactly as it was.
        self._handed_result: PrepareResult | None = None

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
            yield Button("Start print…", id="print-button", disabled=True)
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
        self._left_screen = True
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
        if self._left_screen:
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
        elif event.button.id == "print-button":
            self.open_confirm()

    # --- ownership handoff --------------------------------------------------

    def take_result(self) -> PrepareResult | None:
        """Hand the prepared run (and its workdir) to the confirm modal.

        After this the screen no longer deletes the workdir on unmount — the
        modal owns it, and either consumes it (printed/uploaded/declined) or
        hands it back through ``adopt_result``.
        """
        result = self.result
        self._handed_result = result
        self.result = None
        self._workdir = None
        return result

    def adopt_result(self, result: PrepareResult) -> None:
        """Take ownership back (the user backed out of the modal)."""
        self.result = result
        self._workdir = result.state.workdir

    def open_confirm(self) -> None:
        """Preview → confirmation modal (the only path that can start a print)."""
        from bambu_cli.tui.screens.confirm import ConfirmModal

        if self.result is None or not self.result.ok or self._preparing:
            return
        result = self.take_result()
        assert result is not None  # guarded above  # noqa: S101
        self.app.push_screen(ConfirmModal(self._args, self._deps, result.state), self._confirm_closed)

    def _confirm_closed(self, outcome: Any) -> None:
        from bambu_cli.tui.screens.confirm import BACK, PRINTED

        handed, self._handed_result = self._handed_result, None
        if outcome is None:
            # Dismissed without a decision (never happens through our own code
            # paths): treat it as backing out so nothing is destroyed.
            if handed is not None:
                self.adopt_result(handed)
            return
        if outcome.kind == BACK:
            if handed is not None:
                self.adopt_result(handed)
            self.query_one("#prepare-status", Static).update("Back at the preview — nothing has been sent.")
            return
        # The modal consumed the run: the preview no longer describes a file we
        # own, so the print action goes back to disabled.
        self.query_one("#print-button", Button).disabled = True
        self.query_one("#prepare-status", Static).update(outcome.message)
        if outcome.kind == PRINTED:
            self.app.open_monitor()

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
        self.query_one("#print-button", Button).disabled = True
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
        if self._left_screen:
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
            self.query_one("#print-button", Button).disabled = True
            status.update(result.error or "Preparing the model failed.")
            preview.update("")
            return
        self.result = result
        self.query_one("#print-button", Button).disabled = False
        status.update('Ready. Press "Start print…" to confirm.')
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
