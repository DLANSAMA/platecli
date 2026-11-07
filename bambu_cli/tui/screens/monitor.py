"""Live job monitor: poll printer status until the print reaches a terminal state.

The poll loop runs in a Textual *thread* worker because ``printer.status()``
blocks on a ``threading.Event``; it is cancelled through a ``threading.Event``
that ``on_unmount`` sets, so no worker (and no timer) outlives the screen — a
leaked worker fails the ``-W error::ResourceWarning`` CI mode. The poll interval
is injected (``TuiDeps.poll_interval``) so pilot tests never sleep.

The screen holds no domain logic: whether a state is terminal is
``MonitorService.is_terminal`` (which uses the very set
``protocols.mqtt.monitor_status`` uses), and every rendered value comes from
``services.job_view`` / ``services.temp_view``, the dashboard's own helpers, so
the two screens describe a print in the same words.

**Detaching is not cancelling.** ``Esc`` pops this screen and stops *watching*;
it never sends a stop/pause command, so a print keeps running exactly as it does
when the CLI monitor is interrupted. It returns to whatever pushed the monitor —
the dashboard via ``m``, or the prepare screen after a print was started there.
"""

from __future__ import annotations

import argparse
import threading
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from bambu_cli.tui.services import HEATERS, StatusSnapshot, TempHistory, job_view, temp_view
from bambu_cli.tui.widgets.job_hero import JobHero
from bambu_cli.tui.widgets.temps import TempCard
from bambu_cli.tui.widgets.topbar import TopBar


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
        self._title = title or "Watching the current job live."
        self._service = deps.get_monitor_service()
        self._interval = float(deps.get_poll_interval())
        self._stop = threading.Event()
        self._left_screen = False
        self._history = TempHistory()
        self.clock = datetime.now
        self.polls = 0
        self.finished = False

    def compose(self) -> ComposeResult:
        yield TopBar("Monitor", id="topbar")
        with VerticalScroll(id="monitor-body"):
            yield Static(self._title, id="monitor-title", markup=False)
            yield JobHero(id="job-progress")
            with Horizontal(id="monitor-temps"):
                for key, title, *_ in HEATERS:
                    yield TempCard(title, id=f"monitor-temp-{key}")
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
        self._history.add(snapshot)
        view = job_view(snapshot, self.clock())
        self.query_one("#topbar", TopBar).show_status(view.badge)
        self.query_one("#job-progress", JobHero).show(view)
        for key, _title, current_key, target_key, scale in HEATERS:
            card = self.query_one(f"#monitor-temp-{key}", TempCard)
            card.show(temp_view(snapshot, current_key, target_key), self._history.series(key), scale)
        hint = self.query_one("#monitor-hint", Static)
        if terminal:
            self.finished = True
            hint.update(f"{view.badge.label}: this job is over, so there is nothing left to watch · Esc goes back")
        elif snapshot.ok:
            hint.update("Esc goes back — the print keeps going; nothing here can stop it")
        else:
            hint.update("Status unavailable — still watching · Esc goes back")

    # --- events -------------------------------------------------------------

    def action_detach(self) -> None:
        """Leave the monitor. Detaching never cancels the print or the job."""
        self.app.pop_screen()
