"""Tests for the TUI orientation feature: pose editor, model view, services and pilot flows."""

from __future__ import annotations

import argparse
import os
import zipfile

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual.widgets import Button, Input, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive.core import GoSteps  # noqa: E402
from bambu_cli.mesh import MeshError, OrientRequest  # noqa: E402
from bambu_cli.mesh.formats import write_stl  # noqa: E402
from bambu_cli.mesh.geometry import rotation_about  # noqa: E402
from bambu_cli.mesh.render import Image  # noqa: E402
from bambu_cli.tui import orient_model as om  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.orient import CANCELLED, OrientScreen  # noqa: E402
from bambu_cli.tui.screens.prepare import PrepareScreen  # noqa: E402
from bambu_cli.tui.services import (  # noqa: E402
    PipelineService,
    StatusSnapshot,
    carry_fetched_model,
    needs_fetch,
)
from bambu_cli.tui.widgets.model_view import draw_model, half_blocks  # noqa: E402
from tests.mesh_shapes import box, thin_plate, write_3mf  # noqa: E402
from tests.tui_text import widget_text  # noqa: E402

_IDLE = StatusSnapshot(ok=True, raw={"gcode_state": "IDLE", "mc_percent": 0}, ams={"units": []})


class FakeStatusProvider:
    def fetch(self, args):
        return _IDLE


class Recorder:
    def __init__(self, return_value=None, raises=None, side_effect=None):
        self.calls = []
        self.return_value = return_value
        self.raises = raises
        self.side_effect = side_effect

    def __call__(self, ns=None, **kwargs):
        self.calls.append(ns)
        if self.raises is not None:
            raise self.raises
        if self.side_effect is not None:
            return self.side_effect(ns)
        return self.return_value


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _reset_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)


def _install_ready_settings(tmp_path):
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
    _context.set_current(RuntimeContext(settings=replace(settings)))


def _args(**kwargs):
    base = {"cmd": "tui", "sim": False, "json": False, "verbose": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _deps(steps=None, pipeline=None):
    return TuiDeps(
        status_provider=FakeStatusProvider(),
        steps=steps if steps is not None else GoSteps(),
        ams_detector=lambda args: None,
        pipeline=pipeline,
    )


async def _settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def _open_prepare(pilot):
    await pilot.press("n")
    await _settle(pilot)
    screen = pilot.app.screen
    assert isinstance(screen, PrepareScreen)
    return screen


def _text(widget) -> str:
    return widget_text(widget)


def _stl(tmp_path, mesh=None, name="part.stl"):
    p = tmp_path / name
    write_stl(str(p), mesh if mesh is not None else thin_plate())
    return str(p)


def _sliced_3mf(tmp_path, name="out.gcode.3mf"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", "G1 X1\n")
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config><metadata key="prediction" value="2520"/></config>',
        )
    return str(p)


def _slicer(tmp_path, summary=None):
    sliced = _sliced_3mf(tmp_path)

    def run(ns):
        if summary is not None:
            ns.orientation_result = summary
        return sliced

    return Recorder(side_effect=run)


def _downloader(tmp_path_unused=None):
    def run(ns):
        path = os.path.join(ns.output, "dl.stl")
        write_stl(path, thin_plate())
        return path

    return Recorder(side_effect=run)


async def _set_source(screen, value):
    box_ = screen.query_one("#source-input", Input)
    box_.value = value


# --- orient_model ------------------------------------------------------------


def test_with_turn_merges_same_axis():
    r = om.with_turn(om.with_turn(OrientRequest(), "z", 15), "z", 15)
    assert r.rotations == (("z", 30.0),)


def test_with_turn_different_axis_appends():
    r = om.with_turn(om.with_turn(OrientRequest(), "z", 15), "x", 90)
    assert r.rotations == (("z", 15.0), ("x", 90.0))


def test_with_turn_folds_into_half_open_range():
    r = om.with_turn(om.with_turn(OrientRequest(), "x", 90), "x", 90)
    assert r.rotations == (("x", 180.0),)
    r = om.with_turn(r, "x", 90)
    assert r.rotations == (("x", -90.0),)
    r = om.with_turn(OrientRequest(), "y", -180)
    assert r.rotations == (("y", 180.0),)


def test_with_turn_dropping_zero_sum():
    r = om.with_turn(om.with_turn(OrientRequest(), "z", 90), "z", -90)
    assert r.rotations == ()
    r = om.with_turn(om.with_turn(OrientRequest(), "z", 180), "z", 180)
    assert r.rotations == ()


_KEEP = OrientRequest(mode="keep")


def test_flags_variants():
    # Relative to the default (auto): it needs no flag, keeping the file's pose does.
    assert om.DEFAULT_REQUEST == OrientRequest(mode="auto")
    assert om.flags(om.DEFAULT_REQUEST) == ""
    assert om.flags(_KEEP) == "--orient keep"
    assert om.flags(OrientRequest(face=2)) == "--face 2"
    r = om.with_turn(om.with_turn(OrientRequest(face=2), "z", 15), "x", 90)
    assert om.flags(r) == "--face 2 --rotate z=15 --rotate x=90"
    assert om.flags(om.with_turn(_KEEP, "x", 90)) == "--orient keep --rotate x=90"
    assert om.flags(om.with_turn(om.DEFAULT_REQUEST, "x", 90)) == "--rotate x=90"


def _loaded(tmp_path, mesh=None, name="part.stl"):
    return om.load_pose_model(_stl(tmp_path, mesh, name))


def test_editor_opens_on_the_default_pose(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path))
    assert ed.request == om.DEFAULT_REQUEST and ed.plan.face == 1
    assert ed.result() == om.DEFAULT_REQUEST


