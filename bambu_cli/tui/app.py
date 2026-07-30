"""The Textual application shell for ``plate tui``.

``PlateApp`` wires the injected ``TuiDeps`` into the dashboard screen and owns
global bindings and the stylesheet. It holds no domain logic: the dashboard
fetches through ``TuiDeps.get_status_provider()`` (a ``StatusService`` in
production, a scripted fake in pilot tests).

The status fetch never raises (``StatusService`` captures failures into a
``StatusSnapshot(ok=False)``), so a printer error renders inline instead of
crashing the app; ``sys.exit`` never appears here (it lives only in ``cli.py``).
Later phases will catch ``BambuError`` from pipeline workers and surface it as a
notification.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from textual.app import App

from bambu_cli.tui.deps import TuiDeps
from bambu_cli.tui.screens.dashboard import DashboardScreen

_CSS_PATH = Path(__file__).with_name("styles.tcss")


class PlateApp(App):
    """Full-screen terminal UI for platecli (Phase 1: read-only dashboard)."""

    CSS_PATH = _CSS_PATH
    TITLE = "platecli"
    SUB_TITLE = "printer dashboard"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, args: argparse.Namespace, deps: TuiDeps | None = None) -> None:
        super().__init__()
        self._args = args
        self._deps = deps if deps is not None else TuiDeps()

    def on_mount(self) -> None:
        self.push_screen(
            DashboardScreen(self._args, self._deps.get_status_provider()),
        )

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
