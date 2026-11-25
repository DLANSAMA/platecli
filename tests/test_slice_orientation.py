"""The orientation step of ``slice`` and ``job``: pose chosen before OrcaSlicer runs."""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from array import array

import pytest

from bambu_cli.cliparse import build_parser
from bambu_cli.context import current_settings
from bambu_cli.errors import BambuError
from bambu_cli.mesh import Mesh, load_model
from bambu_cli.mesh.formats import write_stl
from bambu_cli.slicer import cmd_slice
from bambu_cli.slicer import orientation as orientation_mod
from bambu_cli.constants import DEFAULT_ORIENT_MODE
from bambu_cli.mesh import OrientRequest
from bambu_cli.slicer.orientation import (
    apply_orientation,
    asks_to_turn,
    explicit_request,
    orient_request,
    plan_summary,
    request_flags,
)
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher

# --- tiny synthetic shapes ----------------------------------------------------

_BOX_TRIS = [
    (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
    (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5),
]  # fmt: skip


def _mesh(verts, tris) -> Mesh:
    return Mesh(
        array("d", [v[0] for v in verts]),
        array("d", [v[1] for v in verts]),
        array("d", [v[2] for v in verts]),
        array("l", [i for t in tris for i in t]),
    )


def _box_parts(w, d, h, origin=(0.0, 0.0, 0.0)):
    ox, oy, oz = origin
    verts = [(ox + (i & 1) * w, oy + ((i >> 1) & 1) * d, oz + ((i >> 2) & 1) * h) for i in range(8)]
    return verts, _BOX_TRIS


def box(w, d, h) -> Mesh:
    return _mesh(*_box_parts(w, d, h))


def inverted_pyramid(a=10.0, h=10.0) -> Mesh:
    """Apex down at z=0, square base (side 2a) at z=h: rests on a point as exported."""
    verts = [(-a, -a, h), (a, -a, h), (a, a, h), (-a, a, h), (0.0, 0.0, 0.0)]
    tris = [(0, 1, 2), (0, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)]
    return _mesh(verts, tris)


def write_model(path, mesh) -> str:
    write_stl(str(path), mesh)
    return str(path)


_MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def write_3mf(path, meshes, *, sliced=False) -> str:
    """A 3MF with one object and one build item per mesh."""
    objs, build = [], []
    for n, mesh in enumerate(meshes, 1):
        vs = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in zip(mesh.xs, mesh.ys, mesh.zs, strict=True))
        ts = "".join(
            f'<triangle v1="{mesh.tris[i]}" v2="{mesh.tris[i + 1]}" v3="{mesh.tris[i + 2]}"/>'
            for i in range(0, len(mesh.tris), 3)
        )
        objs.append(
            f'<object id="{n}" type="model"><mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>'
        )
        build.append(f'<item objectid="{n}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
    xml = (
        f'<?xml version="1.0"?><model unit="millimeter" xmlns="{_MODEL_NS}">'
        f"<resources>{''.join(objs)}</resources><build>{''.join(build)}</build></model>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", xml)
        zf.writestr("Metadata/keep.config", "keep me")
        if sliced:
            zf.writestr("Metadata/plate_1.gcode", "G28\n")
    return str(path)


def ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def pose_of(path):
    """(z_min, height) of the model at ``path`` in world space."""
    lo, hi = load_model(str(path)).mesh.bounds()
    return lo[2], hi[2] - lo[2]


# --- orient_request / plan_summary --------------------------------------------


def test_orient_request_defaults_to_auto():
    assert DEFAULT_ORIENT_MODE == "auto"
    req = orient_request(ns())
    assert req.mode == "auto" and req.face is None and req.rotations == ()
    assert req.changes_pose
    assert explicit_request(ns()) is None


@pytest.mark.parametrize(
    ("flags", "turns"),
    [
        ({}, False),
        ({"orient": "keep"}, False),
        ({"orient": "keep", "rotate": [("z", 0.0)]}, False),
        ({"orient": "auto"}, True),
        ({"face": 2}, True),
        ({"rotate": [("z", 90.0)]}, True),
    ],
)
def test_asks_to_turn_counts_only_flags(flags, turns):
    assert asks_to_turn(ns(**flags)) is turns


def test_rotate_alone_stacks_on_the_default():
    req = orient_request(ns(rotate=[("x", 90.0)]))
    assert req.mode == DEFAULT_ORIENT_MODE and req.rotations == (("x", 90.0),)


@pytest.mark.parametrize(
    ("request_", "flags"),
    [
        (OrientRequest(mode="auto"), ""),
        (OrientRequest(mode="keep"), "--orient keep"),
        (OrientRequest(face=2, rotations=(("z", 15.0),)), "--face 2 --rotate z=15"),
        (OrientRequest(mode="keep", rotations=(("x", -90.0),)), "--orient keep --rotate x=-90"),
        (OrientRequest(mode="auto", rotations=(("y", 180.0),)), "--rotate y=180"),
    ],
)
def test_request_flags_are_relative_to_the_default(request_, flags):
    assert request_flags(request_) == flags


def test_orient_request_reads_flags():
    req = orient_request(ns(orient="auto", rotate=[("z", 90.0)]))
    assert req.mode == "auto" and req.rotations == (("z", 90.0),)
    assert orient_request(ns(face=2)).face == 2


@pytest.mark.parametrize("mode", ["auto", "keep"])
def test_orient_request_face_with_orient_is_refused(mode):
    with pytest.raises(BambuError) as caught:
        orient_request(ns(orient=mode, face=1))
    assert caught.value.failed_step == "validate"
    assert "--face" in str(caught.value) and f"--orient {mode}" in str(caught.value)


def test_plan_summary_without_plan():
    req = orient_request(ns(face=3, rotate=[("x", 90.0), ("z", 45.5)]))
    assert plan_summary(None, req) == {"mode": "face", "changed": False, "face": 3, "rotations": ["x=90", "z=45.5"]}


def test_plan_summary_presliced_flag():
    summary = plan_summary(None, OrientRequest(mode="keep"), presliced=True)
    assert summary["presliced"] is True and summary["mode"] == "keep"


def test_plan_summary_with_plan(tmp_path):
    from bambu_cli.mesh import plan_orientation

    req = orient_request(ns(orient="auto"))
    plan = plan_orientation(box(60, 40, 3), req)
    summary = plan_summary(plan, req)
    assert summary["mode"] == "auto" and summary["face"] == 1
    assert summary["height_mm"] == 3.0
    assert summary["size_mm"] == sorted(summary["size_mm"], reverse=True) or len(summary["size_mm"]) == 3
    assert summary["stable"] is True
    assert {"bed_contact_mm2", "overhang_mm2"} <= set(summary)
    assert "warning" not in summary
    # A plate already flat is not turned: no turn, the identity matrix.
    assert summary["turned"] is None
    assert summary["rotation"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


# --- apply_orientation --------------------------------------------------------


def test_non_orientable_extension_keep_passes_through(tmp_path):
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28\n")
    out = apply_orientation(ns(), str(gcode), str(tmp_path))
    assert out.path == str(gcode) and out.summary is None and not out.reoriented_3mf


@pytest.mark.parametrize("flags", [{"orient": "auto"}, {"face": 1}, {"rotate": [("z", 90.0)]}])
def test_non_orientable_extension_pose_change_refused(tmp_path, flags):
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28\n")
    with pytest.raises(BambuError) as caught:
        apply_orientation(ns(**flags), str(gcode), str(tmp_path), display_name="shown.gcode")
    assert caught.value.failed_step == "validate"
    assert "shown.gcode" in str(caught.value)


def test_sliced_3mf_keep_reports_presliced(tmp_path):
    path = write_3mf(tmp_path / "s.3mf", [box(10, 10, 10)], sliced=True)
    out = apply_orientation(ns(), path, str(tmp_path))
    assert out.path == path and out.summary["presliced"] is True and not out.reoriented_3mf


def test_sliced_3mf_with_keep_is_allowed(tmp_path):
    path = write_3mf(tmp_path / "s.3mf", [box(10, 10, 10)], sliced=True)
    out = apply_orientation(ns(orient="keep"), path, str(tmp_path))
    assert out.path == path and out.summary["presliced"] is True and out.summary["mode"] == "keep"


def test_sliced_3mf_pose_change_refused(tmp_path):
    path = write_3mf(tmp_path / "s.3mf", [box(10, 10, 10)], sliced=True)
    with pytest.raises(BambuError) as caught:
        apply_orientation(ns(orient="auto"), path, str(tmp_path))
    assert caught.value.failed_step == "validate"
    assert "never re-orients" in str(caught.value)


def test_keep_on_unstable_pose_warns_and_returns_original(tmp_path, caplog):
    import logging

    path = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    with caplog.at_level(logging.WARNING, logger="bambu"):
        out = apply_orientation(ns(orient="keep"), path, str(tmp_path))
    assert out.path == path
    assert out.summary["stable"] is False and out.summary["mode"] == "keep"
    assert "auto would lay it on face 1" in out.summary["warning"]
    assert any("drop --orient keep" in r.getMessage() for r in caplog.records)


def test_keep_on_stable_pose_has_no_warning(tmp_path):
    path = write_model(tmp_path / "box.stl", box(20, 20, 5))
    out = apply_orientation(ns(orient="keep"), path, str(tmp_path))
    assert out.path == path and "warning" not in out.summary and out.summary["changed"] is False


def test_keep_skips_analysis_of_a_huge_file(tmp_path, monkeypatch):
    path = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    monkeypatch.setattr(orientation_mod, "AUTO_ANALYSIS_MAX_BYTES", 10)
    out = apply_orientation(ns(orient="keep"), path, str(tmp_path))
    assert out.path == path
    assert out.summary == {"mode": "keep", "changed": False, "face": None, "rotations": []}


def test_keep_on_multi_object_3mf_skips_analysis(tmp_path):
    path = write_3mf(tmp_path / "two.3mf", [box(10, 10, 10), box(5, 5, 5)])
    out = apply_orientation(ns(orient="keep"), path, str(tmp_path))
    assert out.path == path and "height_mm" not in out.summary and "warning" not in out.summary


@pytest.mark.parametrize("flags", [{"orient": "keep"}, {}])
def test_keep_and_default_never_raise_on_a_corrupt_file(tmp_path, flags):
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"not a model")
    out = apply_orientation(ns(**flags), str(bad), str(tmp_path))
    assert out.path == str(bad) and out.summary["changed"] is False and out.summary["mode"] == "keep"
    zbad = tmp_path / "bad.3mf"
    zbad.write_bytes(b"not a zip")
    assert apply_orientation(ns(**flags), str(zbad), str(tmp_path)).summary["changed"] is False


def test_explicit_auto_on_a_corrupt_file_fails(tmp_path):
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"not a model")
    with pytest.raises(BambuError):
        apply_orientation(ns(orient="auto"), str(bad), str(tmp_path))


