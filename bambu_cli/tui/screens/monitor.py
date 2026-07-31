"""Live job monitor: poll printer status until the print reaches a terminal state.

The poll loop runs in a Textual *thread* worker because ``printer.status()``
blocks on a ``threading.Event``; it is cancelled through a ``threading.Event``
that ``on_unmount`` sets, so no worker (and no timer) outlives the screen — a
leaked worker fails the ``-W error::ResourceWarning`` CI mode. The poll interval
is injected (``TuiDeps.poll_interval``) so pilot tests never sleep.

The screen holds no domain logic: whether a state is terminal is
``MonitorService.is_terminal`` (which uses the very set
``protocols.mqtt.monitor_status`` uses), and every rendered value comes from
``services.job_progress_lines``.

**Detaching is not cancelling.** ``Esc`` pops this screen and stops *watching*;
it never sends a stop/pause command, so a print keeps running exactly as it does
when the CLI monitor is interrupted. It returns to whatever pushed the monitor —
the dashboard via ``m``, or the prepare screen after a print was started there.
"""

from __future__ import annotations

import argparse
import threading
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from bambu_cli.tui.services import StatusSnapshot
from bambu_cli.tui.widgets.job_progress import JobProgress


class MonitorScreen(Screen):
    """Follows a running print until FINISH/FAILED/STOP/IDLE."""

    BINDINGS = [
        ("escape", "detach", "Back (keeps printing)"),
        ("question_mark", "app.help", "Help"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(
        self,
        args: argparse.Namespace,
        deps: Any,
        *,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._args = args
        self._deps = deps
        self._title = title or "Watching the print — Esc goes back (the print keeps going)"
        self._service = deps.get_monitor_service()
        self._interval = float(deps.get_poll_interval())
        self._stop = threading.Event()
        self._left_screen = False
        self.polls = 0
        self.finished = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="monitor-body"):
            yield Static(self._title, id="monitor-title", markup=False)
            yield JobProgress(id="job-progress")
            yield Static("", id="monitor-hint", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(
            self._poll_worker,
            thread=True,
            exclusive=True,
            group="job-monitor",
            exit_on_error=False,
        )

    def on_unmount(self) -> None:
        # Stop watching; never stop the print.
        self._left_screen = True
        self._stop.set()

    # --- polling ------------------------------------------------------------

    def _poll_worker(self) -> None:
        while not self._stop.is_set():
            snapshot = self._service.poll(self._args)
            terminal = self._service.is_terminal(snapshot)
            self.app.call_from_thread(self._apply, snapshot, terminal)
            if terminal:
                return
            # Interruptible wait: unmount sets the event and the loop ends now
            # rather than after the full interval.
            if self._stop.wait(self._interval):
                return

    def _apply(self, snapshot: StatusSnapshot, terminal: bool) -> None:
        self.polls += 1
        if self._left_screen:
            return
        self.query_one("#job-progress", JobProgress).update_snapshot(snapshot)
        hint = self.query_one("#monitor-hint", Static)
        if terminal:
            self.finished = True
            hint.update(f"Reached terminal state: {snapshot.gcode_state} · Esc goes back")
        elif snapshot.ok:
            hint.update("Esc goes back — the print keeps going; nothing here can stop it")
        else:
            hint.update("Status unavailable — still watching · Esc goes back")

    # --- events -------------------------------------------------------------

    def action_detach(self) -> None:
        """Leave the monitor. Detaching never cancels the print or the job."""
        self.app.pop_screen()
