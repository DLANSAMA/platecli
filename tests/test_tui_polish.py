"""Phase 4: help overlay, footer/binding consistency, 80x24 layout, wiring gaps.

Everything here is fake-driven (no printer, no slicer, no network). The 80x24
cases run the real screens at the smallest terminal the project supports.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

pytest.importorskip("textual")

from textual.widgets import Footer, Input, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.confirm import ConfirmModal  # noqa: E402
from bambu_cli.tui.screens.dashboard import DashboardScreen  # noqa: E402
from bambu_cli.tui.screens.help import HELP_ROWS, HelpScreen, help_text  # noqa: E402
from bambu_cli.tui.screens.monitor import MonitorScreen  # noqa: E402
from bambu_cli.tui.screens.prepare import PreflightErrorScreen, PrepareScreen  # noqa: E402
from bambu_cli.tui.services import StatusSnapshot  # noqa: E402

_SMALL = (80, 24)


def _snap(state="IDLE", percent=0):
    return StatusSnapshot(
        ok=True,
        raw={
            "gcode_state": state,
            "mc_percent": percent,
            "nozzle_temper": 25,
            "bed_temper": 25,
            "layer_num": 0,
            "total_layer_num": 100,
            "mc_remaining_time": 0,
        },
        ams={
            "active_tray": 0,
            "units": [
                {
                    "id": 0,
                    "trays": [
                        {"slot": 0, "type": "PLA", "color": "F2F2F2", "remain": 90, "empty": False, "active": True},
                        {"slot": 1, "type": "PETG", "color": "0A0AC8", "remain": 60, "empty": False, "active": False},
                    ],
                }
            ],
        },
    )


class ScriptedStatus:
    def __init__(self, snapshots=None):
        self._snapshots = list(snapshots) if snapshots else [_snap()]
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
    _context.set_current(RuntimeContext(settings=settings))
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
            '<?xml version="1.0"?><config><metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/></config>',
        )
    return str(p)


def _slicer_into_workdir(ns=None, **kwargs):
    return _sliced_3mf(ns.output)


def _deps(**kwargs):
    kwargs.setdefault("status_provider", ScriptedStatus())
    kwargs.setdefault("ams_detector", lambda args: None)
    kwargs.setdefault("poll_interval", 0.01)
    return TuiDeps(**kwargs)


async def _settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _text(widget) -> str:
    renderable = getattr(widget, "renderable", "")
    if isinstance(renderable, str):
        return renderable
    from rich.console import Console

    console = Console(width=200)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


async def _prepared_modal(pilot, app, source):
    """dashboard -> n -> prepare(source) -> confirm modal (returns the modal)."""
    await pilot.press("n")
    await _settle(pilot)
    prepare = app.screen
    prepare.query_one("#source-input", Input).value = str(source)
    prepare.query_one("#source-input", Input).focus()
    await pilot.press("enter")
    await _settle(pilot)
    assert prepare.result is not None
    prepare.open_confirm()
    await _settle(pilot)
    modal = app.screen
    assert isinstance(modal, ConfirmModal)
    return modal


def _footer_keys(app) -> set[str]:
    """The keys the Footer of the active screen advertises."""
    keys: set[str] = set()
    for _footer in app.screen.query(Footer):
        for active in app.screen.active_bindings.values():
            if active.binding.show:
                keys.add(active.binding.key)
    return keys


# ---------------------------------------------------------------------------
# Help overlay
# ---------------------------------------------------------------------------


def test_help_text_lists_every_section_and_key():
    text = help_text()
    for section, rows in HELP_ROWS:
        assert section in text
        for key, description in rows:
            assert key in text
            assert description in text
    assert "confirm dialog" in text  # the safety model is spelled out


def test_help_rows_only_document_keys_that_are_really_bound():
    """The overlay must not advertise a key nothing answers to."""
    from textual.binding import Binding

    bound: set[str] = set()
    for node in (
        PlateApp,
        DashboardScreen,
        PrepareScreen,
        PreflightErrorScreen,
        ConfirmModal,
        MonitorScreen,
        HelpScreen,
    ):
        for entry in node.BINDINGS:
            key = entry.key if isinstance(entry, Binding) else entry[0]
            bound.update(k.strip() for k in key.split(","))
    # Textual's own always-on quit binding and the widget-level keys the help
    # text mentions for the source box.
    bound.update({"ctrl+q", "enter"})
    bound.update({k for entry in bound for k in str(entry).split(",")})
    documented = {key for _section, rows in HELP_ROWS for key, _desc in rows}
    normalized = {"?": "question_mark"}
    for key in documented:
        assert normalized.get(key, key) in bound, f"help documents unbound key {key!r}"


async def test_question_mark_opens_and_closes_the_help_overlay(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        assert "New print" in _text(app.screen.query_one("#help-keys", Static))
        # A second ? does not stack a second overlay.
        await pilot.press("question_mark")
        await _settle(pilot)
        assert sum(isinstance(s, HelpScreen) for s in app.screen_stack) == 0
        # ... and the dashboard is back underneath.
        assert isinstance(app.screen, DashboardScreen)


async def test_help_closes_with_escape_and_with_q_without_quitting(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        app.action_help()
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert isinstance(app.screen, DashboardScreen)

        app.action_help()
        await _settle(pilot)
        await pilot.press("q")
        await _settle(pilot)
        # q closed the overlay; it did NOT quit the app out from under the user.
        assert app.is_running is True
        assert isinstance(app.screen, DashboardScreen)


async def test_help_is_reachable_from_every_screen(tmp_path):
    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())
    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        # Prepare
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        await pilot.press("f1")
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await _settle(pilot)
        # Confirm modal
        prepare.query_one("#source-input", Input).value = str(stl)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        prepare.open_confirm()
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("question_mark")
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        app.screen.query_one("#confirm-cancel").press()
        await _settle(pilot)
        # Monitor (from the dashboard: on the prepare screen the focused source
        # box eats plain letter keys, which is exactly why its footer omits them)
        await pilot.press("escape")
        await _settle(pilot)
        await pilot.press("m")
        await _settle(pilot)
        assert isinstance(app.screen, MonitorScreen)
        await pilot.press("question_mark")
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)


# ---------------------------------------------------------------------------
# Footer consistency
# ---------------------------------------------------------------------------


async def test_footer_advertises_only_keys_that_work_on_that_screen(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        dashboard_keys = _footer_keys(app)
        assert {"r", "n", "m", "q", "question_mark"} <= dashboard_keys

        await pilot.press("n")
        await _settle(pilot)
        prepare_keys = _footer_keys(app)
        assert "escape" in prepare_keys
        assert "f1" in prepare_keys  # "?" would be typed into the source box
        # The prepare screen must NOT promise q/r/n: the source box eats them.
        assert "q" not in prepare_keys
        assert "r" not in prepare_keys
        assert "n" not in prepare_keys


async def test_monitor_and_preflight_footers(tmp_path):
    settings = _install_ready_settings(tmp_path)
    import shutil

    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await _settle(pilot)
        monitor_keys = _footer_keys(app)
        assert {"escape", "q", "question_mark"} <= monitor_keys
        await pilot.press("escape")
        await _settle(pilot)
        # Preflight error screen
        shutil.rmtree(os.path.join(settings.profiles_dir, "process"))
        await pilot.press("n")
        await _settle(pilot)
        assert isinstance(app.screen, PreflightErrorScreen)
        assert {"escape", "q", "question_mark"} <= _footer_keys(app)


# ---------------------------------------------------------------------------
# 80x24
# ---------------------------------------------------------------------------


async def test_main_flow_at_80x24(tmp_path):
    """Every screen renders and works at the smallest supported terminal."""
    _install_ready_settings(tmp_path)
    deep = tmp_path / ("nested/" * 6)
    deep.mkdir(parents=True)
    model = deep / "a-really-long-model-file-name-for-overflow-checks.stl"
    model.write_text("solid cube\nendsolid cube\n", encoding="utf-8")

    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=job)
    provider = ScriptedStatus([_snap("RUNNING", 50), _snap("FINISH", 100)])
    app = PlateApp(_args(), _deps(steps=steps, status_provider=provider))
    async with app.run_test(size=_SMALL) as pilot:
        await _settle(pilot)
        assert app.size.width == 80 and app.size.height == 24
        assert "RUNNING" in _text(app.screen.query_one("#status-panel"))
        assert "PLA" in _text(app.screen.query_one("#ams-panel"))

        await pilot.press("question_mark")
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        assert app.screen.query_one("#help-body").outer_size.width <= 80
        await pilot.press("escape")
        await _settle(pilot)

        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(model)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        assert prepare.result is not None
        # A long path must wrap inside the panel, never widen the screen.
        assert prepare.query_one("#preview").outer_size.width <= 80
        assert app.screen.container_size.width <= 80

        prepare.open_confirm()
        await _settle(pilot)
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        assert modal.query_one("#confirm-body").outer_size.width <= 80
        assert modal.query_one("#confirm-path").outer_size.width <= 80
        modal.query_one("#confirm-print").press()
        await _settle(pilot)

        monitor = app.screen
        assert isinstance(monitor, MonitorScreen)
        assert monitor.query_one("#job-progress").outer_size.width <= 80
        assert monitor.finished is True

    assert len(job.calls) == 1
    assert job.calls[0].confirm is True


async def test_long_error_text_fits_at_80x24(tmp_path):
    """A very long slicer error wraps inside the prepare panel."""
    from bambu_cli.errors import BambuError

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    boom = BambuError("slicer failed: " + "verbose-diagnostic-token " * 20, exit_code=4)
    steps = GoSteps(download=Recorder(), slice=Recorder(raises=boom))

    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test(size=_SMALL) as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        screen = app.screen
        screen.query_one("#source-input", Input).value = str(stl)
        screen.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        status = screen.query_one("#prepare-status", Static)
        assert "slicer failed" in _text(status)
        assert status.outer_size.width <= 80
        assert app.screen.container_size.width <= 80


# ---------------------------------------------------------------------------
# Wiring gaps (previously uncovered branches)
# ---------------------------------------------------------------------------


def test_tuideps_defaults_build_the_real_collaborators():
    from bambu_cli.interactive.core import GoSteps as CoreGoSteps
    from bambu_cli.interactive.core import read_loaded_ams_material
    from bambu_cli.tui.services import MonitorService, PipelineService, StatusService

    deps = TuiDeps()
    assert isinstance(deps.get_status_provider(), StatusService)
    assert isinstance(deps.get_steps(), CoreGoSteps)
    assert isinstance(deps.get_pipeline(), PipelineService)
    assert isinstance(deps.get_monitor_service(), MonitorService)
    assert deps.get_ams_detector() is read_loaded_ams_material
    assert deps.get_poll_interval() == 3.0


def test_tuideps_injection_beats_every_default():
    sentinel = object()
    deps = TuiDeps(
        status_provider=sentinel,
        steps=sentinel,
        pipeline=sentinel,
        ams_detector=sentinel,
        monitor_service=sentinel,
        poll_interval=0.5,
    )
    assert deps.get_status_provider() is sentinel
    assert deps.get_steps() is sentinel
    assert deps.get_pipeline() is sentinel
    assert deps.get_ams_detector() is sentinel
    assert deps.get_monitor_service() is sentinel
    assert deps.get_poll_interval() == 0.5


def test_run_app_constructs_and_runs_the_app(monkeypatch):
    """``run_app`` is the production entry: it builds PlateApp and runs it."""
    from bambu_cli.tui import app as app_mod

    ran = {}

    def fake_run(self):
        ran["args"] = self._args
        ran["deps"] = self._deps

    monkeypatch.setattr(app_mod.PlateApp, "run", fake_run)
    deps = TuiDeps()
    app_mod.run_app(_args(sim=True), deps)
    assert ran["deps"] is deps
    assert ran["args"].sim is True


async def test_refresh_key_is_a_no_op_on_screens_without_status(tmp_path):
    """`r` delegates to the active screen only when it can refresh."""
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus()
    app = PlateApp(_args(), _deps(status_provider=provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("r")
        await _settle(pilot)
        assert provider.calls >= 2  # dashboard refreshed
        after = provider.calls
        await pilot.press("n")
        await _settle(pilot)
        app.action_refresh()  # prepare screen has no refresh_status
        await _settle(pilot)
        assert provider.calls == after
        # And asking for a new print again from the prepare screen is a no-op.
        app.action_new_print()
        await _settle(pilot)
        assert sum(isinstance(s, PrepareScreen) for s in app.screen_stack) == 1


async def test_second_start_press_cannot_double_submit(tmp_path):
    """Guards on the modal's re-entrant paths (running job blocks everything)."""
    import threading

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_job(ns=None, **kwargs):
        started.set()
        gate.wait(10)

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=slow_job)
    app = PlateApp(_args(), _deps(steps=steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            await pilot.press("n")
            await _settle(pilot)
            prepare = app.screen
            prepare.query_one("#source-input", Input).value = str(stl)
            prepare.query_one("#source-input", Input).focus()
            await pilot.press("enter")
            await _settle(pilot)
            prepare.open_confirm()
            await _settle(pilot)
            modal = app.screen
            modal.query_one("#confirm-print").press()
            while not started.is_set():
                await pilot.pause()
            # Every other decision is refused while the job runs.
            modal._start_job(confirm=False)
            modal.action_cancel()
            modal.action_back()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            gate.set()
            await _settle(pilot)
    finally:
        gate.set()


async def test_unexpected_job_error_is_reported_with_captured_output(tmp_path):
    """A non-BambuError from the job surfaces, with a tail of what it printed."""
    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")

    def noisy_job(ns=None, **kwargs):
        print("uploading to printer")
        print("ftp said: 550 permission denied")
        raise RuntimeError("upload aborted")

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=noisy_job)
    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(stl)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        prepare.open_confirm()
        await _settle(pilot)
        modal = app.screen
        modal.query_one("#confirm-print").press()
        await _settle(pilot)
        message = _text(modal.query_one("#confirm-status", Static))
        assert "upload aborted" in message
        assert "550 permission denied" in message
        modal.query_one("#confirm-cancel").press()
        await _settle(pilot)


def test_output_tail_is_bounded():
    from bambu_cli.tui.screens.confirm import _with_output_tail

    assert _with_output_tail("boom", "") == "boom"
    assert _with_output_tail("boom", "   \n\n") == "boom"
    noisy = "\n".join(f"line {i}" for i in range(50))
    tailed = _with_output_tail("boom", noisy)
    assert tailed.splitlines()[0] == "boom"
    assert len(tailed.splitlines()) == 4  # message + 3 kept lines
    assert tailed.endswith("line 49")
    long_line = _with_output_tail("boom", "x" * 500, width=20)
    assert long_line.splitlines()[1] == "x" * 20


async def test_app_exit_with_the_modal_open_cleans_the_workdir(tmp_path):
    """Quitting mid-decision deletes the temp workdir (the wizard's finally)."""
    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())

    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(stl)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        workdir = prepare.result.state.workdir
        prepare.open_confirm()
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        assert os.path.isdir(workdir)
        await pilot.press("ctrl+q")
        await _settle(pilot)

    assert not os.path.exists(workdir)