# --- the default (auto) ---------------------------------------------------------


def test_default_turns_an_inverted_pyramid_and_says_so(tmp_path, caplog):
    import logging

    work = tmp_path / "work"
    work.mkdir()
    src = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    with caplog.at_level(logging.INFO, logger="bambu"):
        out = apply_orientation(ns(), src, str(work))
    assert os.path.dirname(out.path) == str(work)
    assert out.summary["mode"] == "auto" and out.summary["changed"] is True and out.summary["face"] == 1
    assert out.summary["turned"] == "180° about X" or out.summary["turned"].startswith("180°")
    zmin, height = pose_of(out.path)
    assert zmin == pytest.approx(0, abs=1e-6) and height == pytest.approx(10, abs=1e-6)
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "Auto turned this model 180°" in said and "--orient keep slices it as exported" in said


def test_default_on_a_model_already_on_its_best_face_slices_the_file(tmp_path, caplog):
    import logging

    src = write_model(tmp_path / "box.stl", box(60, 40, 3))
    with caplog.at_level(logging.INFO, logger="bambu"):
        out = apply_orientation(ns(), src, str(tmp_path / "unused"))
    assert out.path == src and out.summary["changed"] is False and out.summary["turned"] is None
    assert any("already its best face" in r.getMessage() for r in caplog.records)


