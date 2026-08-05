"""Pilot tests for the confirm modal — the TUI's only physical-action gate.

Every case drives the real screens with a fake ``GoSteps`` job runner, so the
assertions are about what namespace the pipeline *would* receive: nothing here
touches a printer, and ``confirm=True`` can only originate from the modal's
"Start print" button.
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

from textual.widgets import Input, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.confirm import ConfirmModal  # noqa: E402
from bambu_cli.tui.screens.monitor import MonitorScreen  # noqa: E402
from bambu_cli.tui.screens.prepare import PrepareScreen  # noqa: E402
from bambu_cli.tui.services import StatusSnapshot  # noqa: E402

_IDLE = StatusSnapshot(ok=True, raw={"gcode_state": "IDLE", "mc_percent": 0}, ams={"units": []})


class FakeStatusProvider:
    def __init__(self, snapshots=None):
        self._snapshots = list(snapshots) if snapshots else [_IDLE]
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


def _make_stl(tmp_path, name="cube.stl"):
    p = tmp_path / name
    p.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    return str(p)


def _sliced_3mf(path, name="cube.gcode.3mf"):
    p = path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config>'
            '<metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/>'
            "</config>",
        )
    return str(p)


def _slicer_into_workdir(tmp_path):
    """A fake slicer that writes the .3mf inside the workdir, like the real one."""

    def _slice(ns=None, **kwargs):
        return _sliced_3mf(__import__("pathlib").Path(ns.output))

    return _slice


def _deps(steps, ams_detector=None, **kwargs):
    return TuiDeps(
        status_provider=FakeStatusProvider(),
        steps=steps,
        ams_detector=ams_detector if ams_detector is not None else (lambda args: None),
        **kwargs,
    )


async def _settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _text(widget) -> str:
    renderable = getattr(widget, "renderable", "")
    return renderable if isinstance(renderable, str) else str(renderable)


async def _prepare_to_preview(pilot, source):
    """dashboard → n → source → prepared preview; returns the prepare screen."""
    await pilot.press("n")
    await _settle(pilot)
    screen = pilot.app.screen
    assert isinstance(screen, PrepareScreen)
    screen.query_one("#source-input", Input).value = source
    screen.query_one("#source-input", Input).focus()
    await pilot.press("enter")
    await _settle(pilot)
    assert screen.result is not None, "prepare did not reach the preview"
    return screen


async def _open_modal(pilot, screen):
    screen.open_confirm()
    await _settle(pilot)
    modal = pilot.app.screen
    assert isinstance(modal, ConfirmModal)
    return modal


async def _press_button(pilot, modal, button_id):
    modal.query_one(button_id).press()
    await _settle(pilot)


# ---------------------------------------------------------------------------


async def test_start_print_passes_confirm_true_exactly_once(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        # Nothing has been sent just by preparing.
        assert job.calls == []
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-print")

    assert len(job.calls) == 1
    assert [ns.confirm for ns in job.calls] == [True]
    assert job.calls[0].source.endswith(".gcode.3mf")


async def test_upload_only_passes_confirm_false(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-upload")
        assert isinstance(pilot.app.screen, PrepareScreen)
        message = _text(pilot.app.screen.query_one("#prepare-status", Static))

    assert len(job.calls) == 1
    assert job.calls[0].confirm is False
    assert "Uploaded" in message
    # Upload-only must not open the monitor (nothing is printing).
    assert not any(isinstance(s, MonitorScreen) for s in app.screen_stack)


async def test_no_other_path_reaches_the_job_runner(tmp_path):
    """Preparing, backing out and declining never call the job pipeline."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        modal = await _open_modal(pilot, screen)
        await pilot.press("escape")  # back out
        await _settle(pilot)
        assert isinstance(pilot.app.screen, PrepareScreen)
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-cancel")
        await pilot.press("escape")  # leave the prepare screen entirely
        await _settle(pilot)

    assert job.calls == []


