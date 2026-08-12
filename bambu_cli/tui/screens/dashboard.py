"""Read-only printer dashboard: status panel + AMS panel, live refresh.

The blocking ``StatusService.fetch`` (``printer.status()`` waits on a
``threading.Event``) runs in a Textual *thread* worker so the UI never freezes;
its result is applied to the widgets on the main thread via
``App.call_from_thread`` (widget updates must not run off-thread). The service
holds one MQTT session for the process, so a refresh does not open a new TLS
connection. A refresh fires on ``r`` and on a 10 s interval that is only armed
while this screen is active (disarmed on suspend, re-armed on resume) and
cancelled on unmount — a leaked timer would trip the ``-W error::ResourceWarning``
CI mode.

A failed fetch is an ordinary ``StatusSnapshot(ok=False)`` value: the panels
render an inline "unreachable" state and the app keeps running.
"""

from __future__ import annotations

import argparse
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from bambu_cli.tui.services import StatusSnapshot
from bambu_cli.tui.widgets.ams_panel import AmsPanel
from bambu_cli.tui.widgets.job_progress import JobProgress
from bambu_cli.tui.widgets.status_panel import StatusPanel

_REFRESH_INTERVAL = 10.0
# States where a progress bar means something. Deliberately not derived from
# TERMINAL_GCODE_STATES: "not terminal" includes states like PREPARE where a
# percentage is meaningless.
_ACTIVE_STATES = frozenset({"RUNNING", "PAUSE"})


class DashboardScreen(Screen):
    """Default screen: printer status (left) and AMS trays (right)."""

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("n", "app.new_print", "New print"),
        ("m", "app.monitor", "Monitor"),
        ("question_mark", "app.help", "Help"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, args: argparse.Namespace, status_provider: Any) -> None:
        super().__init__()
        self._args = args
        self._status_provider = status_provider
        self._timer: Any = None
        self._fetching = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="dashboard-body"):
            yield StatusPanel(id="status-panel")
            yield AmsPanel(id="ams-panel")
        # The bar the monitor already uses, surfaced here while a job is live.
        # Hidden at rest: an idle printer has no progress to draw, and a 0%%
        # bar sitting on the dashboard reads as a stalled print.
        yield JobProgress(id="dash-progress", compact=True)
        yield Static("", id="dashboard-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._arm_timer()
        self.refresh_status()

    def _arm_timer(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(_REFRESH_INTERVAL, self.refresh_status)

    def _disarm_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_screen_resume(self) -> None:
        self._arm_timer()

    def on_screen_suspend(self) -> None:
        self._disarm_timer()

    def on_unmount(self) -> None:
        # Cancel the interval so no timer outlives the screen (ResourceWarning).
        self._disarm_timer()

    # --- refresh plumbing ---------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_status()

    def action_quit(self) -> None:
        # Route through the app so the in-flight-job guard applies here too.
        self.app.action_quit()

    def refresh_status(self) -> None:
        """Kick off a background fetch unless one is already in flight."""
        if self._fetching:
            return
        self._fetching = True
        self.query_one("#dashboard-hint", Static).update("Refreshing…")
        # thread=True: fetch blocks on MQTT; exit_on_error=False keeps a worker
        # exception from tearing down the app (the service already never raises,
        # but this is belt-and-braces for the safety-critical "never crash").
        self.run_worker(
            self._fetch_worker,
            thread=True,
            exclusive=True,
            group="status-fetch",
            exit_on_error=False,
        )

    def _fetch_worker(self) -> None:
        snapshot = self._status_provider.fetch(self._args)
        # Apply on the main thread; widget mutations must not run off-thread.
        self.app.call_from_thread(self._apply_snapshot, snapshot)

    def _apply_snapshot(self, snapshot: StatusSnapshot) -> None:
        self._fetching = False
        self.query_one("#status-panel", StatusPanel).update_snapshot(snapshot)
        self.query_one("#ams-panel", AmsPanel).update_snapshot(snapshot)
        progress = self.query_one("#dash-progress", JobProgress)
        running = snapshot.ok and str(snapshot.raw.get("gcode_state", "")).upper() in _ACTIVE_STATES
        progress.display = running
        if running:
            progress.update_snapshot(snapshot)
        hint = self.query_one("#dashboard-hint", Static)
        if snapshot.ok:
            hint.update("Press r to refresh · q to quit")
        else:
            hint.update("Printer unreachable · press r to retry · q to quit")
