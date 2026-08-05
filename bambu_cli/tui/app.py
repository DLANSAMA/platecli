"""The Textual application shell for ``plate tui``.

``PlateApp`` wires the injected ``TuiDeps`` into the screens and owns global
bindings and the stylesheet. It holds no domain logic: the dashboard fetches
through ``TuiDeps.get_status_provider()`` (a ``StatusService`` in production, a
scripted fake in pilot tests), and starting a print is the confirm modal's job
alone (``screens/confirm.py``).

The status fetch never raises (``StatusService`` captures failures into a
``StatusSnapshot(ok=False)``), so a printer error renders inline instead of
crashing the app; process termination never happens here (that lives only in
``cli.py``). Pipeline and job workers catch ``BambuError`` and render it inline,
and ``q`` refuses to quit while an upload/print-start worker is still running.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from textual.app import App
from textual.binding import Binding

from bambu_cli.interactive.core import preflight_problem
from bambu_cli.tui.deps import TuiDeps
from bambu_cli.tui.screens.dashboard import DashboardScreen
from bambu_cli.tui.screens.help import HelpScreen
from bambu_cli.tui.screens.monitor import MonitorScreen
from bambu_cli.tui.screens.prepare import PreflightErrorScreen, PrepareScreen

_CSS_PATH = Path(__file__).with_name("styles.tcss")


class PlateApp(App):
    """Full-screen terminal UI for platecli (dashboard, prepare, confirm, monitor)."""

    CSS_PATH = _CSS_PATH
    TITLE = "platecli"
    SUB_TITLE = "printer dashboard"

    # App-level bindings stay ACTIVE everywhere but are not shown in the Footer:
    # each screen advertises the subset that actually does something there (a
    # footer promising "r Refresh" on a form the user is typing into is worse
    # than no footer at all). The help overlay lists the full set.
    BINDINGS = [
        Binding("q", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("n", "new_print", "New print", show=False),
        Binding("m", "monitor", "Monitor job", show=False),
        Binding("question_mark,f1", "help", "Help", show=False),
    ]

    def __init__(self, args: argparse.Namespace, deps: TuiDeps | None = None) -> None:
        super().__init__()
        self._args = args
        self._deps = deps if deps is not None else TuiDeps()
        # True while an upload / print-start worker is running: quitting then
        # would abandon a physical action mid-flight, so the quit binding
        # refuses instead (the modal re-enables it when the worker returns).
        self._job_in_flight = False

    def on_mount(self) -> None:
        self.push_screen(
            DashboardScreen(self._args, self._deps.get_status_provider()),
        )

    def action_new_print(self) -> None:
        """Open the prepare flow — or explain why the setup cannot print yet.

        The preflight is the wizard's step-0 checks minus the prompts (shared
        ``interactive.core.preflight_problem``): an unconfigured printer, an
        unusable OrcaSlicer, or a missing profiles directory sends the user to
        ``plate setup`` rather than into a form whose answers we cannot act on.
        """
        if isinstance(self.screen, (PrepareScreen, PreflightErrorScreen)):
            return
        problem = preflight_problem(self._args)
        if problem:
            self.push_screen(PreflightErrorScreen(problem))
            return
        self.push_screen(PrepareScreen(self._args, self._deps))

    def set_job_in_flight(self, value: bool) -> None:
        self._job_in_flight = bool(value)

    @property
    def job_in_flight(self) -> bool:
        return self._job_in_flight

    def action_quit(self) -> None:
        """Quit, unless a job worker is mid-flight (then say so and stay)."""
        if self._job_in_flight:
            self.notify("Upload in progress — wait for it to finish.", severity="warning")
            return
        self.exit()

    def action_help(self) -> None:
        """Open the key reference (never stacks a second copy).

        Refused while the top screen is busy with a job worker: an overlay
        pushed over the confirm modal would take the top-of-stack away from it,
        and a screen that is not on top cannot dismiss itself with its result.
        The modal repairs that case anyway (see ``ConfirmModal._job_done``), but
        the honest fix is not to cover a screen that is mid-transaction.
        """
        if isinstance(self.screen, HelpScreen):
            return
        if getattr(self.screen, "busy_with_job", False):
            self.notify("Finishing the upload — try help again in a moment.", severity="warning")
            return
        self.push_screen(HelpScreen())

    def action_monitor(self) -> None:
        """Watch the running job. Leaving the monitor never stops the print."""
        self.open_monitor()

    def open_monitor(self) -> None:
        if isinstance(self.screen, MonitorScreen):
            return
        self.push_screen(MonitorScreen(self._args, self._deps))

    def action_refresh(self) -> None:
        # Delegate to the active screen when it can refresh (the dashboard).
        screen: Any = self.screen
        refresh = getattr(screen, "refresh_status", None)
        if callable(refresh):
            refresh()


def run_app(args: argparse.Namespace, deps: TuiDeps | None = None) -> None:
    """Construct and run the Textual app (blocks until the user quits).

    Kept as a plain function so ``entry.cmd_tui`` imports Textual only when it
    actually launches the UI, and so tests can drive ``PlateApp`` directly via
    ``run_test()`` without going through this blocking call.
    """
    PlateApp(args, deps).run()
