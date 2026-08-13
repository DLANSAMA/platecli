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
import zipfile

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, RadioButton, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.prepare import PreflightErrorScreen, PrepareScreen  # noqa: E402
from bambu_cli.tui.services import StatusSnapshot  # noqa: E402
from tests.tui_text import widget_text  # noqa: E402

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
    return widget_text(widget)

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
    # It must NOT claim the chosen material was applied to a file we did not
    # slice. Assert on the material NAME, not on a padded "Material   PETG":
    # that spelling was coupled to the old f-string layout and went vacuous the
    # moment the preview became a Rich grid. The name cannot appear in any other
    # row here (model, printer, estimate), so its absence is the whole guard.
    assert "PETG" not in preview
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

# ---------------------------------------------------------------------------
# Layout: the form on the left, what the run produced on the right
#
# Geometry, not text: these assert where the widgets actually land, because a
# containment assertion alone passed happily while the preview sat below the
# fold. Sizes are the two the project supports (see PrepareScreen and
# tests/test_tui_polish.py's 80x24 cases).
# ---------------------------------------------------------------------------

_WIDE = (100, 30)
_NARROW = (80, 24)

def _column_of(screen, selector):
    """Which prepare column owns a widget ('prepare-inputs'/'prepare-output')."""
    for ancestor in screen.query_one(selector).ancestors:
        if ancestor.id in ("prepare-inputs", "prepare-output"):
            return ancestor.id
    return None

async def test_wide_terminal_puts_the_form_beside_the_results(tmp_path):
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)

        for selector in (
            "#source-input",
            "#source-error",
            "#material-set",
            "#material-pla",
            "#quality-set",
            "#quality-standard",
            "#supports-check",
            "#prepare-actions",
            "#prepare-button",
            "#settings-button",
            "#settings-summary",
        ):
            assert _column_of(screen, selector) == "prepare-inputs", selector
        for selector in ("#prepare-status", "#preview", "#print-button"):
            assert _column_of(screen, selector) == "prepare-output", selector

        inputs = screen.query_one("#prepare-inputs")
        output = screen.query_one("#prepare-output")
        # Side by side and not overlapping, on the same row.
        assert output.region.x >= inputs.region.right
        assert output.region.y == inputs.region.y
        assert not screen.query_one("#prepare-columns").has_class("narrow")

async def test_wide_terminal_shows_the_estimate_without_scrolling(tmp_path):
    """The point of the restructure: preview + Start print visible with the form."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=_sliced_3mf(tmp_path)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)

        assert "Estimate" in _text(screen.query_one("#preview", Static))
        assert screen.query_one("#print-button").disabled is False
        viewport = app.screen.region
        for selector in ("#source-input", "#material-set", "#prepare-button", "#preview", "#print-button"):
            assert viewport.contains_region(screen.query_one(selector).region), selector
        # …and nothing had to scroll to get there.
        assert screen.query_one("#prepare-body").scroll_offset.y == 0

async def test_narrow_terminal_stacks_the_columns(tmp_path):
    """Two 40-column halves cannot hold a material label, so 80x24 stacks."""
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps(ams_detector=lambda args: "ABS"))
    async with app.run_test(size=_NARROW) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)

        assert screen.query_one("#prepare-columns").has_class("narrow")
        inputs = screen.query_one("#prepare-inputs")
        output = screen.query_one("#prepare-output")
        assert output.region.x == inputs.region.x
        assert output.region.y >= inputs.region.bottom
        # The longest label there is (material + the AMS tag) still fits on one
        # line — the reason the two-column layout has a width floor at all.
        material = screen.query_one("#material-set")
        assert "(detected in AMS)" in str(screen.query_one("#material-abs", RadioButton).label)
        assert material.outer_size.width <= 80
        assert material.outer_size.height == len(("PLA", "PETG", "ABS", "TPU")) + 2  # + border

async def test_detected_material_label_survives_the_form_scrollbar(tmp_path):
    """A short terminal must not truncate WHICH material was detected.

    The form is taller than a 25-row terminal, so #prepare-inputs grows a
    vertical scrollbar — and a Textual scrollbar takes real cells rather than
    overlaying. With the column at 60 that left the RadioSet 54 cells against a
    53-cell button, then 52 once the scrollbar appeared, so the label rendered
    as "…(detected in AMS" with the closing paren shaved off. Found by
    recording the TUI and looking at the frames; no assertion had caught it.
    """
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps(ams_detector=lambda args: "PLA"))
    async with app.run_test(size=(122, 25)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)

        inputs = screen.query_one("#prepare-inputs")
        assert inputs.show_vertical_scrollbar, "test is vacuous without the scrollbar"
        from rich.text import Text

        button = screen.query_one("#material-pla", RadioButton)
        # +4 for the toggle glyph and its padding.
        needed = Text.from_markup(str(button.label)).cell_len + 4
        assert screen.query_one("#material-set").content_size.width >= needed

async def test_narrow_terminal_scrolls_the_finished_run_into_view(tmp_path):
    """Stacked, the results start below the fold; the finished run must come up."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=_sliced_3mf(tmp_path)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_NARROW) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        button = screen.query_one("#print-button")
        preview = screen.query_one("#preview")
        assert not app.screen.region.contains_region(button.region)  # below the fold

        await _submit_source(pilot, screen, stl)
        await pilot.pause()

        assert "Estimate" in _text(screen.query_one("#preview", Static))
        assert app.screen.region.contains_region(preview.region)
        assert app.screen.region.contains_region(button.region)
        assert preview.outer_size.width <= 80

