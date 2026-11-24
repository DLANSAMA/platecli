"""``plate orient`` / ``plate preview``: parsing, ranking output, PNG output, failure paths."""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from array import array

import pytest

from bambu_cli import utils
from bambu_cli.cliparse import build_parser
from bambu_cli.commands.orient import cmd_orient, cmd_preview
from bambu_cli.errors import BambuError
from bambu_cli.mesh import Mesh
from bambu_cli.mesh.formats import write_stl

_BOX_TRIS = [
    (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
    (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5),
]  # fmt: skip
_MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _box(w, d, h) -> Mesh:
    verts = [((i & 1) * w, ((i >> 1) & 1) * d, ((i >> 2) & 1) * h) for i in range(8)]
    return Mesh(
        array("d", [v[0] for v in verts]),
        array("d", [v[1] for v in verts]),
        array("d", [v[2] for v in verts]),
        array("l", [i for t in _BOX_TRIS for i in t]),
    )


def _stl(path, mesh=None) -> str:
    write_stl(str(path), mesh or _box(60, 3, 40))
    return str(path)


def _3mf(path, count=1, *, sliced=False) -> str:
    mesh = _box(20, 10, 5)
    vs = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in zip(mesh.xs, mesh.ys, mesh.zs, strict=True))
    ts = "".join(
        f'<triangle v1="{mesh.tris[i]}" v2="{mesh.tris[i + 1]}" v3="{mesh.tris[i + 2]}"/>'
        for i in range(0, len(mesh.tris), 3)
    )
    obj = f'<object id="1" type="model"><mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>'
    items = "".join('<item objectid="1"/>' for _ in range(count))
    xml = (
        f'<?xml version="1.0"?><model unit="millimeter" xmlns="{_MODEL_NS}">'
        f"<resources>{obj}</resources><build>{items}</build></model>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", xml)
        if sliced:
            zf.writestr("Metadata/plate_1.gcode", "G28\n")
    return str(path)


@pytest.fixture(autouse=True)
def _reset_json_state():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None


def _args(file, **kw) -> argparse.Namespace:
    base = {"file": str(file), "json": False, "orient": None, "face": None, "rotate": None, "output": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _error_step(exc: BambuError) -> str | None:
    return exc.failed_step or (utils._LAST_ERROR_PAYLOAD or {}).get("failed_step")


# --- cliparse -----------------------------------------------------------------


def test_rotate_is_repeatable_and_ordered():
    ns = build_parser().parse_args(["slice", "a.stl", "--rotate", "x=90", "--rotate", "z=45", "--rotate", "x=-10"])
    assert ns.rotate == [("x", 90.0), ("z", 45.0), ("x", -10.0)]


@pytest.mark.parametrize("cmd", ["slice", "job", "send", "orient", "preview"])
def test_orient_flags_parse_on_every_command(cmd):
    ns = build_parser().parse_args([cmd, "a.stl", "--orient", "auto", "--rotate", "y=5"])
    assert ns.orient == "auto" and ns.rotate == [("y", 5.0)] and ns.face is None
    assert build_parser().parse_args([cmd, "a.stl", "--face", "3"]).face == 3


def test_orient_flags_default_to_unset():
    ns = build_parser().parse_args(["slice", "a.stl"])
    assert ns.orient is None and ns.face is None and ns.rotate is None


@pytest.mark.parametrize("spec", ["q=90", "x", "x=abc", "", "x=nan", "x=999999"])
def test_bad_rotate_specs_exit(spec):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["slice", "a.stl", "--rotate", spec])


@pytest.mark.parametrize("value", ["0", "-2", "x"])
def test_bad_face_exits(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["slice", "a.stl", "--face", value])


def test_bad_orient_mode_exits():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["slice", "a.stl", "--orient", "sideways"])


def test_orient_and_preview_parse():
    assert build_parser().parse_args(["orient", "a.stl"]).file == "a.stl"
    ns = build_parser().parse_args(["preview", "a.stl", "--output", "x.png"])
    assert ns.file == "a.stl" and ns.output == "x.png"


# --- orient -------------------------------------------------------------------


def test_orient_json_payload_matches_schema(tmp_path, capsys):
    from tests.contracts.test_schema_validation import _load_schema, _validate

    path = _stl(tmp_path / "plate.stl")
    plan = cmd_orient(_args(path, json=True))
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("orient.json"))
    assert payload["status"] == "ok" and payload["command"] == "orient"
    assert payload["format"] == "stl" and payload["triangles"] == 12 and payload["objects"] == 1
    assert [f["face"] for f in payload["faces"]] == list(range(1, len(payload["faces"]) + 1))
    assert payload["as_exported"].get("face") is None
    # The default (auto) lays the standing plate flat, and says how it turned it.
    assert payload["selected"]["mode"] == "auto" and payload["selected"]["changed"] is True
    assert payload["selected"]["turned"].startswith("90° about") or payload["selected"]["turned"].startswith("-90°")
    assert payload["selected"]["rotation"] == payload["rotation"]
    assert plan.faces[0].height == pytest.approx(payload["faces"][0]["height_mm"], abs=0.01)