async def test_dashboard_ignores_a_refresh_while_one_is_in_flight(tmp_path):
    import threading

    _install_ready_settings(tmp_path)
    gate = threading.Event()

    class BlockingProvider:
        def __init__(self):
            self.calls = 0

        def fetch(self, args):
            self.calls += 1
            gate.wait(10)
            return _snap()

    provider = BlockingProvider()
    app = PlateApp(_args(), _deps(status_provider=provider))
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            while provider.calls == 0:
                await pilot.pause()
            app.screen.refresh_status()  # ignored: one is already running
            app.screen.refresh_status()
            await pilot.pause()
            assert provider.calls == 1
            gate.set()
            await _settle(pilot)
    finally:
        gate.set()


# ---------------------------------------------------------------------------
# Service-level branches (pure; no pilot)
# ---------------------------------------------------------------------------


def test_status_service_captures_every_failure_shape(monkeypatch):
    from bambu_cli.tui.services import StatusService, _short_error

    class EmptyPrinter:
        def status(self):
            return {}

    class BoomPrinter:
        def status(self):
            raise RuntimeError("mqtt exploded")

    class SilentPrinter:
        def status(self):
            raise OSError()

    ctx = MagicMock()
    monkeypatch.setattr("bambu_cli.context.RuntimeContext.for_request", classmethod(lambda cls, args: ctx))

    ctx.printer.return_value = EmptyPrinter()
    snapshot = StatusService().fetch(_args())
    assert snapshot.ok is False
    assert snapshot.error == "Printer returned no status."

    ctx.printer.return_value = BoomPrinter()
    assert StatusService().fetch(_args()).error == "mqtt exploded"

    ctx.printer.return_value = SilentPrinter()
    assert StatusService().fetch(_args()).error == "Printer unreachable (OSError)."
    assert _short_error(ValueError("plain")) == "plain"