def test_next_face_from_exported_goes_to_face_one(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path), _KEEP)
    assert ed.plan.face is None or ed.plan.face != 1
    ed.next_face()
    assert ed.request.face == 1
    assert ed.result() == OrientRequest(face=1)


def test_next_face_from_auto_moves_past_face_one(tmp_path):
    # Auto already shows face 1, so f goes on to the next one.
    ed = om.PoseEditor(_loaded(tmp_path, box(60, 40, 3)))
    assert ed.plan.face == 1
    ed.next_face()
    assert ed.request.face == 2


def test_prev_face_goes_to_last_and_cycles_wrap(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path))
    n = ed.face_count
    assert n > 1
    ed.next_face(-1)
    assert ed.request.face == n
    ed.next_face()
    assert ed.request.face == 1
    ed.next_face(-1)
    assert ed.request.face == n
    ed.next_face(1)
    assert ed.request.face == 1


def test_turn_auto_reset_and_view(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path))
    ed.turn("z", 90)
    assert ed.request.rotations == (("z", 90.0),) and ed.request.mode == "auto"
    ed.auto()
    assert ed.request == om.DEFAULT_REQUEST
    ed.reset()
    assert ed.result() == _KEEP
    assert ed.describe() == "as exported"
    seen = [ed.view]
    for _ in range(len(om.VIEW_ORDER)):
        ed.cycle_view()
        seen.append(ed.view)
    assert seen[0] == seen[-1]
    assert set(seen) == set(om.VIEW_ORDER)


def test_result_is_always_the_pose_on_screen(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path))
    ed.turn("z", 90)
    ed.turn("z", -90)
    assert ed.result() == om.DEFAULT_REQUEST
    ed.reset()
    assert ed.result() == _KEEP


def test_facts_say_how_the_pose_turned(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path))
    assert dict(ed.facts())["Turned"].endswith("about X")
    ed.reset()
    assert dict(ed.facts())["Turned"] == "no, as exported"