async def test_backing_out_of_the_modal_keeps_the_prepared_file(tmp_path):
    """Esc in the modal returns ownership: the file and preview survive."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=Recorder())

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        printable = screen.result.state.printable_path
        modal = await _open_modal(pilot, screen)
        await pilot.press("escape")
        await _settle(pilot)
        assert isinstance(pilot.app.screen, PrepareScreen)
        assert screen.result is not None, "ownership was not handed back"
        assert os.path.exists(printable)
        assert screen.query_one("#print-button").disabled is False


async def test_decline_preserves_the_sliced_file_and_names_it(tmp_path, monkeypatch):
    """Cancel keeps the sliced file (wizard's decline path) and says where."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=Recorder())

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        workdir = screen.result.state.workdir
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-cancel")
        message = _text(pilot.app.screen.query_one("#prepare-status", Static))

    assert message.startswith("Nothing sent. Sliced file kept at ")
    kept = message.split("Nothing sent. Sliced file kept at ", 1)[1].strip()
    assert os.path.exists(kept), f"declining deleted the sliced file ({kept})"
    assert os.path.dirname(os.path.abspath(kept)) == str(cwd)
    assert not os.path.exists(workdir)


async def test_ams_mapping_only_when_detected_material_is_kept(tmp_path):
    """use_ams rides on the detected-material rule, through the real screens."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    def detector(args, on_active_slot=None):
        on_active_slot(2)
        return "PETG"

    app = PlateApp(_args(), _deps(steps, ams_detector=detector))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        assert screen.selected_material() == "PETG"  # kept the detected material
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-print")

    assert job.calls[0].use_ams is True
    assert job.calls[0].ams_mapping == "2"


async def test_ams_mapping_absent_when_user_changes_material(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder()
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    def detector(args, on_active_slot=None):
        on_active_slot(2)
        return "PETG"

    app = PlateApp(_args(), _deps(steps, ams_detector=detector))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        screen = pilot.app.screen
        # The user overrides the detected PETG with ABS.
        screen.query_one("#material-abs").value = True
        await pilot.pause()
        screen.query_one("#source-input", Input).value = stl
        screen.query_one("#source-input", Input).focus()
        await pilot.press("enter")
        await _settle(pilot)
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-print")

    assert job.calls[0].confirm is True
    assert not getattr(job.calls[0], "use_ams", False)


async def test_failed_job_keeps_the_modal_open_and_the_file(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    job = Recorder(raises=BambuError("printer refused the upload", exit_code=6))
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=job)

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        printable = screen.result.state.printable_path
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-print")
        assert isinstance(pilot.app.screen, ConfirmModal)
        assert "printer refused the upload" in _text(modal.query_one("#confirm-status", Static))
        assert os.path.exists(printable)
        assert app.job_in_flight is False
        assert modal.query_one("#confirm-print").disabled is False
        await _press_button(pilot, modal, "#confirm-cancel")


async def test_quit_is_refused_while_a_job_worker_is_in_flight(tmp_path):
    import threading

    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    gate = threading.Event()
    started = threading.Event()

    def slow_job(ns=None, **kwargs):
        started.set()
        gate.wait(10)

    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=slow_job)
    app = PlateApp(_args(), _deps(steps))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            screen = await _prepare_to_preview(pilot, stl)
            modal = await _open_modal(pilot, screen)
            modal.query_one("#confirm-print").press()
            while not started.is_set():
                await pilot.pause()
            assert app.job_in_flight is True
            printable = modal._state.printable_path
            # ctrl+q is Textual's PRIORITY quit binding: unlike plain "q" it
            # pierces a modal screen, so this is the quit path that can actually
            # reach the app while the modal owns the screen.
            await pilot.press("ctrl+q")
            await pilot.pause()
            assert app.is_running is True, "quit abandoned an in-flight upload"
            assert os.path.exists(printable), "quitting mid-upload destroyed the file"
            gate.set()
            await _settle(pilot)
            assert app.job_in_flight is False
    finally:
        gate.set()


async def test_start_print_opens_the_monitor(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=Recorder())

    app = PlateApp(_args(), _deps(steps, poll_interval=0.01))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        modal = await _open_modal(pilot, screen)
        await _press_button(pilot, modal, "#confirm-print")
        assert isinstance(pilot.app.screen, MonitorScreen)


# --- the summary grid renders filenames as data, not Rich markup -----------


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


async def test_confirm_summary_shows_a_bracketed_filename_verbatim(tmp_path):
    """The one screen that names the file must not let Rich eat part of it.

    A ``str`` cell in a Rich grid is markup-parsed, so "model [remix].stl"
    renders as "model .stl" — the modal would then ask the user to confirm
    sending a file under a name that is not the file's name.
    """
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path, name="model [remix].stl")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir(tmp_path), job=Recorder())

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = await _prepare_to_preview(pilot, stl)
        modal = await _open_modal(pilot, screen)
        summary = _render_to_text(modal.query_one("#confirm-summary", Static).renderable)
        await _press_button(pilot, modal, "#confirm-cancel")

    assert "model [remix].stl" in summary


def test_confirm_summary_survives_a_markup_shaped_value():
    """A closing-tag shape must render, not raise MarkupError mid-modal."""
    from bambu_cli.tui.screens.confirm import _summary_table

    rows = [("Model", "a[/b]c.gcode"), ("Overrides", "process: layer_height=[0.98]")]
    text = _render_to_text(_summary_table(rows))  # raises MarkupError against str cells
    assert "a[/b]c.gcode" in text
    assert "[0.98]" in text