def test_status_lines_show_targets_and_file():
    from bambu_cli.tui.services import status_lines

    snapshot = StatusSnapshot(
        ok=True,
        raw={
            "gcode_state": "RUNNING",
            "mc_percent": 12,
            "nozzle_temper": 210,
            "nozzle_target_temper": 220,
            "bed_temper": 55,
            "bed_target_temper": 60,
            "gcode_file": "cube.gcode.3mf",
        },
    )
    rows = dict(status_lines(snapshot))
    assert rows["Nozzle"] == "210°C → 220°C"
    assert rows["Bed"] == "55°C → 60°C"
    assert rows["File"] == "cube.gcode.3mf"


def test_job_progress_names_the_file_and_survives_junk():
    from bambu_cli.tui.services import job_progress_lines, progress_percent

    snapshot = StatusSnapshot(
        ok=True,
        raw={"gcode_state": "RUNNING", "mc_percent": "n/a", "gcode_file": "cube.gcode.3mf"},
    )
    assert dict(job_progress_lines(snapshot))["File"] == "cube.gcode.3mf"
    assert progress_percent(snapshot) == 0  # a non-numeric percent is not a crash


def test_pipeline_and_monitor_services_build_their_own_collaborators():
    from bambu_cli.interactive.core import GoSteps as CoreGoSteps
    from bambu_cli.tui.services import MonitorService, PipelineService, StatusService

    assert isinstance(PipelineService()._get_steps(), CoreGoSteps)
    assert isinstance(MonitorService()._provider(), StatusService)