def test_facts_note_row_for_edge_rest_and_cli_row(tmp_path):
    mesh = box(10, 10, 10).rotated(rotation_about("x", 45))
    mesh = mesh.translated((0.0, 0.0, -min(mesh.zs)))
    ed = om.PoseEditor(_loaded(tmp_path, mesh))
    assert "Note" not in dict(ed.facts())  # auto lays it down
    ed.reset()
    rows = dict(ed.facts())
    assert "Note" in rows
    assert rows["CLI"] == "--orient keep"
    ed.next_face()
    rows = dict(ed.facts())
    assert "Note" not in rows
    assert rows["CLI"] == om.flags(ed.request) == "--face 1"
    ed.turn("z", 15)
    assert dict(ed.facts())["CLI"] == "--face 1 --rotate z=15"


def test_facts_plain_box_has_no_note(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path, box(20, 20, 20)))
    rows = dict(ed.facts())
    assert "Note" not in rows
    assert rows["Stands"] == "yes"


def test_face_rows_flag_current_and_fit_panel(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path), _KEEP)
    ed.next_face()
    rows = ed.face_rows()
    assert len(rows) == ed.face_count
    assert [n for n, (cur, _l) in enumerate(rows, 1) if cur] == [1]
    assert all(len(line) <= 38 for _c, line in rows)
    assert "auto" in rows[0][1]
    assert len(om.FACE_HEADER) <= 38


def test_face_rows_tags_exported_pose(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path, box(60, 40, 3)))
    assert "auto" in ed.face_rows()[0][1]


def test_start_request_with_missing_face_falls_back(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path), OrientRequest(face=999))
    assert ed.request == om.DEFAULT_REQUEST


def test_start_request_with_valid_face_is_kept(tmp_path):
    ed = om.PoseEditor(_loaded(tmp_path), OrientRequest(face=2))
    assert ed.request.face == 2


def test_load_pose_model_refuses_sliced_3mf(tmp_path):
    with pytest.raises(MeshError, match="already sliced"):
        om.load_pose_model(_sliced_3mf(tmp_path))


def test_load_pose_model_refuses_two_build_items(tmp_path):
    path = write_3mf(tmp_path / "two.3mf", {"1": box()}, [("1", None), ("1", None)])
    with pytest.raises(MeshError, match="exactly one"):
        om.load_pose_model(path)


def test_load_pose_model_refuses_unsupported_extension(tmp_path):
    p = tmp_path / "model.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(MeshError, match="can turn"):
        om.load_pose_model(str(p))


# --- model_view --------------------------------------------------------------


def _img(width, height, fn):
    img = Image.blank(width, height)
    for y in range(height):
        for x in range(width):
            r, g, b = fn(x, y)
            o = 3 * (y * width + x)
            img.pixels[o : o + 3] = bytes((r, g, b))
    return img


@pytest.mark.parametrize("height", [1, 2, 3, 4, 5])
def test_half_blocks_shape(height):
    text = half_blocks(_img(4, height, lambda x, y: (10 * y, 0, 0)))
    lines = text.plain.split("\n")
    assert len(lines) == (height + 1) // 2
    assert all(len(line) == 4 for line in lines)
    assert set(text.plain) <= {"▀", "\n"}


def test_half_blocks_upper_is_foreground_lower_is_background():
    text = half_blocks(_img(1, 2, lambda x, y: (255, 0, 0) if y == 0 else (0, 0, 255)))
    style = text.spans[0].style
    assert style.color.get_truecolor().hex == "#ff0000"
    assert style.bgcolor.get_truecolor().hex == "#0000ff"


def test_half_blocks_odd_last_row_pairs_with_itself():
    text = half_blocks(_img(1, 3, lambda x, y: (0, 255, 0) if y == 2 else (9, 9, 9)))
    style = text.spans[-1].style
    assert style.color.get_truecolor().hex == "#00ff00"
    assert style.bgcolor.get_truecolor().hex == "#00ff00"


