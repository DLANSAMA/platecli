"""Textual pilot tests for the prepare flow (source → presets → slice → preview).

Driven headlessly via ``PlateApp(...).run_test()`` with an injected ``TuiDeps``:
the pipeline is the real ``PipelineService`` running the real shared
``interactive.core`` pipeline, but its ``GoSteps`` collaborators are fakes — no
network, no slicer, no MQTT. Preflight passes because a fake OrcaSlicer
executable and profiles directory are installed in the test context.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

pytest.importorskip("textual")

from textual.widgets import Input, RadioButton, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.prepare import PreflightErrorScreen, PrepareScreen  # noqa: E402
from bambu_cli.tui.services import StatusSnapshot  # noqa: E402

_IDLE = StatusSnapshot(ok=True, raw={"gcode_state": "IDLE", "mc_percent": 0}, ams={"units": []})


class FakeStatusProvider:
    def fetch(self, args):
        return _IDLE


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


def _make_stl(tmp_path, name="cube.stl"):
    p = tmp_path / name
    p.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    return str(p)


def _sliced_3mf(tmp_path, name="cube.gcode.3mf"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config>'
            '<metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/>'
            "</config>",
        )
    return str(p)


def _deps(steps=None, ams_detector=None):
    return TuiDeps(
        status_provider=FakeStatusProvider(),
        steps=steps if steps is not None else GoSteps(),
        ams_detector=ams_detector if ams_detector is not None else (lambda args: None),
    )


async def _settle(pilot):
    """Let queued messages AND thread workers finish before asserting."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def _open_prepare(pilot):
    await pilot.press("n")
    await _settle(pilot)
    screen = pilot.app.screen
    assert isinstance(screen, PrepareScreen)
    return screen


async def _submit_source(pilot, screen, source):
    screen.query_one("#source-input", Input).value = source
    screen.query_one("#source-input", Input).focus()
    await pilot.press("enter")
    await _settle(pilot)


def _text(widget) -> str:
    renderable = getattr(widget, "renderable", "")
    return renderable if isinstance(renderable, str) else str(renderable)


# ---------------------------------------------------------------------------


async def test_n_opens_prepare_screen(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await _open_prepare(pilot)
        # Pressing 'n' again does not stack a second prepare screen.
        await pilot.press("n")
        await _settle(pilot)
        assert sum(isinstance(s, PrepareScreen) for s in app.screen_stack) == 1


async def test_invalid_source_shows_inline_error(tmp_path):
    _install_ready_settings(tmp_path)
    slicer = Recorder()
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, str(tmp_path / "does-not-exist.stl"))
        error = _text(screen.query_one("#source-error", Static))
        assert "File not found" in error
        # Nothing was prepared: the pipeline never ran.
        assert slicer.calls == []
        assert screen.result is None
        # Typing again clears the error.
        screen.query_one("#source-input", Input).value = "x"
        await pilot.pause()
        assert _text(screen.query_one("#source-error", Static)) == ""


async def test_valid_local_stl_reaches_preview(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    slicer = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(), slice=slicer)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)
        preview = _text(screen.query_one("#preview", Static))
        assert screen.result is not None
        workdir = screen.result.state.workdir

    assert "cube.stl" in preview
    assert "P1S" in preview or "0.4mm nozzle" in preview
    assert "PLA" in preview  # the default material was applied to the slice
    assert "Estimate" in preview
    # The slicer ran once, inside the temp workdir the screen owns.
    assert len(slicer.calls) == 1
    assert slicer.calls[0].output == workdir
    # Leaving the screen took the temp workdir with it.
    assert not os.path.exists(workdir)


async def test_presliced_3mf_shows_material_not_applied_caveat(tmp_path):
    _install_ready_settings(tmp_path)
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    slicer = Recorder()
    steps = GoSteps(download=Recorder(), slice=slicer)

    app = PlateApp(_args(), _deps(steps, ams_detector=lambda args: "PETG"))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, presliced)
        preview = _text(screen.query_one("#preview", Static))

    assert "pre-sliced — material settings not applied" in preview
    # It must NOT claim the chosen material was applied to a file we did not slice.
    assert "Material   PETG" not in preview
    assert slicer.calls == []


async def test_ams_detected_material_is_preselected(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps(ams_detector=lambda args: "PETG"))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        assert screen.selected_material() == "PETG"
        petg = screen.query_one("#material-petg", RadioButton)
        pla = screen.query_one("#material-pla", RadioButton)
        assert "(detected in AMS)" in str(petg.label)
        assert "(detected in AMS)" not in str(pla.label)
        assert petg.value is True
        assert pla.value is False


async def test_no_ams_detection_keeps_pla_default(tmp_path):
    _install_ready_settings(tmp_path)
    # A detector that fails entirely (the real one returns None on any error).
    app = PlateApp(_args(), _deps(ams_detector=lambda args: None))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        assert screen.selected_material() == "PLA"
        assert screen.selected_quality() == "standard"
        assert screen.selected_supports() is False
        for name in ("pla", "petg", "abs", "tpu"):
            assert "(detected in AMS)" not in str(screen.query_one(f"#material-{name}", RadioButton).label)