def test_pipeline_cleanup_tolerates_none_and_missing_dirs(tmp_path):
    from bambu_cli.interactive.core import WizardState, make_workdir
    from bambu_cli.tui.services import PipelineService

    service = PipelineService()
    service.cleanup(None)  # no state: nothing to do, no error
    service.cleanup_workdir(None)
    workdir = make_workdir(prefix="bambu-tui-test-")
    service.cleanup(WizardState(workdir=workdir))
    assert not os.path.exists(workdir)
    service.cleanup_workdir(workdir)  # already gone: still fine


def test_pressed_helper_falls_back_when_nothing_is_selected():
    from bambu_cli.tui.screens.prepare import _pressed

    class NoSelection:
        pressed_index = -1

    assert _pressed(NoSelection(), ["PLA", "PETG"]) == "PLA"
    assert _pressed(NoSelection(), ["draft", "standard"], default="standard") == "standard"


# ---------------------------------------------------------------------------
# Prepare-screen wiring
# ---------------------------------------------------------------------------


async def test_print_button_opens_the_confirm_modal(tmp_path):
    """The preview's button — not just the API — reaches the modal."""
    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())

    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(stl)
        prepare.query_one("#source-input", Input).focus()
        # The prepare button, too, rather than only Enter in the source box.
        prepare.query_one("#prepare-button").press()
        await _settle(pilot)
        assert prepare.result is not None
        prepare.query_one("#print-button").press()
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        app.screen.query_one("#confirm-cancel").press()
        await _settle(pilot)


