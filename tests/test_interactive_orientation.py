"""The pose a front-end chose reaches the slice step, and the preview says which pose was sliced."""

from __future__ import annotations

import os
import zipfile

import pytest

from bambu_cli import context as _context
from bambu_cli import utils
from bambu_cli.context import RuntimeContext, Settings
from bambu_cli.errors import BambuError
from bambu_cli.interactive import core
from bambu_cli.mesh import OrientRequest
from bambu_cli.slicer import SliceResult


@pytest.fixture(autouse=True)
def _ready_settings(tmp_path):
    saved = _context.get_current()
    _context.set_current(
        RuntimeContext(settings=Settings(printer_ip="192.168.1.23", printer_model="P1S", nozzle_size="0.4"))
    )
    yield
    _context.set_current(saved)
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None


class Recorder:
    def __init__(self, return_value=None, summary=None):
        self.calls = []
        self.return_value = return_value
        self.summary = summary

    def __call__(self, ns=None, **kwargs):
        self.calls.append(ns)
        if self.summary is not None:
            return SliceResult(self.return_value, orientation=self.summary)
        return self.return_value


def _stl(tmp_path, name="part.stl"):
    path = tmp_path / name
    path.write_text("solid p\nendsolid p\n", encoding="utf-8")
    return str(path)


def _three_mf(tmp_path, name, *, sliced):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
        if sliced:
            zf.writestr("Metadata/plate_1.gcode", "G28\n")
    return str(path)


def _workdir(tmp_path):
    workdir = str(tmp_path / "work")
    os.makedirs(workdir, exist_ok=True)
    return workdir


def test_without_a_pose_the_slice_namespace_asks_for_nothing(tmp_path):
    slicer = Recorder(return_value=str(tmp_path / "out.3mf"))
    state = core.WizardState(source=_stl(tmp_path))
    core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=slicer), state, _workdir(tmp_path))
    ns = slicer.calls[0]
    assert (ns.orient, ns.face, ns.rotate) == (None, None, None)
    assert state.orientation_summary is None


def test_a_face_and_turns_reach_the_slicer_and_its_summary_is_kept(tmp_path):
    summary = {"mode": "face", "changed": True, "face": 2, "rotations": ["z=15"], "height_mm": 3.0}
    slicer = Recorder(return_value=str(tmp_path / "out.3mf"), summary=summary)
    state = core.WizardState(source=_stl(tmp_path), orientation=OrientRequest(face=2, rotations=(("z", 15.0),)))
    core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=slicer), state, _workdir(tmp_path))
    ns = slicer.calls[0]
    assert ns.orient is None and ns.face == 2 and ns.rotate == [("z", 15.0)]
    assert state.orientation_summary == summary


def test_auto_reaches_the_slicer_as_orient_auto(tmp_path):
    slicer = Recorder(return_value=str(tmp_path / "out.3mf"))
    state = core.WizardState(source=_stl(tmp_path), orientation=OrientRequest(mode="auto"))
    core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=slicer), state, _workdir(tmp_path))
    assert slicer.calls[0].orient == "auto" and slicer.calls[0].face is None


def test_an_already_fetched_model_is_sliced_again_without_downloading(tmp_path):
    download = Recorder(return_value="unused.stl")
    slicer = Recorder(return_value=str(tmp_path / "out.3mf"))
    model = _stl(tmp_path)
    state = core.WizardState(source="https://example.com/part.stl")
    returned = core.run_prepare_pipeline(
        core.GoSteps(download=download, slice=slicer), state, _workdir(tmp_path), model_path=model
    )
    assert returned == model
    assert download.calls == []
    assert slicer.calls[0].file == model


def test_fetch_model_downloads_without_slicing(tmp_path):
    model = _stl(tmp_path)
    download = Recorder(return_value=model)
    slicer = Recorder()
    state = core.WizardState(source="https://example.com/part.stl")
    assert core.fetch_model(core.GoSteps(download=download, slice=slicer), state, _workdir(tmp_path)) == model
    assert slicer.calls == []


def test_an_unsliced_3mf_project_is_sliced(tmp_path):
    project = _three_mf(tmp_path, "project.3mf", sliced=False)
    slicer = Recorder(return_value=str(tmp_path / "out.3mf"))
    state = core.WizardState(source=project)
    core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=slicer), state, _workdir(tmp_path))
    assert slicer.calls[0].file == project
    assert state.sliced is True


def test_a_presliced_3mf_is_never_turned(tmp_path):
    ready = _three_mf(tmp_path, "ready.3mf", sliced=True)
    slicer = Recorder()
    state = core.WizardState(source=ready, orientation=OrientRequest(rotations=(("x", 90.0),)))
    with pytest.raises(BambuError, match="never re-orients"):
        core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=slicer), state, _workdir(tmp_path))
    assert slicer.calls == []


def test_a_presliced_3mf_passes_through_when_the_pose_is_kept(tmp_path):
    ready = _three_mf(tmp_path, "ready.3mf", sliced=True)
    state = core.WizardState(source=ready, orientation=OrientRequest())
    core.run_prepare_pipeline(core.GoSteps(download=Recorder(), slice=Recorder()), state, _workdir(tmp_path))
    assert state.sliced is False and state.printable_path == ready


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (None, None),
        ({"mode": "keep", "presliced": True}, None),
        ({"mode": "keep", "height_mm": 40.0, "stable": True}, "as exported  ·  40.0 mm tall"),
        (
            {"mode": "keep", "height_mm": 30.0, "stable": False, "warning": "rests on a point"},
            "as exported  ·  30.0 mm tall  ·  rests on a point or an edge: it may not print",
        ),
        ({"mode": "auto", "face": 1, "height_mm": 3.0}, "auto (face 1)  ·  3.0 mm tall"),
        (
            {"mode": "auto", "face": 1, "turned": "90° about X", "height_mm": 3.0},
            "auto (face 1)  ·  turned 90° about X  ·  3.0 mm tall",
        ),
        ({"mode": "auto", "face": 1, "turned": None}, "auto (face 1)  ·  lies as exported"),
        ({"mode": "keep", "warning": "two.3mf places 2 objects"}, "as exported  ·  auto could not choose a face"),
        ({"mode": "auto"}, "auto"),
        ({"mode": "face", "face": 2, "rotations": ["z=15", "x=-90"]}, "face 2, z+15°, x-90°"),
        ({"mode": "face", "face": 3, "stable": False, "height_mm": 9.25}, "face 3  ·  9.2 mm tall  ·  may tip over"),
        ({"mode": "keep", "rotations": ["odd"]}, "as exported, odd"),
    ],
)
def test_orientation_line(summary, expected):
    assert core.orientation_line(summary) == expected


def test_preview_rows_show_the_sliced_pose_after_the_material(tmp_path):
    state = core.WizardState(
        printable_path=str(tmp_path / "missing.3mf"),
        sliced=True,
        orientation_summary={"mode": "auto", "face": 1, "height_mm": 3.0},
    )
    rows = core.preview_rows(state, str(tmp_path / "part.stl"))
    labels = [label for label, _value in rows]
    assert labels.index("Orientation") == labels.index("Material") + 1
    assert dict(rows)["Orientation"] == "auto (face 1)  ·  3.0 mm tall"


def test_preview_rows_have_no_orientation_for_a_presliced_file(tmp_path):
    ready = _three_mf(tmp_path, "ready.3mf", sliced=True)
    state = core.WizardState(printable_path=ready, sliced=False, orientation_summary={"mode": "keep"})
    assert "Orientation" not in dict(core.preview_rows(state, ready))
