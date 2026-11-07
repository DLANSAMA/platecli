"""Read-only printer dashboard: the job, the heaters and the AMS, live.

The blocking ``StatusService.fetch`` (``printer.status()`` waits on a
``threading.Event``) runs in a Textual *thread* worker so the UI never freezes;
its result is applied to the widgets on the main thread via
``App.call_from_thread`` (widget updates must not run off-thread). The service
holds one MQTT session for the process, so a refresh does not open a new TLS
connection. A refresh fires on ``r`` and on a 10 s interval that is only armed
while this screen is active (disarmed on suspend, re-armed on resume) and
cancelled on unmount — a leaked timer would trip the ``-W error::ResourceWarning``
CI mode.

A failed fetch is an ordinary ``StatusSnapshot(ok=False)`` value: the job
panel says the printer cannot be reached, and the app keeps running. Every word
and number on screen comes from a pure helper in ``tui.services``; the heater
history is a ``TempHistory`` this screen owns, so it starts empty each launch.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer

from bambu_cli.tui.services import HEATERS, StatusSnapshot, TempHistory, ams_view, job_view, temp_view, wifi_text
from bambu_cli.tui.widgets.ams_strip import AmsStrip
from bambu_cli.tui.widgets.job_hero import JobHero
from bambu_cli.tui.widgets.temps import TempCard
from bambu_cli.tui.widgets.topbar import TopBar

_REFRESH_INTERVAL = 10.0


class DashboardScreen(Screen):
    """Default screen: the job, the two heaters, then the AMS trays."""

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
        self._history = TempHistory()
        # Injected by tests that pin the ETA clock; the real screen asks the OS.
        self.clock = datetime.now

    def compose(self) -> ComposeResult:
        yield TopBar("Dashboard", id="topbar")
        with VerticalScroll(id="dashboard-body"):
            yield JobHero(id="job-hero")
            with Horizontal(id="temps"):
                for key, title, *_ in HEATERS:
                    yield TempCard(title, id=f"temp-{key}")
            yield AmsStrip(id="ams-panel")
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
        self.query_one("#job-hero", JobHero).border_subtitle = "refreshing…"
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
        now = self.clock()
        self._history.add(snapshot)
        view = job_view(snapshot, now)
        self.query_one("#topbar", TopBar).show_status(view.badge, wifi_text(snapshot))
        hero = self.query_one("#job-hero", JobHero)
        hero.show(view)
        hero.border_subtitle = Text(f"updated {now:%H:%M:%S}" if snapshot.ok else "press r to retry")
        for key, _title, current_key, target_key, scale in HEATERS:
            card = self.query_one(f"#temp-{key}", TempCard)
            card.show(temp_view(snapshot, current_key, target_key), self._history.series(key), scale)
        self.query_one("#ams-panel", AmsStrip).show(ams_view(snapshot), reachable=snapshot.ok)