async def test_confirm_dismissed_without_an_outcome_returns_ownership(tmp_path):
    """Defensive path: a screen dismissal with no outcome destroys nothing."""
    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())

    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(stl)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        handed = prepare.take_result()
        printable = handed.state.printable_path
        prepare._confirm_closed(None)
        await _settle(pilot)
        assert prepare.result is handed
        assert os.path.exists(printable)


async def test_open_confirm_is_refused_without_a_prepared_file(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.open_confirm()  # nothing prepared yet
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)


async def test_app_level_help_and_refresh_actions_are_idempotent(tmp_path):
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus()
    app = PlateApp(_args(), _deps(status_provider=provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        before = provider.calls
        app.action_refresh()  # dashboard CAN refresh: delegated
        await _settle(pilot)
        assert provider.calls > before
        app.action_help()
        await _settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        app.action_help()  # already open: no second overlay
        await _settle(pilot)
        assert sum(isinstance(s, HelpScreen) for s in app.screen_stack) == 1


async def test_preflight_screen_quit_goes_through_the_app_guard(tmp_path):
    settings = _install_ready_settings(tmp_path)
    import shutil

    shutil.rmtree(os.path.join(settings.profiles_dir, "process"))
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        assert isinstance(app.screen, PreflightErrorScreen)
        app.set_job_in_flight(True)
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running is True  # the guard applies here too
        app.set_job_in_flight(False)
        await pilot.press("q")
        await pilot.pause()
    assert app.is_running is False


async def test_monitor_worker_stops_when_the_screen_goes_away_mid_interval(tmp_path):
    """Leaving between polls ends the loop immediately, not after the interval."""
    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([_snap("RUNNING", 10)])
    app = PlateApp(_args(), _deps(status_provider=provider, poll_interval=30.0))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        while provider.calls == 0:
            await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        # If the interruptible wait were a plain sleep, this would hang for 30s.
        await pilot.app.workers.wait_for_complete()
        assert isinstance(app.screen, DashboardScreen)


async def test_prepare_ignores_a_second_start_while_one_is_running(tmp_path):
    import threading

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_slice(ns=None, **kwargs):
        started.set()
        gate.wait(10)
        return _sliced_3mf(ns.output)

    steps = GoSteps(download=Recorder(), slice=slow_slice)
    app = PlateApp(_args(), _deps(steps=steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            await pilot.press("n")
            await _settle(pilot)
            prepare = app.screen
            prepare.query_one("#source-input", Input).value = str(stl)
            prepare.query_one("#source-input", Input).focus()
            await pilot.press("enter")
            while not started.is_set():
                await pilot.pause()
            workdir = prepare._workdir
            prepare.start_prepare()  # refused: one is already running
            await pilot.pause()
            assert prepare._workdir == workdir
            gate.set()
            await _settle(pilot)
            assert prepare.result is not None
    finally:
        gate.set()


async def test_help_is_refused_while_a_job_is_in_flight(tmp_path):
    """No overlay may cover the confirm modal while its job worker runs."""
    import threading

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_job(ns=None, **kwargs):
        started.set()
        gate.wait(10)

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=slow_job)
    app = PlateApp(_args(), _deps(steps=steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            modal = await _prepared_modal(pilot, app, stl)
            modal.query_one("#confirm-print").press()
            while not started.is_set():
                await pilot.pause()
            assert modal.busy_with_job is True
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal), "help covered a modal mid-job"
            gate.set()
            await _settle(pilot)
            assert isinstance(app.screen, MonitorScreen)
    finally:
        gate.set()


async def test_job_outcome_lands_even_if_an_overlay_covers_the_modal(tmp_path):
    """Second layer: an overlay pushed over the modal cannot strand the result.

    Reproduces the verifier's scenario with the app-level guard bypassed (the
    help screen is pushed directly), so the modal's own repair path is what is
    under test.
    """
    import threading

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_job(ns=None, **kwargs):
        started.set()
        gate.wait(10)

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=slow_job)
    app = PlateApp(_args(), _deps(steps=steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            modal = await _prepared_modal(pilot, app, stl)
            modal.query_one("#confirm-print").press()
            while not started.is_set():
                await pilot.pause()
            app.push_screen(HelpScreen())  # bypasses action_help's guard
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            gate.set()
            await _settle(pilot)
            # The outcome landed: overlay gone, modal gone, monitor open.
            assert not any(isinstance(s, HelpScreen) for s in app.screen_stack)
            assert not any(isinstance(s, ConfirmModal) for s in app.screen_stack)
            assert isinstance(app.screen, MonitorScreen)
    finally:
        gate.set()


async def test_upload_only_outcome_lands_from_under_an_overlay(tmp_path):
    """Same repair for upload-only: the prepare screen gets its message."""
    import threading

    _install_ready_settings(tmp_path)
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_job(ns=None, **kwargs):
        started.set()
        gate.wait(10)

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=slow_job)
    app = PlateApp(_args(), _deps(steps=steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            modal = await _prepared_modal(pilot, app, stl)
            modal.query_one("#confirm-upload").press()
            while not started.is_set():
                await pilot.pause()
            app.push_screen(HelpScreen())
            await pilot.pause()
            gate.set()
            await _settle(pilot)
            assert isinstance(app.screen, PrepareScreen)
            assert "Uploaded" in _text(app.screen.query_one("#prepare-status", Static))
    finally:
        gate.set()


async def test_dashboard_shows_the_progress_bar_only_while_a_job_runs(tmp_path):
    """A 0% bar on an idle printer reads as a stalled print, so it stays hidden."""
    from bambu_cli.tui.widgets.job_progress import JobProgress

    _install_ready_settings(tmp_path)
    provider = ScriptedStatus([_snap("IDLE", 0), _snap("RUNNING", 42), _snap("FINISH", 100)])
    app = PlateApp(_args(), _deps(status_provider=provider))
    async with app.run_test() as pilot:
        await _settle(pilot)
        bar = app.screen.query_one("#dash-progress", JobProgress)
        assert bar.display is False  # IDLE

        await pilot.press("r")
        await _settle(pilot)
        assert bar.display is True  # RUNNING

        await pilot.press("r")
        await _settle(pilot)
        assert bar.display is False  # FINISH is not an active state


async def test_confirm_modal_says_what_it_is_about_to_print(tmp_path):
    """The riskiest dialog in the app must show more than a temp path."""
    _install_ready_settings(tmp_path)
    model = tmp_path / "cube.stl"
    model.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())
    app = PlateApp(_args(), _deps(steps=steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#source-input", Input).value = str(model)
        prepare.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        prepare.open_confirm()
        await _settle(pilot)
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        # The preview rows the prepare screen computed travel to the dialog.
        assert modal._rows, "confirm modal received no summary rows"
        assert modal.query_one("#confirm-body").border_title == "Confirm"
        # Rendered through a Rich grid, so assert on the labels reaching it.
        labels = [str(label) for label, _value in modal._rows]
        assert any("Model" in x for x in labels), labels
