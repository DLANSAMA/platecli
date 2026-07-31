"""The print-confirmation modal — the TUI's single physical-action gate.

This module is the **only** place in ``bambu_cli/tui`` that may build a job
namespace with ``confirm=True``; every other screen stops at a prepared file.
Grep ``confirm=True`` under ``bambu_cli/tui`` and exactly one call site answers:
the "Start print" button below. Upload-only takes the same path with
``confirm=False`` (the wizard's semantics: the file is uploaded, the print is
not started).

Workdir ownership (documented once, here, because it spans two screens):

* ``PrepareScreen`` creates the temp workdir and owns it until the user asks to
  print. Opening this modal *transfers* ownership (``PrepareScreen.take_result``
  clears its handle) so the sliced file survives the prepare screen's unmount.
* While this modal is open it is the sole owner. Every exit decides:
  - **Start print / Upload only** — after the job succeeds the file lives on the
    printer, so the workdir is deleted, exactly as the wizard deletes it after
    ``cmd_job`` returns.
  - **Cancel** — the wizard's decline path: ``preserve_printable`` moves the
    sliced file out of the temp dir and the "Nothing sent. Sliced file kept at
    …" message names it. Nothing is silently thrown away.
  - **Esc / back** — no decision was made, so ownership goes *back* to the
    prepare screen and the preview is still there. This is the only exit that
    neither prints nor consumes the file.
  - **App exit with the modal open** — the wizard's cancellation path deletes
    its workdir in a ``finally``; this does the same on unmount when it still
    owns the run.

A failed job keeps ownership here so the user can retry or cancel deliberately.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from bambu_cli.errors import BambuError
from bambu_cli.interactive.core import build_job_namespace, cleanup_workdir, decline_message

# Outcome kinds handed back to the prepare screen.
PRINTED = "printed"
UPLOADED = "uploaded"
DECLINED = "declined"
BACK = "back"

_PRINTED_MESSAGE = "Printing. Watching it live — leaving the monitor never stops the print."
_UPLOADED_MESSAGE = "Uploaded, not started. Start it from the printer's screen or with 'plate print'."


class ConfirmOutcome:
    """What the modal decided, handed to the prepare screen's callback."""

    def __init__(self, kind: str, message: str = "") -> None:
        self.kind = kind
        self.message = message