# ---------------------------------------------------------------------------
# The results column: a titled box that says what will land in it, and a
# label/value grid whose wrapped values stay out of the label column.
# ---------------------------------------------------------------------------

def _render_at(renderable, width):
    """Plain text of a Rich renderable at an exact console width.

    force_terminal=False as well as no_color: the label column is styled bold,
    and bold is not a colour — without it the capture carries SGR codes and the
    column arithmetic below measures escape sequences instead of glyphs.
    """
    from rich.console import Console

    console = Console(width=max(width, 1), no_color=True, force_terminal=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()

def test_summary_grid_wraps_a_value_under_the_value_column():
    """The defect: f"{label:<11}{value}" continued a wrap in the label column.

    "Bambu Lab P1S, 0.4mm nozzle" then rendered as a Printer row followed by a
    line reading "nozzle", which looks like a field name.
    """
    from bambu_cli.tui.widgets.summary import summary_grid

    rows = [("Printer", "Bambu Lab P1S, 0.4mm nozzle"), ("Estimate", "1h 42m, ~13 g")]
    lines = _render_at(summary_grid(rows), 30).splitlines()

    value_column = lines[0].index("Bambu")
    assert value_column > 0
    wrapped = [line for line in lines if line.strip() == "nozzle"]
    assert wrapped, lines  # it did wrap at this width
    for line in wrapped:
        # …to the value column, with the label column left blank.
        assert line.index("nozzle") == value_column, line
        assert line[:value_column].strip() == "", line

def test_summary_grid_renders_a_bracketed_filename_verbatim():
    """A "[" in a filename is not Rich markup: str cells would eat it."""
    from bambu_cli.tui.widgets.summary import summary_grid

    out = _render_at(summary_grid([("Model", "benchy [remix].stl")]), 60)
    assert "benchy [remix].stl" in out

    # And the shape that does not merely render wrong but raises MarkupError.
    closing = _render_at(summary_grid([("Model", "a[/b]c.gcode")]), 60)
    assert "a[/b]c.gcode" in closing

def test_summary_grid_tolerates_no_rows():
    from bambu_cli.tui.widgets.summary import summary_grid

    assert _render_at(summary_grid(None), 40).strip() == ""
    assert _render_at(summary_grid([]), 40).strip() == ""

async def test_results_column_is_a_titled_box_with_a_placeholder(tmp_path):
    """Before any run the column must not read as a half-rendered widget."""
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)

        output = screen.query_one("#prepare-output")
        assert str(output.border_title) == "Result"
        # round, not thick: thick renders as solid slabs top and bottom (0d63378).
        assert output.styles.border_top[0] == "round"

        placeholder = _text(screen.query_one("#prepare-status", Static))
        assert "Nothing prepared yet" in placeholder
        assert "Prepare" in placeholder
        # Never mistakable for a real result: no estimate-shaped claim in it.
        assert "Estimate" not in placeholder
        assert _text(screen.query_one("#preview", Static)) == ""

async def test_placeholder_is_replaced_by_the_real_status(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=_sliced_3mf(tmp_path)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)

        status = _text(screen.query_one("#prepare-status", Static))
        assert "Nothing prepared yet" not in status
        assert "Start print" in status

async def test_preview_never_starts_a_line_in_the_label_column(tmp_path):
    """The screen, not just the helper: a wrap must not invent a field name."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=_sliced_3mf(tmp_path)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, stl)

        preview = screen.query_one("#preview", Static)
        labels = {str(label) for label, _ in screen.result.rows}
        rendered = widget_text(preview)
        # A wrapped value must stay in the value column (leading spaces), never
        # start a line that looks like a new label. Textual 8 tables do not
        # always wrap at this width; unwrapped rows still start with a label.
        for line in rendered.splitlines():
            if line and not line.startswith(" "):
                assert line.split()[0] in labels, line

async def test_preview_shows_a_bracketed_filename_verbatim(tmp_path):
    """End to end: the preview Static must not markup-parse a filename."""
    _install_ready_settings(tmp_path)
    stl = tmp_path / "benchy [remix] v2.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=_sliced_3mf(tmp_path)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_WIDE) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        await _submit_source(pilot, screen, str(stl))

        assert "benchy [remix] v2.stl" in _text(screen.query_one("#preview", Static))

async def test_form_groups_are_one_width_that_fills_the_column(tmp_path):
    """One form, not four boxes of unrelated size with a ragged right edge."""
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps())
    async with app.run_test(size=(120, 34)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)

        column = screen.query_one("#prepare-inputs").content_size.width
        widths = {
            selector: screen.query_one(selector).outer_size.width
            for selector in ("#material-set", "#quality-set", "#supports-check", "#prepare-actions")
        }
        assert set(widths.values()) == {column}, widths

async def test_narrow_terminal_scrolls_a_failure_into_view(tmp_path):
    """Stacked, a failure lands below the fold too — and looks like nothing ran."""
    from bambu_cli.errors import BambuError

    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(raises=BambuError("slicer failed: disk on fire", exit_code=4)))

    app = PlateApp(_args(), _deps(steps))
    async with app.run_test(size=_NARROW) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        status = screen.query_one("#prepare-status", Static)
        assert not app.screen.region.contains_region(status.region)  # below the fold

        await _submit_source(pilot, screen, stl)
        await pilot.pause()

        assert "disk on fire" in _text(status)
        assert app.screen.region.contains_region(status.region)
