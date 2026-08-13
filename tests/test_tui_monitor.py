"""Pilot tests for the live job monitor + the full sim end-to-end flow.

The poll interval is injected (0.01 s) so nothing sleeps; statuses are scripted
snapshots, never a real printer. The end-to-end case walks the whole plan §7
acceptance path: dashboard → n → source/presets → prepare → confirm → monitor →
FINISH.
"""

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.confirm import ConfirmModal  # noqa: E402
from bambu_cli.tui.screens.dashboard import DashboardScreen  # noqa: E402
from bambu_cli.tui.screens.monitor import MonitorScreen  # noqa: E402
from bambu_cli.tui.screens.prepare import PrepareScreen  # noqa: E402
from bambu_cli.tui.services import MonitorService, StatusSnapshot  # noqa: E402
from bambu_cli.tui.widgets.job_progress import JobProgress  # noqa: E402
from tests.tui_text import widget_text  # noqa: E402

def _snap(state, percent, layer=0, total=100, remaining=0):
    return StatusSnapshot(
        ok=True,
        raw={
            "gcode_state": state,
            "mc_percent": percent,
            "layer_num": layer,
            "total_layer_num": total,
            "mc_remaining_time": remaining,
            "nozzle_temper": 220,
            "bed_temper": 60,
        },
        ams={"units": []},
    )

class ScriptedStatus:
    """Replays scripted snapshots and counts every fetch."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    def fetch(self, args):
        self.calls += 1
        return self._snapshots[min(self.calls - 1, len(self._snapshots) - 1)]

class Recorder:
    def __init__(self, return_value=None, raises=None):
        self.calls = []
        self.return_value = return_value
        self.raises = raises

    def __call__(self, ns=None, **kwargs):
        self.calls.append(ns)
        if self.raises is not None:
            raise self.raises
        return self.return_value

@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Never let a declined print drop its preserved file in the repo.

    ``preserve_printable`` relocates the sliced file into the *current working
    directory* by design; tests that decline must therefore own their cwd.
    """
    monkeypatch.chdir(tmp_path)

@pytest.fixture(autouse=True)
def _reset_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)

def _install_ready_settings(tmp_path, **overrides):
    from dataclasses import replace

    from bambu_cli.context import RuntimeContext, Settings

    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(orca, 0o755)
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    settings = Settings(
        printer_ip="192.168.1.23",
        printer_model="P1S",
        nozzle_size="0.4",
        orca_slicer=str(orca),
        profiles_dir=str(profiles),
    )
    settings = replace(settings, **overrides)
    _context.set_current(RuntimeContext(settings=settings, simulation=bool(overrides.get("_sim"))))
    return settings

def _args(**kwargs):
    base = {"cmd": "tui", "sim": False, "json": False, "verbose": False}
    base.update(kwargs)
    return argparse.Namespace(**base)

def _sliced_3mf(path, name="cube.gcode.3mf"):
    p = Path(path) / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config>'
            '<metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/>'
            "</config>",
        )
    return str(p)

def _slicer_into_workdir(ns=None, **kwargs):
    return _sliced_3mf(ns.output)

async def _settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

async def _pump(pilot, times=10, delay=0.02):
    """Give the poll worker real (short) wall time without waiting on workers."""
    import asyncio

    for _ in range(times):
        await pilot.pause()
        await asyncio.sleep(delay)

async def _wait_until(condition, pilot, timeout=5.0):
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "condition never became true"
        await pilot.pause()
        await asyncio.sleep(0.01)

def _text(widget) -> str:
    return widget_text(widget)

# --- MonitorService unit level (no pilot) ----------------------------------

def test_monitor_terminal_states_match_the_cli_monitor():
    from bambu_cli.protocols.mqtt import TERMINAL_GCODE_STATES
    from bambu_cli.tui.services import terminal_gcode_states

    assert terminal_gcode_states() is TERMINAL_GCODE_STATES
    assert set(TERMINAL_GCODE_STATES) == {"FINISH", "FAILED", "STOP", "IDLE"}

def test_monitor_service_is_terminal():
    service = MonitorService(ScriptedStatus([_snap("RUNNING", 10)]))
    assert service.is_terminal(_snap("RUNNING", 10)) is False
    assert service.is_terminal(_snap("FINISH", 100)) is True
    assert service.is_terminal(_snap("FAILED", 50)) is True
    # An unreadable status is never terminal: we keep watching instead of
    # declaring a print finished because MQTT hiccuped.
    assert service.is_terminal(StatusSnapshot(ok=False, error="timeout")) is False

def test_job_progress_lines_and_formatting():
    from bambu_cli.tui.services import format_remaining, job_progress_lines, progress_percent

    rows = dict(job_progress_lines(_snap("RUNNING", 42, layer=21, total=100, remaining=95)))
    assert rows["State"] == "RUNNING"
    assert rows["Progress"] == "42%"
    assert rows["Layer"] == "21 / 100"
    assert rows["Remaining"] == "1h 35m"
    assert format_remaining(None) == "—"
    assert format_remaining(-1) == "—"
    assert format_remaining(7) == "7m"
    assert progress_percent(_snap("RUNNING", 200)) == 100
    assert progress_percent(StatusSnapshot(ok=False, error="x")) == 0
    assert dict(job_progress_lines(StatusSnapshot(ok=False, error="boom")))["Status"] == "boom"