def test_orient_json_with_keep_selects_the_exported_pose(tmp_path, capsys):
    cmd_orient(_args(_stl(tmp_path / "plate.stl"), json=True, orient="keep"))
    selected = json.loads(capsys.readouterr().out)["selected"]
    assert selected["mode"] == "keep" and selected["changed"] is False and selected.get("turned") is None


def test_orient_json_selected_follows_the_flags(tmp_path, capsys):
    cmd_orient(_args(_stl(tmp_path / "plate.stl"), json=True, face=1))
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"]["mode"] == "face" and payload["selected"]["face"] == 1
    assert payload["selected"]["height_mm"] == 3.0
    assert payload["rotation"] != [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def test_orient_human_output_has_one_row_per_face(tmp_path, capsys):
    plan = cmd_orient(_args(_stl(tmp_path / "plate.stl")))
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[0] == "face"
    rows = out[1:]
    assert len(rows) == len(plan.faces)
    assert [r.split()[0] for r in rows] == [str(n) for n in range(1, len(rows) + 1)]
    assert "auto" in rows[0] and all("auto" not in r for r in rows[1:])
    assert any("as exported" in r for r in rows)


def test_orient_human_output_reports_selection(tmp_path, capsys, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="bambu"):
        cmd_orient(_args(_stl(tmp_path / "plate.stl")))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Auto turned this model -90° about X" in text and "--orient keep slices it as exported" in text
    assert "plate slice plate.stl slices this pose" in text


def test_orient_human_output_names_the_flags_of_the_pose(tmp_path, capsys, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="bambu"):
        cmd_orient(_args(_stl(tmp_path / "plate.stl"), face=2, rotate=[("z", 15.0)]))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "plate slice plate.stl --face 2 --rotate z=15 slices this pose" in text


def test_orient_warns_about_an_unstable_export(tmp_path, capsys, caplog):
    import logging

    verts = [(-10, -10, 10), (10, -10, 10), (10, 10, 10), (-10, 10, 10), (0, 0, 0)]
    tris = [(0, 1, 2), (0, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)]
    pyramid = Mesh(
        array("d", [v[0] for v in verts]),
        array("d", [v[1] for v in verts]),
        array("d", [v[2] for v in verts]),
        array("l", [i for t in tris for i in t]),
    )
    with caplog.at_level(logging.WARNING, logger="bambu"):
        cmd_orient(_args(_stl(tmp_path / "pyr.stl", pyramid), orient="keep"))
    capsys.readouterr()
    assert any("drop --orient keep" in r.getMessage() for r in caplog.records)


def test_orient_missing_file(tmp_path):
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(tmp_path / "nope.stl"))
    assert _error_step(caught.value) == "validate" and "File not found" in str(caught.value)


def test_orient_unsupported_extension(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("hi")
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(txt))
    assert "is not a model" in str(caught.value)


def test_orient_refuses_a_sliced_3mf(tmp_path):
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(_3mf(tmp_path / "s.3mf", sliced=True)))
    assert "already sliced" in str(caught.value)


def test_orient_corrupt_model_fails_at_load(tmp_path):
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"junk")
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(bad))
    assert _error_step(caught.value) == "load"


def test_orient_bad_face_is_a_validation_error(tmp_path):
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(_stl(tmp_path / "plate.stl"), face=99))
    assert _error_step(caught.value) == "validate" and "face 99" in str(caught.value)


def test_orient_face_and_auto_conflict(tmp_path):
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(_stl(tmp_path / "plate.stl"), orient="auto", face=1))
    assert _error_step(caught.value) == "validate"


def test_two_item_3mf_ranks_but_refuses_a_face(tmp_path, capsys):
    path = _3mf(tmp_path / "two.3mf", count=2)
    cmd_orient(_args(path, json=True))
    payload = json.loads(capsys.readouterr().out)
    # The default would turn it; with two objects it is sliced, and shown, as exported.
    assert payload["objects"] == 2 and payload["selected"]["mode"] == "keep"
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(path, face=1))
    assert "2 objects" in str(caught.value) and _error_step(caught.value) == "validate"