class ConfirmModal(ModalScreen[ConfirmOutcome]):
    """Start print / Upload only / Cancel for an already-prepared file."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("question_mark", "app.help", "Help"),
    ]

    def __init__(self, args: argparse.Namespace, deps: Any, state: Any) -> None:
        super().__init__()
        self._args = args
        self._deps = deps
        self._state = state
        self._owns = True
        self._job_running = False

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Send this to the printer?", id="confirm-title"),
            Static(str(self._state.printable_path or ""), id="confirm-path", markup=False),
            Horizontal(
                Button("Start print", id="confirm-print", variant="primary"),
                Button("Upload only", id="confirm-upload"),
                Button("Cancel", id="confirm-cancel"),
                id="confirm-buttons",
            ),
            Static("", id="confirm-status", markup=False),
            id="confirm-body",
        )
        yield Footer()

    def on_unmount(self) -> None:
        # Still owning the run at unmount means the app is going away mid-decision.
        if self._owns:
            self._owns = False
            cleanup_workdir(self._state)

    # --- events -------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-print":
            # The one and only place the TUI starts a print.
            self._start_job(confirm=True)
        elif event.button.id == "confirm-upload":
            self._start_job(confirm=False)
        elif event.button.id == "confirm-cancel":
            self.action_cancel()

    def action_back(self) -> None:
        """Leave without deciding: the prepare screen takes the file back."""
        if self._job_running:
            return
        self._owns = False
        self.dismiss(ConfirmOutcome(BACK))

    def action_cancel(self) -> None:
        """Decline: keep the sliced file the way the wizard keeps it."""
        if self._job_running:
            return
        message = decline_message(self._state)
        cleanup_workdir(self._state)
        self._owns = False
        self.dismiss(ConfirmOutcome(DECLINED, message))

    # --- the job worker -----------------------------------------------------

    def _start_job(self, *, confirm: bool) -> None:
        if self._job_running:
            return
        self._job_running = True
        self._set_app_job_in_flight(True)
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#confirm-status", Static).update(
            "Starting the print…" if confirm else "Uploading…",
        )
        self.run_worker(
            partial(self._job_worker, confirm),
            thread=True,
            exclusive=True,
            group="job",
            exit_on_error=False,
        )

    def _job_worker(self, confirm: bool) -> None:
        error: str | None = None
        # cmd_job logs to the console; inside a full-screen app that would draw
        # over the UI, so its output is captured here and a short tail of it is
        # appended to the message when the job fails (on success it is dropped —
        # the UI already says what happened).
        # NOTE: redirect_stdout/redirect_stderr are PROCESS-global, not
        # thread-local: while this worker runs, anything any other thread prints
        # lands in this buffer too. That is harmless today because no other
        # worker prints; a future one that does must not rely on stdout.
        buffer = io.StringIO()
        try:
            job_ns = build_job_namespace(self._state, self._args, confirm=confirm)
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                self._deps.get_steps().get_job()(job_ns)
        except BambuError as exc:
            error = str(exc) or "The job failed."
        except Exception as exc:  # noqa: BLE001 -- a worker must never crash the UI
            error = str(exc) or f"The job failed ({type(exc).__name__})."
        if error is not None:
            error = _with_output_tail(error, buffer.getvalue())
        self.app.call_from_thread(self._job_done, confirm, error)

    @property
    def busy_with_job(self) -> bool:
        """True while the job worker runs — nothing may be pushed over us then.

        Read by ``PlateApp.action_help``: a modal that is not top-of-stack
        cannot deliver its outcome, so overlays are refused for this window.
        """
        return self._job_running

    def _ensure_on_top(self) -> None:
        """Pop anything stacked above this modal so ``dismiss`` is accepted.

        Textual ignores ``dismiss()`` from a screen that is not on top, and the
        worker's completion callback would then be swallowed — leaving the modal
        frozen with its buttons disabled and the run's outcome lost. Runs on the
        main thread (``call_from_thread``), so touching the stack is safe.
        """
        app = self.app
        for _ in range(len(app.screen_stack)):
            if app.screen is self or self not in app.screen_stack:
                return
            app.pop_screen()

    def _job_done(self, confirm: bool, error: str | None) -> None:
        # An overlay (today: the help screen) may have been pushed over us while
        # the worker ran; take the top back before reporting anything.
        self._ensure_on_top()
        self._job_running = False
        self._set_app_job_in_flight(False)
        if error is not None:
            # Keep ownership: the user can retry or cancel deliberately.
            if self.is_mounted:
                for button in self.query(Button):
                    button.disabled = False
                self.query_one("#confirm-status", Static).update(error)
            return
        # The file is on the printer now; the temp copy has done its job.
        cleanup_workdir(self._state)
        self._owns = False
        self.dismiss(
            ConfirmOutcome(PRINTED if confirm else UPLOADED, _PRINTED_MESSAGE if confirm else _UPLOADED_MESSAGE)
        )

    def _set_app_job_in_flight(self, value: bool) -> None:
        setter = getattr(self.app, "set_job_in_flight", None)
        if callable(setter):
            setter(value)


def _with_output_tail(message: str, captured: str, *, lines: int = 3, width: int = 200) -> str:
    """Append the last few non-empty lines the job printed to a failure message.

    Bounded on both axes (at most ``lines`` lines, each truncated to ``width``)
    so a chatty failure cannot flood the modal. The result is rendered by a
    ``Static(markup=False)``, so no escaping is needed.
    """
    tail = [line.strip() for line in (captured or "").splitlines() if line.strip()]
    if not tail:
        return message
    kept = [line[:width] for line in tail[-lines:]]
    return message + "\n" + "\n".join(kept)