# --- pilot -----------------------------------------------------------------

def _deps(monitor_provider, **kwargs):
    """Deps whose MONITOR polls ``monitor_provider``.

    The dashboard keeps its own provider so the monitor's poll count is not
    polluted by the dashboard's periodic refresh.
    """
    return TuiDeps(
        status_provider=ScriptedStatus([_snap("IDLE", 0)]),
        monitor_service=MonitorService(monitor_provider),
        poll_interval=0.01,
        **kwargs,
    )

async def test_monitor_progresses_and_stops_on_terminal_state(tmp_path):
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([_snap("RUNNING", 10, layer=5), _snap("RUNNING", 60, layer=60), _snap("FINISH", 100)])
    app = PlateApp(_args(), _deps(provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MonitorScreen)
        # Deliberately NOT waiting on the worker here: a monitor that never
        # stops polling must fail this test, not hang it.
        await _wait_until(lambda: screen.finished, pilot)
        text = _text(screen.query_one("#job-progress", JobProgress))
        assert "FINISH" in text
        assert "100%" in text
        assert "Reached terminal state: FINISH" in _text(screen.query_one("#monitor-hint", Static))
        # Polling STOPPED at the terminal state: no further fetches happen even
        # after many more poll intervals' worth of wall time.
        settled = provider.calls
        await _pump(pilot)
        assert provider.calls == settled
        # And it really did poll three times (RUNNING, RUNNING, FINISH).
        assert settled == 3

async def test_escape_detaches_without_stopping_anything(tmp_path):
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([_snap("RUNNING", 10)])
    stop = Recorder()
    app = PlateApp(_args(), _deps(provider, steps=GoSteps(job=stop)))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await pilot.pause()
        monitor = app.screen
        assert isinstance(monitor, MonitorScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        # The poll loop is cancelled...
        await pilot.app.workers.wait_for_complete()
        detached = provider.calls
        await _pump(pilot)
        assert provider.calls == detached
    # ...and nothing was sent to the printer to stop the print.
    assert stop.calls == []

async def test_monitor_survives_an_unreadable_status(tmp_path):
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([StatusSnapshot(ok=False, error="Printer unreachable (timeout)."), _snap("FINISH", 100)])
    app = PlateApp(_args(), _deps(provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await pilot.pause()
        screen = app.screen
        # It kept polling past the unreadable status instead of calling the job
        # finished, and only stopped once a real terminal state arrived.
        await _wait_until(lambda: screen.finished, pilot)
        assert provider.calls == 2

async def test_m_does_not_stack_monitor_screens(tmp_path):
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([_snap("FINISH", 100)])
    app = PlateApp(_args(), _deps(provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await _settle(pilot)
        await pilot.press("m")
        await _settle(pilot)
        assert sum(isinstance(s, MonitorScreen) for s in app.screen_stack) == 1

async def test_full_sim_end_to_end_dashboard_to_finish(tmp_path):
    """Plan §7 acceptance: dashboard → n → presets → prepare → confirm → FINISH."""
    _install_ready_settings(tmp_path)
    model = tmp_path / "cube.stl"
    model.write_text("solid cube\nendsolid cube\n", encoding="utf-8")

    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=job)
    provider = ScriptedStatus([_snap("RUNNING", 5, layer=1), _snap("FINISH", 100, layer=100)])
    deps = TuiDeps(
        status_provider=provider,
        steps=steps,
        ams_detector=lambda args: "PLA",
        poll_interval=0.01,
    )

    app = PlateApp(_args(sim=True), deps)
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert isinstance(app.screen, DashboardScreen)

        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        assert isinstance(prepare, PrepareScreen)
        assert prepare.selected_material() == "PLA"  # AMS detection drove it
        prepare.query_one("#quality-fine").value = True
        prepare.query_one("#supports-check").value = True
        prepare.query_one("#source-input", Input).value = str(model)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        assert "cube.stl" in _text(prepare.query_one("#preview", Static))

        prepare.open_confirm()
        await _settle(pilot)
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        modal.query_one("#confirm-print").press()
        await _settle(pilot)

        monitor = app.screen
        assert isinstance(monitor, MonitorScreen)
        assert monitor.finished is True
        assert "FINISH" in _text(monitor.query_one("#job-progress", JobProgress))

        await pilot.press("escape")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)

    # One job, started deliberately, on the sliced file, carrying --sim through.
    assert len(job.calls) == 1
    assert job.calls[0].confirm is True
    assert job.calls[0].sim is True
    assert job.calls[0].source.endswith(".gcode.3mf")
    # The temp workdir did not survive the run.
    assert not os.path.exists(os.path.dirname(job.calls[0].source))
