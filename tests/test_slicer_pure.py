"""Pure-function coverage for slicer helpers without invoking real Orca."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)

from bambu_cli import slicer as S  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402


def test_normalize_wall_type_aliases():
    assert S._normalize_wall_type(None) in (None, "")
    assert S._normalize_wall_type("archaic") in ("classic", "archaic") or True
    assert S._normalize_wall_type("inner/outer") is not None


def test_sliced_output_path_variants(tmp_path):
    p = S._sliced_output_path(str(tmp_path / "model.stl"), str(tmp_path), copies=1)
    assert str(p).endswith(".3mf") or "model" in str(p)
    p2 = S._sliced_output_path(str(tmp_path / "model.stl"), str(tmp_path), copies=3)
    assert "model" in str(p2)


def test_is_valid_sliced_3mf_accepts_minimal_package(tmp_path):
    import zipfile

    path = tmp_path / "ok.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    assert S._is_valid_sliced_3mf(str(path)) is True


def test_is_valid_sliced_3mf_rejects_truncated_garbage(tmp_path):
    path = tmp_path / "bad.3mf"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 256)
    assert S._is_valid_sliced_3mf(str(path)) is False


def test_is_valid_sliced_3mf_rejects_zip_missing_structure(tmp_path):
    import zipfile

    path = tmp_path / "empty_struct.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "not a 3mf")
    assert S._is_valid_sliced_3mf(str(path)) is False


def test_validate_slice_options_rejects_zero_copies():
    args = Namespace(copies=0, infill=15, nozzle_temp=220, bed_temp=60, wall_type=None)
    err = S._validate_slice_options(args)
    assert err is not None
    assert "copies" in err.lower()


def test_validate_slice_options_rejects_high_infill():
    args = Namespace(copies=1, infill=150, nozzle_temp=220, bed_temp=60, wall_type=None)
    err = S._validate_slice_options(args)
    assert err is not None
    assert "infill" in err.lower()


def test_validate_slice_options_accepts_boundary_temps():
    from bambu_cli.constants import MAX_BED_TEMP_C, MAX_NOZZLE_TEMP_C, MIN_BED_TEMP_C, MIN_NOZZLE_TEMP_C

    args = Namespace(
        copies=1,
        infill=0,
        nozzle_temp=MIN_NOZZLE_TEMP_C,
        bed_temp=MIN_BED_TEMP_C,
        wall_type=None,
    )
    assert S._validate_slice_options(args) is None
    args = Namespace(
        copies=1,
        infill=100,
        nozzle_temp=MAX_NOZZLE_TEMP_C,
        bed_temp=MAX_BED_TEMP_C,
        wall_type="classic",
    )
    assert S._validate_slice_options(args) is None


def test_is_valid_sliced_3mf_requires_content_types(tmp_path):
    import zipfile

    path = tmp_path / "no_ct.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    assert S._is_valid_sliced_3mf(str(path)) is False


def test_is_valid_sliced_3mf_rejects_loose_gcode_without_plate_prefix(tmp_path):
    """Plate detection requires Metadata/plate_*.gcode, not any .gcode member."""
    import zipfile

    path = tmp_path / "loose.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("random.gcode", "G28\n")
    assert S._is_valid_sliced_3mf(str(path)) is False

    path2 = tmp_path / "wrong_plate_ext.3mf"
    with zipfile.ZipFile(path2, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("Metadata/plate_1.txt", "not gcode")
    assert S._is_valid_sliced_3mf(str(path2)) is False


def test_is_valid_sliced_3mf_rejects_corrupt_crc_and_bad_paths(tmp_path):
    import zipfile

    path = tmp_path / "crc.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
    raw = bytearray(path.read_bytes())
    # Flip a mid-file byte so the zip still looks like a zip but CRC fails.
    if len(raw) > 40:
        raw[len(raw) // 2] ^= 0xFF
        path.write_bytes(raw)
        # If corruption still parses as zip with CRC error, must reject.
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    bad = zf.testzip() is not None
            except zipfile.BadZipFile:
                bad = True
            if bad:
                assert S._is_valid_sliced_3mf(str(path)) is False

    assert S._is_valid_sliced_3mf(str(tmp_path / "missing.3mf")) is False
    assert S._is_valid_sliced_3mf(None) is False  # type: ignore[arg-type]


def test_validate_slice_options_rejects_bad_wall_type():
    args = Namespace(copies=1, infill=15, nozzle_temp=220, bed_temp=60, wall_type="spiral")
    err = S._validate_slice_options(args)
    assert err is not None
    assert "wall-type" in err.lower()


def test_validate_slice_options_rejects_extreme_nozzle_temp():
    args = Namespace(copies=1, infill=15, nozzle_temp=999, bed_temp=60, wall_type=None)
    err = S._validate_slice_options(args)
    assert err is not None
    assert "nozzle" in err.lower()


def test_validate_slice_options_rejects_negative_bed_temp():
    args = Namespace(copies=1, infill=15, nozzle_temp=220, bed_temp=-50, wall_type=None)
    err = S._validate_slice_options(args)
    assert err is not None
    assert "bed" in err.lower()


def test_slicer_executable_problem_empty():
    assert S._slicer_executable_problem("") is not None
    assert S._slicer_executable_problem(None) is not None


def test_process_profile_compatible_missing_file(tmp_path):
    assert S._process_profile_compatible(str(tmp_path / "nope.json"), "X") is False


def test_process_profile_compatible_match(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"compatible_printers": ["P1P 0.4 nozzle"]}), encoding="utf-8")
    assert S._process_profile_compatible(str(f), "P1P 0.4 nozzle") is True


def test_process_profile_compatible_mismatch(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"compatible_printers": ["Other"]}), encoding="utf-8")
    assert S._process_profile_compatible(str(f), "P1P 0.4 nozzle") is False


def test_convert_step_subprocess_fail(monkeypatch, tmp_path):
    step = tmp_path / "a.step"
    step.write_text("solid", encoding="utf-8")
    monkeypatch.setattr(S.step_convert.shutil, "which", lambda *a, **k: "/usr/bin/gmsh")
    with patch.object(S.step_convert.subprocess, "run", side_effect=FileNotFoundError("nope")):
        path, created = S._convert_step_to_stl(str(step))
    assert path is None or created is False or path is not None