def test_default_on_a_huge_file_keeps_it_and_says_why(tmp_path, monkeypatch, caplog):
    import logging

    path = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    monkeypatch.setattr(orientation_mod, "AUTO_ANALYSIS_MAX_BYTES", 10)
    with caplog.at_level(logging.WARNING, logger="bambu"):
        out = apply_orientation(ns(), path, str(tmp_path))
    assert out.path == path and out.summary["mode"] == "keep" and out.summary["changed"] is False
    assert "too big" in out.summary["warning"] and "--orient auto" in out.summary["warning"]
    assert any("too big" in r.getMessage() for r in caplog.records)


def test_explicit_auto_on_a_huge_file_still_turns_it(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    path = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    monkeypatch.setattr(orientation_mod, "AUTO_ANALYSIS_MAX_BYTES", 10)
    assert apply_orientation(ns(orient="auto"), path, str(work)).summary["changed"] is True


def test_default_on_a_multi_object_3mf_keeps_it_and_says_why(tmp_path):
    path = write_3mf(tmp_path / "two.3mf", [box(10, 10, 10), box(5, 5, 5)])
    out = apply_orientation(ns(), path, str(tmp_path))
    assert out.path == path and out.summary["mode"] == "keep" and not out.reoriented_3mf
    assert "2 objects" in out.summary["warning"]


def test_default_on_an_unreadable_path_keeps_it(tmp_path):
    out = apply_orientation(ns(), str(tmp_path / "gone.stl"), str(tmp_path))
    assert out.summary["mode"] == "keep" and "could not read" in out.summary["warning"]


def test_rotate_alone_turns_the_auto_pose(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = write_model(tmp_path / "plate.stl", box(60, 3, 40))
    flat = apply_orientation(ns(), src, str(work))
    assert pose_of(flat.path)[1] == pytest.approx(3, abs=1e-6)
    stood = apply_orientation(ns(rotate=[("x", 90.0)]), src, str(work))
    assert stood.summary["mode"] == "auto" and stood.summary["rotations"] == ["x=90"]
    assert pose_of(stood.path)[1] > 30


def test_auto_writes_oriented_stl_into_work_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    out = apply_orientation(ns(orient="auto"), src, str(work))
    assert os.path.dirname(out.path) == str(work) and out.path.endswith("pyr.stl")
    assert out.summary["changed"] is True and out.summary["face"] == 1
    assert not out.reoriented_3mf
    zmin, height = pose_of(out.path)
    assert zmin == pytest.approx(0, abs=1e-6) and height == pytest.approx(10, abs=1e-6)
    assert out.summary["stable"] is True


def test_face_one_lays_a_standing_plate_flat(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = write_model(tmp_path / "plate.stl", box(60, 3, 40))
    out = apply_orientation(ns(face=1), src, str(work))
    assert pose_of(out.path)[1] == pytest.approx(3, abs=1e-6)
    assert out.summary["mode"] == "face"


def test_rotate_only_changes_the_pose(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = write_model(tmp_path / "plate.stl", box(60, 3, 40))
    out = apply_orientation(ns(orient="keep", rotate=[("x", 90.0)]), src, str(work))
    assert out.summary["mode"] == "keep" and out.summary["rotations"] == ["x=90"]
    zmin, height = pose_of(out.path)
    assert zmin == pytest.approx(0, abs=1e-6) and height == pytest.approx(3, abs=1e-6)


def test_missing_face_names_plate_orient(tmp_path):
    src = write_model(tmp_path / "box.stl", box(10, 10, 10))
    with pytest.raises(BambuError) as caught:
        apply_orientation(ns(face=99), src, str(tmp_path))
    assert "plate orient" in str(caught.value) and caught.value.failed_step == "validate"


def test_reoriented_single_object_3mf(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = write_3mf(tmp_path / "plate.3mf", [box(60, 3, 40)])
    out = apply_orientation(ns(face=1), src, str(work))
    assert out.reoriented_3mf is True
    assert out.path.endswith("plate.3mf") and os.path.dirname(out.path) == str(work)
    assert pose_of(out.path)[1] == pytest.approx(3, abs=1e-6)
    with zipfile.ZipFile(out.path) as zf:
        assert zf.read("Metadata/keep.config") == b"keep me"


# --- cmd_slice against the stub -----------------------------------------------


@pytest.fixture
def stub(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("ORCA_STUB_SCENARIO", raising=False)
    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    outdir = tmp_path / "out"
    outdir.mkdir()
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    def run(model, scenario="success", **overrides):
        monkeypatch.setenv("ORCA_STUB_SCENARIO", scenario)
        monkeypatch.setenv("ORCA_STUB_ARGV_DUMP", str(dump_dir / "argv.json"))
        monkeypatch.setenv("ORCA_STUB_MARKER", str(dump_dir / "marker.txt"))
        args = ns(
            file=str(model),
            output=str(outdir),
            quality="standard",
            copies=1,
            infill=15,
            pattern="3dhoneycomb",
            supports=False,
            nozzle_temp=220,
            bed_temp=60,
            filament="PLA Basic",
            json=False,
            threads=None,
            list_settings=False,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        run.args = args
        with settings_ctx(orca_slicer=launcher, profiles_dir=profiles):
            return cmd_slice(args)

    def argv():
        with open(dump_dir / "argv.json", encoding="utf-8") as fh:
            return json.load(fh)["argv"]

    run.argv = argv
    run.dump_dir = dump_dir
    run.marker = dump_dir / "marker.txt"
    return run


def test_argv_always_disables_orcaslicer_orienting(stub, tmp_path):
    model = write_model(tmp_path / "box.stl", box(20, 20, 5))
    stub(model)
    argv = stub.argv()
    assert argv[argv.index("--orient") + 1] == "0"


def test_stl_gets_arrange(stub, tmp_path):
    stub(write_model(tmp_path / "box.stl", box(20, 20, 5)))
    argv = stub.argv()
    assert argv[argv.index("--arrange") + 1] == "1"


def test_unreoriented_single_copy_3mf_is_not_arranged(stub, tmp_path):
    # Already on its best face, so auto leaves the file as it is.
    stub(write_3mf(tmp_path / "proj.3mf", [box(20, 20, 5)]))
    argv = stub.argv()
    assert "--arrange" not in argv and "--orient" in argv


def test_reoriented_3mf_is_arranged(stub, tmp_path):
    stub(write_3mf(tmp_path / "proj.3mf", [box(60, 3, 40)]), face=1)
    argv = stub.argv()
    assert argv[argv.index("--arrange") + 1] == "1"
    received = stub.dump_dir / "proj.3mf"
    assert pose_of(received)[1] == pytest.approx(3, abs=1e-6)


def test_auto_on_inverted_pyramid_reaches_stub_on_the_bed(stub, tmp_path):
    stub(write_model(tmp_path / "pyr.stl", inverted_pyramid()), orient="auto")
    zmin, height = pose_of(stub.dump_dir / "pyr.stl")
    assert zmin == pytest.approx(0, abs=1e-6) and height == pytest.approx(10, abs=1e-6)


def test_face_one_on_standing_plate_is_three_mm_tall(stub, tmp_path):
    stub(write_model(tmp_path / "plate.stl", box(60, 3, 40)), face=1)
    assert pose_of(stub.dump_dir / "plate.stl")[1] == pytest.approx(3, abs=1e-6)


def test_json_payload_carries_orientation(stub, tmp_path, capsys):
    from tests.test_slice_stub_integration import _last_json_object

    stub(write_model(tmp_path / "plate.stl", box(60, 3, 40)), face=1, json=True)
    orientation = _last_json_object(capsys.readouterr().out)["orientation"]
    assert orientation["mode"] == "face" and orientation["changed"] is True
    assert orientation["face"] == 1 and orientation["height_mm"] == 3.0


def test_orientation_result_is_left_on_the_namespace(stub, tmp_path):
    stub(write_model(tmp_path / "plate.stl", box(60, 3, 40)), face=1)
    assert stub.args.orientation_result["face"] == 1
    assert stub.args.orientation_result["height_mm"] == 3.0


def test_keep_mode_on_pyramid_warns_and_failure_suggests_auto(stub, tmp_path, caplog):
    import logging

    model = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    with caplog.at_level(logging.WARNING, logger="bambu"):
        with pytest.raises(BambuError) as caught:
            stub(model, scenario="missing_output", orient="keep")
    assert any("drop --orient keep" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert caught.value.next_command == ["slice", model, "--orient", "auto"]


def test_default_on_pyramid_reaches_stub_turned_and_failure_has_no_orient_hint(stub, tmp_path):
    model = write_model(tmp_path / "pyr.stl", inverted_pyramid())
    with pytest.raises(BambuError) as caught:
        stub(model, scenario="missing_output")
    assert caught.value.next_command != ["slice", model, "--orient", "auto"]
    zmin, height = pose_of(stub.dump_dir / "pyr.stl")
    assert zmin == pytest.approx(0, abs=1e-6) and height == pytest.approx(10, abs=1e-6)


def test_sliced_3mf_without_flags_or_with_keep_reaches_the_stub(stub, tmp_path):
    path = write_3mf(tmp_path / "s.3mf", [box(10, 10, 10)], sliced=True)
    stub(path)
    assert stub.marker.exists()
    stub(path, orient="keep")


def test_failure_on_a_stable_model_has_no_orient_hint(stub, tmp_path):
    model = write_model(tmp_path / "box.stl", box(20, 20, 5))
    with pytest.raises(BambuError) as caught:
        stub(model, scenario="missing_output")
    assert caught.value.next_command != ["slice", model, "--orient", "auto"]


@pytest.mark.parametrize("flags", [{"rotate": [("z", 90.0)]}, {"orient": "auto"}, {"face": 1}])
def test_sliced_3mf_with_pose_change_is_refused_before_the_stub(stub, tmp_path, flags):
    path = write_3mf(tmp_path / "s.3mf", [box(10, 10, 10)], sliced=True)
    with pytest.raises(BambuError) as caught:
        stub(path, **flags)
    assert caught.value.failed_step == "validate"
    assert not stub.marker.exists()


@pytest.mark.parametrize("mode", ["auto", "keep"])
def test_face_and_orient_together_are_refused_before_the_stub(stub, tmp_path, mode):
    with pytest.raises(BambuError) as caught:
        stub(write_model(tmp_path / "box.stl", box(10, 10, 10)), orient=mode, face=1)
    assert caught.value.failed_step == "validate"
    assert not stub.marker.exists()


def test_face_out_of_range_fails_the_slice_with_plate_orient(stub, tmp_path):
    with pytest.raises(BambuError) as caught:
        stub(write_model(tmp_path / "box.stl", box(10, 10, 10)), face=99)
    assert "plate orient" in str(caught.value)
    assert not stub.marker.exists()


# --- orca argv builder ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "copies", "arrange", "expected"),
    [
        ("a.stl", 1, None, True),
        ("a.3mf", 1, None, False),
        ("a.3mf", 1, True, True),
        ("a.3mf", 2, None, True),
    ],
)
def test_build_cmd_orient_and_arrange(monkeypatch, name, copies, arrange, expected):
    from bambu_cli.slicer.orca import _build_orcaslicer_cmd

    monkeypatch.setenv("DISPLAY", ":0")
    with settings_ctx(orca_slicer="orca"):
        cmd = _build_orcaslicer_cmd(
            current_settings(),
            ns(threads=None),
            "m.json",
            "p.json",
            "f.json",
            "out.3mf",
            "outdir",
            copies,
            name,
            arrange=arrange,
        )
    assert cmd[cmd.index("--orient") + 1] == "0"
    assert ("--arrange" in cmd) is expected


# --- job ------------------------------------------------------------------------


def _sliced_3mf(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    return str(path)


def _drive_job(source, *extra, slice_=None):
    from bambu_cli.context import RuntimeContext
    from bambu_cli.job.orchestrate import _run_job
    from bambu_cli.job.steps import JobSteps

    calls = {}

    def upload(args):
        calls["upload"] = args
        return "model.3mf"

    def print_(args):
        calls["print"] = args

    args = build_parser().parse_args(["--json", "job", source, "--confirm", *extra])
    with settings_ctx():
        steps = JobSteps(upload=upload, print_=print_, slice=slice_)
        _run_job(RuntimeContext.for_request(args), args, steps)
    return calls


def _failed_step(capsys):
    """The ``failed_step`` of the job failure payload printed on stdout."""
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    return payload["failed_step"]


def test_path_would_slice(tmp_path):
    from bambu_cli.job.predict import _path_would_slice

    assert _path_would_slice("a.stl", ".stl")
    assert _path_would_slice("a.step", ".step")
    assert not _path_would_slice("a.gcode", ".gcode")
    assert not _path_would_slice(_sliced_3mf(tmp_path / "s.3mf"), ".3mf")
    assert _path_would_slice(write_3mf(tmp_path / "u.3mf", [box(5, 5, 5)]), ".3mf")
    assert not _path_would_slice(str(tmp_path / "missing.3mf"), ".3mf")


def test_slice_args_for_job_forwards_orientation(tmp_path):
    from bambu_cli.job.predict import _slice_args_for_job

    args = build_parser().parse_args(["job", "a.stl", "--orient", "auto", "--rotate", "z=90", "--rotate", "x=45"])
    sliced = _slice_args_for_job("a.stl", args, str(tmp_path))
    assert sliced.orient == "auto" and sliced.face is None
    assert sliced.rotate == [("z", 90.0), ("x", 45.0)]
    # A person sees the pose drawn; a --json run does not.
    assert sliced.orient_preview is True
    json_args = build_parser().parse_args(["--json", "job", "a.stl"])
    assert _slice_args_for_job("a.stl", json_args, str(tmp_path)).orient_preview is False


def test_job_sends_a_sliced_3mf_without_flags(tmp_path, capsys):
    calls = _drive_job(_sliced_3mf(tmp_path / "s.3mf"))
    assert "upload" in calls


def test_job_reports_orientation_from_the_slice_step(tmp_path, capsys):
    from bambu_cli.slicer import SliceResult

    stl = write_model(tmp_path / "plate.stl", box(60, 3, 40))
    sliced = _sliced_3mf(tmp_path / "plate_sliced.3mf")
    result = {"mode": "auto", "changed": True, "face": 1, "rotations": [], "height_mm": 3.0}

    def slice_(_slice_ns):
        return SliceResult(sliced, orientation=result)

    calls = _drive_job(stl, "--orient", "auto", "--output", str(tmp_path), slice_=slice_)
    assert calls["upload"].file == sliced
    assert json.loads(capsys.readouterr().out)["orientation"] == result


def test_job_rotate_on_gcode_is_refused_before_upload(tmp_path, capsys):
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28\n")
    with pytest.raises(BambuError):
        _drive_job(str(gcode), "--rotate", "z=90")
    assert _failed_step(capsys) == "validate"


def test_job_face_with_auto_is_refused(tmp_path, capsys):
    stl = write_model(tmp_path / "a.stl", box(5, 5, 5))
    with pytest.raises(BambuError):
        _drive_job(stl, "--orient", "auto", "--face", "1", slice_=lambda n: stl)
    assert _failed_step(capsys) == "validate"


# --- the pose drawn in the terminal ------------------------------------------------


class _Tty:
    def __init__(self, tty=True):
        self.tty = tty
        self.text = []

    def isatty(self):
        return self.tty

    def write(self, text):
        self.text.append(text)

    def flush(self):
        pass


@pytest.fixture
def colour_tty(monkeypatch):
    stream = _Tty()
    # Not sys.stderr itself: pytest's capture puts its own back between fixture and test.
    monkeypatch.setattr(orientation_mod, "_terminal", lambda: stream)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    return stream


def test_picture_wanted_only_for_a_person_at_a_colour_terminal(colour_tty, monkeypatch):
    from bambu_cli.slicer.orientation import picture_wanted

    assert picture_wanted(ns()) is True
    assert picture_wanted(ns(json=True)) is False
    assert picture_wanted(ns(orient_preview=False)) is False
    monkeypatch.setenv("TERM", "dumb")
    assert picture_wanted(ns()) is False
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    assert picture_wanted(ns()) is False
    monkeypatch.delenv("NO_COLOR")
    colour_tty.tty = False
    assert picture_wanted(ns()) is False


def test_picture_wanted_survives_a_stream_without_isatty(monkeypatch):
    from bambu_cli.slicer.orientation import picture_wanted

    monkeypatch.setattr(orientation_mod, "_terminal", object)
    assert picture_wanted(ns()) is False

    class Closed:
        def isatty(self):
            raise ValueError("closed")

    monkeypatch.setattr(orientation_mod, "_terminal", Closed)
    assert picture_wanted(ns()) is False


def test_pose_picture_has_two_views_and_a_caption(monkeypatch):
    from bambu_cli.slicer.orientation import PICTURE_ROWS, pose_picture

    monkeypatch.setattr(orientation_mod.shutil, "get_terminal_size", lambda fallback: os.terminal_size((120, 40)))
    lines = pose_picture(box(20, 20, 5), ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    assert len(lines) == PICTURE_ROWS + 1
    assert all(line.count("\x1b[0m") == 2 for line in lines[:-1])  # each view resets its colours
    assert "from above" in lines[-1] and "from the front" in lines[-1]


def test_pose_picture_shrinks_to_a_narrow_terminal(monkeypatch):
    import re

    from bambu_cli.slicer.orientation import pose_picture

    monkeypatch.setattr(orientation_mod.shutil, "get_terminal_size", lambda fallback: os.terminal_size((40, 20)))
    first = pose_picture(box(20, 20, 5), ((1, 0, 0), (0, 1, 0), (0, 0, 1)))[0]
    assert len(re.sub(r"\x1b\[[0-9;]*m", "", first)) <= 40


def test_default_draws_the_turned_pose_before_saying_it(tmp_path, colour_tty, caplog):
    import logging

    work = tmp_path / "work"
    work.mkdir()
    with caplog.at_level(logging.INFO, logger="bambu"):
        apply_orientation(ns(), write_model(tmp_path / "pyr.stl", inverted_pyramid()), str(work))
    drawn = "".join(colour_tty.text)
    assert "\u2580" in drawn and "from the front" in drawn
    assert any("Auto turned" in r.getMessage() for r in caplog.records)


def test_no_picture_for_json_or_a_front_end(tmp_path, colour_tty):
    src = write_model(tmp_path / "box.stl", box(20, 20, 5))
    apply_orientation(ns(json=True), src, str(tmp_path))
    apply_orientation(ns(orient_preview=False), src, str(tmp_path))
    assert colour_tty.text == []


def test_a_picture_that_cannot_be_written_does_not_stop_the_slice(tmp_path, colour_tty, monkeypatch):
    def broken(text):
        raise OSError("gone")

    monkeypatch.setattr(colour_tty, "write", broken)
    src = write_model(tmp_path / "box.stl", box(20, 20, 5))
    assert apply_orientation(ns(), src, str(tmp_path)).summary["mode"] == "auto"


@pytest.mark.parametrize(
    ("request_", "said"),
    [
        (OrientRequest(mode="keep"), "Orientation: as exported"),
        (OrientRequest(face=1), "Orientation: face 1, turned"),
        (OrientRequest(mode="keep", rotations=(("z", 90.0),)), "Orientation: z+90°, turned 90° about Z from the file"),
    ],
)
def test_turn_line_names_the_pose(request_, said):
    from bambu_cli.mesh import plan_orientation
    from bambu_cli.slicer.orientation import turn_line

    assert said in turn_line(plan_orientation(box(60, 3, 40), request_), request_)


def test_turn_line_flags_a_pose_that_may_tip():
    from bambu_cli.mesh import plan_orientation
    from bambu_cli.slicer.orientation import turn_line

    request_ = OrientRequest(mode="keep")
    assert "may not stand" in turn_line(plan_orientation(inverted_pyramid(), request_), request_)