def test_single_item_3mf_reports_format(tmp_path, capsys):
    cmd_orient(_args(_3mf(tmp_path / "one.3mf"), json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "3mf" and payload["objects"] == 1


# --- STEP input ---------------------------------------------------------------


def test_step_goes_through_the_converter_and_its_dir_is_removed(tmp_path, capsys):
    step = tmp_path / "part.step"
    step.write_text("ISO-10303-21;")
    conv_dir = tmp_path / "conv"
    seen = []

    def converter(path):
        seen.append(path)
        conv_dir.mkdir()
        out = conv_dir / "part.stl"
        write_stl(str(out), _box(10, 10, 10))
        return str(out), True

    plan = cmd_orient(_args(step), step_converter=converter)
    capsys.readouterr()
    assert seen == [str(step)] and plan.faces
    assert not conv_dir.exists()


def test_step_converter_failure(tmp_path):
    step = tmp_path / "part.stp"
    step.write_text("x")
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(step), step_converter=lambda p: (None, False))
    assert _error_step(caught.value) == "convert"


def test_step_dir_is_removed_even_when_the_load_fails(tmp_path):
    step = tmp_path / "part.step"
    step.write_text("x")
    conv_dir = tmp_path / "conv"

    def converter(path):
        conv_dir.mkdir()
        bad = conv_dir / "part.stl"
        bad.write_bytes(b"junk")
        return str(bad), True

    with pytest.raises(BambuError):
        cmd_orient(_args(step), step_converter=converter)
    assert not conv_dir.exists()


def test_default_step_converter_is_the_slicers(monkeypatch):
    from bambu_cli.commands import orient as orient_cmd
    from bambu_cli.slicer.step_convert import _convert_step_to_stl

    assert orient_cmd._default_step_converter() is _convert_step_to_stl


def test_step_without_injection_uses_the_default_converter(tmp_path, monkeypatch):
    step = tmp_path / "part.step"
    step.write_text("x")
    monkeypatch.setattr("bambu_cli.slicer.step_convert._convert_step_to_stl", lambda p: (None, False))
    with pytest.raises(BambuError) as caught:
        cmd_orient(_args(step))
    assert _error_step(caught.value) == "convert"


# --- preview ------------------------------------------------------------------


def test_preview_default_path_is_beside_the_model(tmp_path, capsys):
    path = _stl(tmp_path / "plate.stl")
    out = cmd_preview(_args(path))
    assert out == os.path.join(str(tmp_path), "plate_preview.png")
    with open(out, "rb") as fh:
        assert fh.read(8) == _PNG_MAGIC


def test_preview_output_directory(tmp_path):
    outdir = tmp_path / "pics"
    outdir.mkdir()
    out = cmd_preview(_args(_stl(tmp_path / "plate.stl"), output=str(outdir)))
    assert out == os.path.join(str(outdir), "plate_preview.png") and os.path.getsize(out) > 8


def test_preview_output_file(tmp_path):
    target = tmp_path / "mine.PNG"
    assert cmd_preview(_args(_stl(tmp_path / "plate.stl"), output=str(target))) == str(target)
    assert target.is_file()


def test_preview_output_must_be_png(tmp_path):
    target = tmp_path / "mine.jpg"
    with pytest.raises(BambuError) as caught:
        cmd_preview(_args(_stl(tmp_path / "plate.stl"), output=str(target)))
    assert "--output must name a .png" in str(caught.value) and not target.exists()


def test_preview_unwritable_output(tmp_path):
    target = tmp_path / "no_such_dir" / "x.png"
    with pytest.raises(BambuError) as caught:
        cmd_preview(_args(_stl(tmp_path / "plate.stl"), output=str(target)))
    assert _error_step(caught.value) == "write"


def test_preview_json_payload_matches_schema(tmp_path, capsys):
    from tests.contracts.test_schema_validation import _load_schema, _validate

    out = tmp_path / "p.png"
    cmd_preview(_args(_stl(tmp_path / "plate.stl"), json=True, output=str(out), face=1))
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("preview.json"))
    assert payload["status"] == "ok" and payload["command"] == "preview"
    assert payload["width"] > 0 and payload["height"] > 0 and payload["faces_shown"] >= 1
    assert payload["selected"]["mode"] == "face" and out.is_file()


def test_preview_human_output_logs_the_path(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="bambu"):
        cmd_preview(_args(_stl(tmp_path / "plate.stl")))
    assert any("Preview written" in r.getMessage() for r in caplog.records)


def test_preview_validation_errors(tmp_path):
    with pytest.raises(BambuError):
        cmd_preview(_args(tmp_path / "nope.stl"))
    with pytest.raises(BambuError):
        cmd_preview(_args(_stl(tmp_path / "plate.stl"), face=99))
    with pytest.raises(BambuError):
        cmd_preview(_args(_3mf(tmp_path / "s.3mf", sliced=True)))


def test_preview_of_a_step_file_cleans_up(tmp_path):
    step = tmp_path / "part.step"
    step.write_text("x")
    conv_dir = tmp_path / "conv"

    def converter(path):
        conv_dir.mkdir()
        out = conv_dir / "part.stl"
        write_stl(str(out), _box(10, 10, 10))
        return str(out), True

    out = cmd_preview(_args(step), step_converter=converter)
    assert out == os.path.join(str(tmp_path), "part_preview.png") and os.path.isfile(out)
    assert not conv_dir.exists()


def test_orient_draws_the_pose_on_stderr_at_a_colour_terminal(tmp_path, capsys, monkeypatch):
    import io

    from bambu_cli.slicer import orientation as orientation_mod

    terminal = io.StringIO()
    monkeypatch.setattr(orientation_mod, "picture_wanted", lambda args: True)
    monkeypatch.setattr(orientation_mod, "_terminal", lambda: terminal)
    cmd_orient(_args(_stl(tmp_path / "plate.stl")))
    drawn = terminal.getvalue()
    assert "\x1b[38;2;" in drawn and "\u2580" in drawn and "from the front" in drawn
    # stdout stays the ranking only, so it still pipes.
    assert "\x1b[" not in capsys.readouterr().out