def test_draw_model_sized_to_cells():
    text = draw_model(box(10, 10, 10), rotation_about("z", 0), 20, 6)
    assert isinstance(text, Text)
    lines = text.plain.split("\n")
    assert len(lines) == 6
    assert all(len(line) == 20 for line in lines)
    tiny = draw_model(box(10, 10, 10), rotation_about("z", 0), 0, 0)
    assert len(tiny.plain.split("\n")) == 1
    assert len(tiny.plain) == 2


# --- services ----------------------------------------------------------------


def test_needs_fetch(tmp_path):
    assert needs_fetch("https://example.com/a.stl")
    assert needs_fetch("http://example.com/a")
    assert needs_fetch(str(tmp_path / "a.zip"))
    assert needs_fetch(str(tmp_path / "A.ZIP"))
    assert not needs_fetch(str(tmp_path / "a.stl"))


def test_carry_fetched_model_moves_inside_old_workdir(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    model = old / "m.stl"
    model.write_text("x", encoding="utf-8")
    moved = carry_fetched_model(str(model), str(old), str(new))
    assert moved == os.path.join(str(new), "m.stl")
    assert os.path.isfile(moved)
    assert not model.exists()


def test_carry_fetched_model_outside_or_missing(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    outside = tmp_path / "local.stl"
    outside.write_text("x", encoding="utf-8")
    assert carry_fetched_model(str(outside), str(old), str(new)) is None
    assert outside.exists()
    assert carry_fetched_model(str(old / "gone.stl"), str(old), str(new)) is None


def test_pipeline_fetch_success(tmp_path):
    dl = _downloader()
    svc = PipelineService(GoSteps(download=dl))
    work = tmp_path / "w"
    work.mkdir()
    res = svc.fetch(_args(), source="https://example.com/x.stl", workdir=str(work))
    assert res.ok
    assert res.model_path == os.path.join(str(work), "dl.stl")
    assert len(dl.calls) == 1


def test_pipeline_fetch_failure(tmp_path):
    svc = PipelineService(GoSteps(download=Recorder(raises=BambuError("no route", exit_code=4))))
    work = tmp_path / "w"
    work.mkdir()
    res = svc.fetch(_args(), source="https://example.com/x.stl", workdir=str(work))
    assert not res.ok
    assert "no route" in res.error
    assert work.exists()


def test_pipeline_fetch_unexpected_error(tmp_path):
    svc = PipelineService(GoSteps(download=Recorder(raises=OSError("disk gone"))))
    res = svc.fetch(_args(), source="https://example.com/x.stl", workdir=str(tmp_path))
    assert not res.ok
    assert res.error


def test_load_pose_model_step_success_names_after_step(tmp_path):
    step = tmp_path / "widget.step"
    step.write_text("ISO-10303", encoding="utf-8")
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    seen = []

    def convert(path):
        seen.append(path)
        out = conv_dir / "tmp123.stl"
        write_stl(str(out), box(10, 10, 10))
        return str(out), True

    svc = PipelineService(GoSteps(), convert_step=convert)
    loaded, error = svc.load_pose_model(str(step))
    assert error is None
    assert loaded.name == "widget.step"
    assert seen == [str(step)]
    assert not conv_dir.exists()


def test_load_pose_model_step_converter_failure(tmp_path):
    step = tmp_path / "widget.stp"
    step.write_text("x", encoding="utf-8")
    svc = PipelineService(GoSteps(), convert_step=lambda path: (None, False))
    loaded, error = svc.load_pose_model(str(step))
    assert loaded is None
    assert "STEP" in error


def test_load_pose_model_unreadable_stl(tmp_path):
    bad = tmp_path / "bad.stl"
    bad.write_text("solid x\nendsolid x\n", encoding="utf-8")
    loaded, error = PipelineService(GoSteps()).load_pose_model(str(bad))
    assert loaded is None
    assert isinstance(error, str) and error


def test_load_pose_model_missing_file(tmp_path):
    loaded, error = PipelineService(GoSteps()).load_pose_model(str(tmp_path / "nope.stl"))
    assert loaded is None
    assert error


def _prepare(svc, tmp_path, workdir, model_path):
    return svc.prepare(
        _args(),
        source="https://example.com/x.stl",
        material="PLA",
        quality="standard",
        supports=False,
        workdir=str(workdir),
        model_path=model_path,
    )


def test_failed_reslice_keeps_workdir_with_fetched_model(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    model = work / "dl.stl"
    write_stl(str(model), box(10, 10, 10))
    svc = PipelineService(GoSteps(slice=Recorder(raises=BambuError("boom", exit_code=4))))
    res = _prepare(svc, tmp_path, work, str(model))
    assert not res.ok
    assert work.exists()
    assert model.exists()


def test_failed_slice_without_model_path_removes_workdir(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    dl = _downloader()
    svc = PipelineService(GoSteps(download=dl, slice=Recorder(raises=BambuError("boom", exit_code=4))))
    res = _prepare(svc, tmp_path, work, None)
    assert not res.ok
    assert not work.exists()


# --- pilot flows -------------------------------------------------------------


async def _orient(pilot, screen):
    screen.action_orient()
    await _settle(pilot)


async def _submit(pilot, screen):
    screen.query_one("#prepare-button", Button).press()
    await _settle(pilot)


@pytest.mark.parametrize("via", ["button", "action"])
async def test_orient_opens_screen_for_local_stl(tmp_path, via):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    app = PlateApp(
        _args(),
        _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path))),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        if via == "button":
            screen.query_one("#orient-button", Button).press()
        else:
            screen.action_orient()
        await _settle(pilot)
        assert isinstance(app.screen, OrientScreen)
        assert app.screen.editor is not None
        assert "part.stl" in app.screen.query_one("#orient-view").border_title
        facts = _text(app.screen.query_one("#orient-facts", Static))
        assert "auto" in facts and "about X" in facts  # opens on the default pose
        await pilot.press("escape")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)


async def test_o_key_opens_orient_when_input_not_focused(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        screen.set_focus(None)
        await pilot.press("o")
        await _settle(pilot)
        assert isinstance(app.screen, OrientScreen)


async def test_face_then_enter_slices_with_that_face(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    slicer = _slicer(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _orient(pilot, screen)
        await pilot.press("f")  # auto shows face 1, so f moves on to face 2
        await _settle(pilot)
        assert app.screen.editor.request.face == 2
        assert "--face 2" in _text(app.screen.query_one("#orient-facts", Static))
        await pilot.press("enter")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert "Orientation: face 2" in _text(screen.query_one("#orient-summary", Static))
        await _submit(pilot, screen)
        assert len(slicer.calls) == 1
        ns = slicer.calls[0]
        assert ns.face == 2
        assert ns.orient is None
        assert ns.orient_preview is False  # the TUI draws the pose itself


async def test_keys_turn_spin_auto_reset_view(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _orient(pilot, screen)
        orient = app.screen
        await pilot.press("x", "x", "X")
        assert orient.editor.request.rotations == (("x", 90.0),)
        await pilot.press("y", "Y", "z", "Z")
        assert orient.editor.request.rotations == (("x", 90.0),)
        await pilot.press("right_square_bracket", "right_square_bracket", "left_square_bracket")
        assert orient.editor.request.rotations == (("x", 90.0), ("z", 15.0))
        await pilot.press("F")
        assert orient.editor.request.face == orient.editor.face_count
        await pilot.press("a")
        assert orient.editor.request.mode == "auto"
        await pilot.press("v")
        assert orient.editor.view == "front"
        await pilot.press("0")
        assert orient.editor.result() == OrientRequest(mode="keep")
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert _text(screen.query_one("#orient-summary", Static)) == "Orientation: as exported"
        assert screen.orientation == OrientRequest(mode="keep")


async def test_turns_reach_slicer_as_rotate(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    slicer = _slicer(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _orient(pilot, screen)
        await pilot.press("z", "enter")
        await _settle(pilot)
        await _submit(pilot, screen)
        ns = slicer.calls[0]
        assert ns.rotate == [("z", 90.0)]
        assert ns.face is None


async def test_escape_keeps_previous_pose(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _orient(pilot, screen)
        await pilot.press("f", "enter")
        await _settle(pilot)
        assert screen.orientation.face == 2
        await _orient(pilot, screen)
        assert app.screen.editor.request.face == 2  # reopened on the chosen pose
        await pilot.press("f", "escape")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert screen.orientation.face == 2
        assert "face 2" in _text(screen.query_one("#orient-summary", Static))


def test_cancelled_sentinel_repr():
    assert repr(CANCELLED) == "CANCELLED"


async def test_changing_pose_after_prepare_clears_preview(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    slicer = _slicer(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        assert screen.result is not None
        assert not screen.query_one("#print-button", Button).disabled
        assert _text(screen.query_one("#preview", Static))
        await _orient(pilot, screen)
        await pilot.press("f", "enter")
        await _settle(pilot)
        assert screen.result is None
        assert screen.query_one("#print-button", Button).disabled
        assert _text(screen.query_one("#preview", Static)) == ""
        assert "prepare again" in _text(screen.query_one("#prepare-status", Static))


async def test_unchanged_pose_keeps_preview(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        await _orient(pilot, screen)
        await pilot.press("enter")
        await _settle(pilot)
        assert screen.result is not None
        assert not screen.query_one("#print-button", Button).disabled


async def test_url_source_fetched_once_and_reused(tmp_path):
    _install_ready_settings(tmp_path)
    dl = _downloader()
    slicer = _slicer(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=dl, slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = "https://example.com/model.stl"
        await _orient(pilot, screen)
        assert isinstance(app.screen, OrientScreen)
        assert len(dl.calls) == 1
        await pilot.press("f", "enter")
        await _settle(pilot)
        first_workdir = screen._workdir
        assert first_workdir and os.path.isdir(first_workdir)
        await _submit(pilot, screen)
        assert len(dl.calls) == 1
        assert len(slicer.calls) == 1
        assert slicer.calls[0].face == 2
        assert screen.result is not None
        # Second prepare: still one download, and the first workdir is replaced.
        await _submit(pilot, screen)
        assert len(dl.calls) == 1
        assert len(slicer.calls) == 2
        assert screen.result is not None
        assert screen._workdir != first_workdir or not os.path.isdir(first_workdir)
        assert os.path.isdir(screen._workdir)


async def test_url_second_prepare_removes_first_workdir(tmp_path):
    _install_ready_settings(tmp_path)
    dl = _downloader()
    app = PlateApp(_args(), _deps(GoSteps(download=dl, slice=_slicer(tmp_path))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = "https://example.com/model.stl"
        await _submit(pilot, screen)
        first = screen._workdir
        assert os.path.isdir(first)
        await _submit(pilot, screen)
        assert len(dl.calls) == 1
        assert screen._workdir != first
        assert not os.path.exists(first)


async def test_url_fetch_failure_shows_status(tmp_path):
    _install_ready_settings(tmp_path)
    dl = Recorder(raises=BambuError("no route to host", exit_code=4))
    app = PlateApp(_args(), _deps(GoSteps(download=dl, slice=Recorder())))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = "https://example.com/model.stl"
        await _orient(pilot, screen)
        assert isinstance(app.screen, PrepareScreen)
        assert "no route to host" in _text(screen.query_one("#prepare-status", Static))
        assert not screen.query_one("#prepare-button", Button).disabled


async def test_presliced_result_disables_orient_button(tmp_path):
    _install_ready_settings(tmp_path)
    ready = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    slicer = Recorder()
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = ready
        await _submit(pilot, screen)
        assert screen.result is not None
        assert screen.query_one("#orient-button", Button).disabled
        assert "orientation is fixed" in _text(screen.query_one("#orient-summary", Static))
        screen.action_orient()
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert slicer.calls == []


async def test_changing_source_drops_pose(tmp_path):
    _install_ready_settings(tmp_path)
    a = _stl(tmp_path, name="a.stl")
    b = _stl(tmp_path, box(20, 20, 20), name="b.stl")
    slicer = _slicer(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer)))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = a
        await _orient(pilot, screen)
        await pilot.press("f", "enter")
        await _settle(pilot)
        assert screen.orientation is not None
        screen.query_one("#source-input", Input).value = b
        await _submit(pilot, screen)
        assert screen.orientation is None
        ns = slicer.calls[0]
        assert ns.face is None
        assert ns.orient is None  # the default pose
        assert _text(screen.query_one("#orient-summary", Static)) == ""


async def test_unloadable_model_shows_error_and_enter_keeps_pose(tmp_path):
    _install_ready_settings(tmp_path)
    bad = tmp_path / "bad.stl"
    bad.write_text("solid x\nendsolid x\n", encoding="utf-8")
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder())))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = str(bad)
        screen.orientation = OrientRequest(face=2)
        screen._orientation_source = str(bad)
        await _orient(pilot, screen)
        orient = app.screen
        assert isinstance(orient, OrientScreen)
        assert orient.editor is None
        assert "Esc" in _text(orient.query_one("#orient-facts", Static))
        await pilot.press("f", "x")  # ignored without a model
        await pilot.press("enter")
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert screen.orientation == OrientRequest(face=2)


async def test_orientation_row_in_preview(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    summary = {"mode": "face", "face": 1, "rotations": ["z=15"], "height_mm": 3.0, "stable": True}
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path, summary))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        preview = _text(screen.query_one("#preview", Static))
        assert "Orientation" in preview
        assert "face 1, z+15°" in preview
        assert "3.0 mm tall" in preview


_TURNED = {
    "mode": "auto",
    "changed": True,
    "face": 1,
    "rotations": [],
    "turned": "-90° about X",
    "rotation": [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
    "height_mm": 3.0,
    "stable": True,
}


async def _prepared_with(tmp_path, summary, source=None):
    """Prepare a local STL whose (fake) slice reports ``summary``; return (app, screen) inside run_test."""
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=_slicer(tmp_path, summary))))
    return app, source or _stl(tmp_path)


async def test_prepare_draws_the_auto_pose_and_says_how_it_turned(tmp_path):
    app, stl = await _prepared_with(tmp_path, _TURNED)
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        await _settle(pilot)
        view = screen.query_one("#pose-view")
        assert view.has_class("shown") and view.display
        assert "\u2580" in _text(view)
        line = screen.query_one("#pose-line", Static)
        assert _text(line) == "Auto turned this model -90° about X · o, then 0, keeps the file's orientation"
        assert not line.has_class("warning")
        # Before anything can be confirmed: the print button is below the picture.
        assert not screen.query_one("#print-button", Button).disabled


async def test_pose_picture_goes_when_the_pose_changes(tmp_path):
    app, stl = await _prepared_with(tmp_path, _TURNED)
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        await _orient(pilot, screen)
        await pilot.press("0", "enter")
        await _settle(pilot)
        assert not screen.query_one("#pose-view").has_class("shown")
        assert _text(screen.query_one("#pose-line", Static)) == ""


async def test_kept_pose_on_an_edge_warns_by_the_picture(tmp_path):
    summary = {"mode": "keep", "rotations": [], "height_mm": 14.0, "stable": False, "warning": "rests on a point"}
    app, stl = await _prepared_with(tmp_path, summary)
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        line = screen.query_one("#pose-line", Static)
        assert line.has_class("warning")
        assert "o, then a" in _text(line)
        # No rotation reported: nothing to draw, the line still says it.
        assert not screen.query_one("#pose-view").has_class("shown")


async def test_presliced_result_shows_no_pose(tmp_path):
    _install_ready_settings(tmp_path)
    ready = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder())))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = ready
        await _submit(pilot, screen)
        assert not screen.query_one("#pose-view").has_class("shown")
        assert _text(screen.query_one("#pose-line", Static)) == ""


async def test_failed_prepare_clears_the_pose(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _stl(tmp_path)
    calls = {"n": 0}
    sliced = _sliced_3mf(tmp_path)

    def run(ns):
        calls["n"] += 1
        if calls["n"] > 1:
            raise BambuError("slicer fell over", exit_code=4)
        ns.orientation_result = _TURNED
        return sliced

    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(side_effect=run))))
    async with app.run_test(size=(120, 36)) as pilot:
        await _settle(pilot)
        screen = await _open_prepare(pilot)
        screen.query_one("#source-input", Input).value = stl
        await _submit(pilot, screen)
        assert screen.query_one("#pose-view").has_class("shown")
        await _submit(pilot, screen)
        assert "slicer fell over" in _text(screen.query_one("#prepare-status", Static))
        assert not screen.query_one("#pose-view").has_class("shown")


@pytest.mark.parametrize(
    ("summary", "line", "warns"),
    [
        (None, None, None),
        ({"mode": "keep", "presliced": True}, None, None),
        (_TURNED, "Auto turned this model -90° about X · o, then 0, keeps the file's orientation", False),
        (
            {"mode": "auto", "turned": None, "rotations": [], "stable": True},
            "Auto: it already lies on its best face, as exported · o changes it",
            False,
        ),
        (
            {"mode": "auto", "turned": "90° about Y", "rotations": [], "stable": False},
            "Auto turned this model 90° about Y · o, then 0, keeps the file's orientation · it may tip over",
            True,
        ),
        ({"mode": "keep", "rotations": [], "stable": True}, "As exported · o changes it", False),
        ({"mode": "keep", "rotations": [], "stable": False}, "As exported · it may tip over · o changes it", True),
        (
            {"mode": "keep", "rotations": [], "stable": False, "warning": "rests on a point"},
            "As exported it rests on a point or an edge · o, then a, lays it on its best face",
            True,
        ),
        (
            {"mode": "keep", "warning": "two.3mf places 2 objects"},
            "Auto could not read this model, so it is sliced as exported.",
            True,
        ),
        (
            {"mode": "face", "face": 2, "rotations": ["z=15"], "turned": "15° about Z", "stable": True},
            "Face 2 + z=15: turned 15° about Z from the file · o changes it",
            False,
        ),
        (
            {"mode": "keep", "rotations": ["x=0"], "turned": None, "stable": True},
            "As exported + x=0: the file's own pose · o changes it",
            False,
        ),
        (
            {"mode": "auto", "rotations": ["x=90"], "turned": "90° about X", "stable": True},
            "Auto + x=90: turned 90° about X from the file · o changes it",
            False,
        ),
    ],
)
def test_pose_line(summary, line, warns):
    said = om.pose_line(summary)
    if line is None:
        assert said is None
    else:
        assert said == (line, warns)


def test_load_pose_model_is_cached_until_the_file_changes(tmp_path):
    svc = PipelineService(GoSteps())
    path = _stl(tmp_path)
    first, _ = svc.load_pose_model(path)
    again, _ = svc.load_pose_model(path)
    assert first is not None and again is first
    write_stl(path, box(20, 20, 20))
    os.utime(path, ns=(1, 1))
    changed, _ = svc.load_pose_model(path)
    assert changed is not first and changed.mesh.triangle_count == 12
    missing, error = svc.load_pose_model(str(tmp_path / "gone.stl"))
    assert missing is None and error


def test_draw_views_side_by_side_with_captions():
    from bambu_cli.tui.widgets.model_view import draw_views

    text = draw_views(box(20, 20, 5), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), 40, 6)
    lines = text.plain.split("\n")
    assert len(lines) == 6
    assert lines[-1].startswith("from above") and "from the front" in lines[-1]
    # Two 19-cell pictures and a 2-cell gap on every picture row.
    assert all(len(line) == 40 for line in lines[:-1])
    assert all(line[19:21] == "  " for line in lines[:-1])
