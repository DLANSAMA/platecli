"""Textual pilot tests for the read-only dashboard (bambu_cli.tui).

Driven headlessly via ``PlateApp(...).run_test()`` with an injected ``TuiDeps``
carrying scripted fakes — no real MQTT, no sleeps. One case builds a real
``StatusService`` against the ``--sim`` transport to prove the end-to-end path.
These run headless on Linux/macOS/Windows CI (no PTY assumptions).
"""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

pytest.importorskip("textual")

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.context import RuntimeContext, Settings  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.services import StatusService, StatusSnapshot  # noqa: E402
from bambu_cli.tui.widgets.ams_panel import AmsPanel  # noqa: E402
from bambu_cli.tui.widgets.status_panel import StatusPanel  # noqa: E402

# A representative IDLE snapshot with a populated AMS, shaped like parse_ams output.
_IDLE_SNAPSHOT = StatusSnapshot(
    ok=True,
    raw={
        "gcode_state": "IDLE",
        "mc_percent": 0,
        "nozzle_temper": 25,
        "nozzle_target_temper": 0,
        "bed_temper": 25,
        "bed_target_temper": 0,
        "wifi_signal": "-42dBm",
    },
    ams={
        "active_tray": 0,
        "units": [
            {
                "id": 0,
                "humidity": 5,
                "temp": 26.0,
                "trays": [
                    {"slot": 0, "type": "PLA", "color": "F2F2F2", "remain": 90, "empty": False, "active": True},
                    {"slot": 1, "type": "PETG", "color": "0A0AC8", "remain": 60, "empty": False, "active": False},
                    {"slot": 2, "type": None, "color": None, "remain": None, "empty": True, "active": False},
                    {"slot": 3, "type": "TPU", "color": "000000", "remain": 45, "empty": False, "active": False},
                ],
            }
        ],
    },
)


class FakeStatusProvider:
    """Returns scripted snapshots; records how many fetches happened."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    def fetch(self, args):
        self.calls += 1
        idx = min(self.calls - 1, len(self._snapshots) - 1)
        return self._snapshots[idx]


def _args(**kwargs):
    base = {"cmd": "tui", "sim": False, "json": False, "verbose": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _render_to_text(renderable) -> str:
    """Render a Rich renderable (or str) to plain text via a wide Console."""
    from rich.console import Console

    if renderable is None:
        return ""
    if isinstance(renderable, str):
        return renderable
    console = Console(width=200)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _all_text(app):
    """Concatenate the rendered text of the two dashboard panels."""
    parts = []
    for widget_type in (StatusPanel, AmsPanel):
        for widget in app.query(widget_type):
            parts.append(_render_to_text(getattr(widget, "renderable", None)))
    return "\n".join(parts)


@pytest.fixture(autouse=True)
def _reset_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)


async def test_dashboard_renders_status_and_ams():
    provider = FakeStatusProvider([_IDLE_SNAPSHOT])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    assert "IDLE" in text
    assert "PLA" in text
    assert "PETG" in text
    assert "TPU" in text
    assert provider.calls >= 1


async def test_r_key_triggers_a_refresh():
    provider = FakeStatusProvider([_IDLE_SNAPSHOT])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        before = provider.calls
        await pilot.press("r")
        await pilot.pause()
    assert provider.calls > before


async def test_q_key_quits():
    provider = FakeStatusProvider([_IDLE_SNAPSHOT])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    # The app is no longer running after quit.
    assert app.is_running is False


async def test_unreachable_printer_renders_error_state():
    bad = StatusSnapshot(ok=False, error="Printer unreachable (timeout).")
    provider = FakeStatusProvider([bad])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    assert "unreachable" in text.lower()


async def test_dashboard_against_sim_transport():
    """End-to-end sim path: real StatusService + simulation-mode printer."""
    _context.set_current(
        RuntimeContext(
            settings=Settings.from_config(
                {
                    "printer_ip": "127.0.0.1",
                    "serial": "SIMSERIAL",
                    "access_code": "SIMCODE",
                }
            ),
            simulation=True,
        )
    )
    deps = TuiDeps(status_provider=StatusService())
    app = PlateApp(_args(sim=True), deps)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    # The sim MQTT payload reports IDLE with a PLA/PETG/TPU AMS.
    assert "IDLE" in text
    assert "PLA" in text