async def test_pipeline_failure_renders_inline_and_app_survives(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(raises=BambuError("slicer exploded", exit_code=4)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)
        status = _text(screen.query_one("#prepare-status", Static))
        assert "slicer exploded" in status
        assert screen.result is None
        assert app.is_running is True


async def test_supports_checkbox_and_quality_reach_the_slicer(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    slicer = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(), slice=slicer)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#supports-check").value = True
        screen.query_one("#quality-draft", RadioButton).value = True
        await pilot.pause()
        assert screen.selected_quality() == "draft"
        await _submit_source(pilot, screen, stl)

    assert len(slicer.calls) == 1
    assert slicer.calls[0].supports is True
    assert slicer.calls[0].quality == "draft"


async def test_preflight_failure_points_at_plate_setup(tmp_path):
    settings = _install_ready_settings(tmp_path)
    import shutil

    shutil.rmtree(os.path.join(settings.profiles_dir, "process"))
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        screen = app.screen
        assert isinstance(screen, PreflightErrorScreen)
        problem = _text(screen.query_one("#preflight-problem", Static))
        hint = _text(screen.query_one("#preflight-hint", Static))
        assert "profiles" in problem.lower()
        assert "plate setup" in hint
        # Escape returns to the dashboard rather than trapping the user.
        await pilot.press("escape")
        await _settle(pilot)
        assert not isinstance(app.screen, PreflightErrorScreen)


async def test_unconfigured_printer_blocks_prepare(tmp_path):
    _install_ready_settings(tmp_path, printer_ip="0.0.0.0")
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        assert isinstance(app.screen, PreflightErrorScreen)
        assert "plate setup" in _text(app.screen.query_one("#preflight-problem", Static))


async def test_escape_returns_to_dashboard_from_prepare(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test() as pilot:
        await _settle(pilot)
        await _open_prepare(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert not isinstance(app.screen, PrepareScreen)


async def test_unexpected_pipeline_error_is_reported_not_raised(tmp_path):
    """A non-BambuError from a collaborator still renders inline (thread worker)."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(raises=OSError("disk on fire")))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)
        assert "disk on fire" in _text(screen.query_one("#prepare-status", Static))
        assert screen.result is None
        assert app.is_running is True


async def test_second_prepare_cleans_up_the_first_workdir(tmp_path):
    """Re-preparing must not leak the previous run's temp workdir."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=sliced))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)
        first_workdir = screen.result.state.workdir
        await _submit_source(pilot, screen, stl)
        second_workdir = screen.result.state.workdir
        assert first_workdir != second_workdir
        assert not os.path.exists(first_workdir)


async def _open_prepare_nowait(pilot):
    """Open the prepare screen WITHOUT waiting for its workers to finish.

    Used by the race tests, where the AMS detector / slicer is deliberately
    still running when we assert.
    """
    await pilot.press("n")
    await pilot.pause()
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, PrepareScreen)
    return screen


async def _wait_for(condition, pilot, timeout=5.0):
    """Pump the UI until ``condition()`` is true (never sleeps blindly)."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "condition never became true"
        await pilot.pause()
        await asyncio.sleep(0.02)


async def test_escaping_mid_prepare_does_not_leak_the_workdir(tmp_path):
    """Leaving the screen while the pipeline is still running still cleans up.

    The temp workdir is created by the prepare run, so whoever created it must
    delete it even when the worker finishes after the screen is gone (nothing is
    handed to the caller in that case, and multi-MB downloads live in there).
    """
    import threading

    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    gate = threading.Event()
    seen: dict[str, str] = {}

    def slow_slice(ns=None, **kwargs):
        seen["workdir"] = ns.output
        gate.wait(10)
        return sliced

    steps = GoSteps(download=Recorder(), slice=slow_slice)
    app = PlateApp(_args(), _deps(steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            screen = await _open_prepare(pilot)
            screen.query_one("#source-input", Input).value = stl
            screen.query_one("#source-input", Input).focus()
            await pilot.press("enter")
            # The slicer is now blocked inside the thread worker, holding the workdir.
            await _wait_for(lambda: "workdir" in seen, pilot)
            assert os.path.isdir(seen["workdir"])
            await pilot.press("escape")  # leave mid-prepare
            await pilot.pause()
            gate.set()  # let the pipeline finish into a screen that is gone
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
    finally:
        gate.set()

    assert not os.path.exists(seen["workdir"]), f"leaked temp workdir {seen['workdir']}"


async def test_manual_material_choice_survives_late_ams_detection(tmp_path):
    """A slow AMS read must never overwrite a choice the user already made.

    The real detector blocks on MQTT for seconds; anything it reports after the
    user has touched the material control would silently slice the wrong
    filament profile.
    """
    import threading

    _install_ready_settings(tmp_path)
    gate = threading.Event()

    def slow_detector(args, on_active_slot=None):
        gate.wait(10)
        on_active_slot(0)
        return "PLA"

    app = PlateApp(_args(), _deps(ams_detector=slow_detector))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            screen = await _open_prepare_nowait(pilot)
            # The user picks PETG while detection is still in flight.
            screen.query_one("#material-set").focus()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert screen.selected_material() == "PETG"
            gate.set()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # Detection landed late: it may TAG its finding, but must not re-pick.
            assert screen.selected_material() == "PETG"
            assert "(detected in AMS)" in str(screen.query_one("#material-pla", RadioButton).label)
    finally:
        gate.set()


def test_message_less_prepare_failure_is_not_diagnosed_as_unreachable(tmp_path):
    """A blank OSError from the slicer is a prepare failure, not a dead printer."""
    from bambu_cli.tui.services import PipelineService

    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    service = PipelineService(steps=GoSteps(download=Recorder(), slice=Recorder(raises=OSError())))
    result = service.prepare(_args(), source=stl, material="PLA", quality="standard", supports=False)

    assert result.ok is False
    assert "unreachable" not in (result.error or "").lower()
    assert result.error == "Preparing the model failed (OSError)."
    assert not os.path.exists(result.state.workdir)
