"""Textual pilot tests for the read-only dashboard (bambu_cli.tui).

Driven headlessly via ``PlateApp(...).run_test()`` with an injected ``TuiDeps``
carrying scripted fakes — no real MQTT, no sleeps. One case builds a real
``StatusService`` against the ``--sim`` transport to prove the end-to-end path.
These run headless on Linux/macOS/Windows CI (no PTY assumptions).
"""

from __future__ import annotations

import argparse

import pytest

pytest.importorskip("textual")

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.context import RuntimeContext, Settings  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.services import StatusService, StatusSnapshot  # noqa: E402
from bambu_cli.tui.widgets.ams_panel import AmsPanel  # noqa: E402
from bambu_cli.tui.widgets.status_panel import StatusPanel  # noqa: E402
from tests.tui_text import widget_text  # noqa: E402

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

def _all_text(app):
    """Concatenate the rendered text of the two dashboard panels.

    Textual 8's ``App.query`` does not descend into the active screen;
    query the screen itself.
    """
    parts = []
    screen = app.screen
    for widget_type in (StatusPanel, AmsPanel):
        for widget in screen.query(widget_type):
            parts.append(widget_text(widget))
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

# --- printer-supplied text is data, never Rich markup ----------------------
#
# Every cell in these two panels carries strings the *printer* chose: the name
# of the file it is running, the filament type in a tray. A ``str`` cell in a
# Rich table is markup-parsed, which both eats content ("model [remix].stl"
# renders as "model .stl") and can raise ``MarkupError`` mid-render on a name
# shaped like a closing tag. Passing ``Text`` is what stops both.

def _bracketed_file_snapshot(name):
    return StatusSnapshot(
        ok=True,
        raw={
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "nozzle_temper": 220,
            "bed_temper": 60,
            "gcode_file": name,
        },
        ams={"units": []},
    )

def _tray_type_snapshot(ftype):
    return StatusSnapshot(
        ok=True,
        raw={"gcode_state": "IDLE", "mc_percent": 0},
        ams={
            "active_tray": 0,
            "units": [
                {
                    "id": 0,
                    "trays": [
                        {"slot": 0, "type": ftype, "color": "F2F2F2", "remain": 90, "empty": False, "active": True},
                    ],
                }
            ],
        },
    )

async def test_status_panel_renders_a_bracketed_filename_verbatim():
    """A "[remix]" tag in the running file's name must survive to the screen."""
    provider = FakeStatusProvider([_bracketed_file_snapshot("model [remix].stl")])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    assert "model [remix].stl" in text

async def test_status_panel_survives_a_markup_shaped_filename():
    """A closing-tag shape must not blow the render up (MarkupError)."""
    provider = FakeStatusProvider([_bracketed_file_snapshot("a[/b]c.gcode")])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)  # raises rich.errors.MarkupError against a str cell
    assert "a[/b]c.gcode" in text

async def test_ams_panel_renders_a_bracketed_filament_type_verbatim():
    provider = FakeStatusProvider([_tray_type_snapshot("PLA [matte]")])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    assert "PLA [matte]" in text

async def test_ams_panel_survives_a_markup_shaped_filament_type():
    provider = FakeStatusProvider([_tray_type_snapshot("a[/b]c")])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        text = _all_text(app)
    assert "a[/b]c" in text

async def test_dashboard_timer_disarms_on_suspend_and_unmount():
    """A leaked interval would trip ResourceWarning-as-error in CI."""
    provider = FakeStatusProvider([_IDLE_SNAPSHOT])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        dash = app.screen
        assert dash._timer is not None
        dash.on_screen_suspend()
        assert dash._timer is None
        dash.on_screen_resume()
        assert dash._timer is not None
    assert dash._timer is None

async def test_quit_releases_the_status_provider():
    closed = []

    class ClosingProvider(FakeStatusProvider):
        def close(self):
            closed.append(True)

    provider = ClosingProvider([_IDLE_SNAPSHOT])
    app = PlateApp(_args(), TuiDeps(status_provider=provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert closed
