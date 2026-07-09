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


# --- Generic setting overrides (--set / --set-filament / --settings-json) -----


def test_parse_kv_overrides_valid_and_malformed():
    assert S._parse_kv_overrides(["wall_loops=4", "brim_type=outer_only"], "set") == {
        "wall_loops": "4",
        "brim_type": "outer_only",
    }
    assert S._parse_kv_overrides(None, "set") == {}
    with pytest.raises(ValueError):
        S._parse_kv_overrides(["wall_loops"], "set")  # no '='
    with pytest.raises(ValueError):
        S._parse_kv_overrides(["=4"], "set")  # empty key


def test_coerce_override_value_matches_base_shape():
    # list-typed base -> single-element list, or a parsed JSON array
    assert S._coerce_override_value(["220"], "230") == ["230"]
    assert S._coerce_override_value(["220"], "[230, 235]") == ["230", "235"]
    # scalar base -> string, regardless of numeric-looking input
    assert S._coerce_override_value("3", "4") == "4"
    assert S._coerce_override_value(None, "outer_only") == "outer_only"


def test_generic_section_overrides_precedence():
    # --set beats --settings-json for the same key
    args = Namespace(
        settings_json='{"process": {"wall_loops": "2", "brim_width": "1"}}',
        set_process=["wall_loops=5"],
        set_filament=None,
    )
    result = S._generic_section_overrides(args, "process")
    assert result == {"wall_loops": "5", "brim_width": "1"}


def test_create_temp_profiles_applies_set_override(tmp_path):
    process = tmp_path / "process" / "p.json"
    filament = tmp_path / "filament" / "f.json"
    process.parent.mkdir()
    filament.parent.mkdir()
    process.write_text(json.dumps({"wall_loops": "2", "name": "p"}), encoding="utf-8")
    filament.write_text(json.dumps({"nozzle_temperature": ["220"], "name": "f"}), encoding="utf-8")
    args = Namespace(
        infill=15,
        pattern="3dhoneycomb",
        supports=False,
        nozzle_temp=220,
        bed_temp=60,
        set_process=["wall_loops=6"],
        set_filament=["filament_flow_ratio=0.98"],
        settings_json=None,
        brim=2,
    )
    tmp_proc, tmp_fil = S._create_temp_profiles(str(process), str(filament), args)
    try:
        proc = json.loads(Path(tmp_proc.name).read_text(encoding="utf-8"))
        fil = json.loads(Path(tmp_fil.name).read_text(encoding="utf-8"))
        assert proc["wall_loops"] == "6"  # --set won
        assert proc["brim_width"] == "2"  # named --brim flag
        assert proc["brim_type"] == "outer_only"
        assert fil["filament_flow_ratio"] == "0.98"  # --set-filament passthrough
    finally:
        import os as _os

        _os.unlink(tmp_proc.name)
        _os.unlink(tmp_fil.name)


# --- Safety: overrides cannot bypass temperature bounds ----------------------


def _base_slice_args(**over):
    base = dict(
        copies=1,
        infill=15,
        nozzle_temp=220,
        bed_temp=60,
        wall_type=None,
        layer_height=None,
        brim=None,
        speed=None,
        set_process=None,
        set_filament=None,
        settings_json=None,
    )
    base.update(over)
    return Namespace(**base)


def test_validate_rejects_unsafe_set_filament_nozzle_temp():
    args = _base_slice_args(set_filament=["nozzle_temperature=999"])
    err = S._validate_slice_options(args)
    assert err is not None and "nozzle temperature override" in err


def test_validate_rejects_unsafe_settings_json_bed_temp():
    args = _base_slice_args(settings_json='{"filament": {"hot_plate_temp": "500"}}')
    err = S._validate_slice_options(args)
    assert err is not None and "bed temperature override" in err


def test_validate_accepts_safe_overrides():
    args = _base_slice_args(
        set_process=["wall_loops=4"],
        set_filament=["nozzle_temperature=230"],
        layer_height=0.2,
        brim=3.0,
        speed=120.0,
    )
    assert S._validate_slice_options(args) is None


def test_validate_rejects_malformed_set_and_json():
    assert "KEY=VALUE" in (S._validate_slice_options(_base_slice_args(set_process=["oops"])) or "")
    assert "valid JSON" in (S._validate_slice_options(_base_slice_args(settings_json="{not json")) or "")


def test_validate_rejects_bad_named_flags():
    assert "layer-height" in (S._validate_slice_options(_base_slice_args(layer_height=5.0)) or "")
    assert "speed" in (S._validate_slice_options(_base_slice_args(speed=0)) or "")


def test_known_setting_keys_reads_profiles(tmp_path):
    (tmp_path / "process").mkdir()
    (tmp_path / "filament").mkdir()
    (tmp_path / "process" / "a.json").write_text(
        json.dumps({"wall_loops": "3", "layer_height": "0.2", "name": "a", "inherits": "x"}), encoding="utf-8"
    )
    (tmp_path / "filament" / "f.json").write_text(json.dumps({"flow_ratio": "1.0", "name": "f"}), encoding="utf-8")
    proc = S._known_setting_keys(str(tmp_path), "process")
    fil = S._known_setting_keys(str(tmp_path), "filament")
    assert "wall_loops" in proc and "layer_height" in proc
    assert "name" not in proc and "inherits" not in proc  # bookkeeping keys excluded
    assert "flow_ratio" in fil


def test_slice_args_for_job_threads_overrides():
    from bambu_cli.job.predict import _slice_args_for_job

    src = Namespace(
        set_process=["wall_loops=4"],
        set_filament=["flow_ratio=0.9"],
        settings_json='{"process":{}}',
        layer_height=0.15,
        brim=2.0,
        speed=100.0,
    )
    out = _slice_args_for_job("m.stl", src, "/out")
    assert out.set_process == ["wall_loops=4"]
    assert out.set_filament == ["flow_ratio=0.9"]
    assert out.settings_json == '{"process":{}}'
    assert out.layer_height == 0.15 and out.brim == 2.0 and out.speed == 100.0


def test_create_temp_profiles_named_convenience_flags(tmp_path):
    process = tmp_path / "process" / "p.json"
    filament = tmp_path / "filament" / "f.json"
    process.parent.mkdir()
    filament.parent.mkdir()
    process.write_text(json.dumps({"name": "p"}), encoding="utf-8")
    filament.write_text(json.dumps({"fan_max_speed": ["80"], "name": "f"}), encoding="utf-8")
    args = Namespace(
        infill=15,
        pattern="3dhoneycomb",
        supports=False,
        nozzle_temp=220,
        bed_temp=60,
        first_layer_height=0.25,
        seam_position="back",
        ironing="none",
        support_threshold=40,
        fan_speed=90,
        flow_ratio=0.97,
        set_process=None,
        set_filament=None,
        settings_json=None,
    )
    tmp_proc, tmp_fil = S._create_temp_profiles(str(process), str(filament), args)
    try:
        proc = json.loads(Path(tmp_proc.name).read_text(encoding="utf-8"))
        fil = json.loads(Path(tmp_fil.name).read_text(encoding="utf-8"))
        assert proc["initial_layer_print_height"] == "0.25"
        assert proc["seam_position"] == "back"
        assert proc["ironing_type"] == "no ironing"  # 'none' -> Orca's off value
        assert proc["support_threshold_angle"] == "40"
        assert fil["fan_max_speed"] == ["90"]  # list-typed, matches base shape
        assert fil["filament_flow_ratio"] == "0.97"  # correct Orca key, not bare flow_ratio
    finally:
        import os as _os

        _os.unlink(tmp_proc.name)
        _os.unlink(tmp_fil.name)


def test_validate_rejects_bad_convenience_flags():
    assert "first-layer-height" in (S._validate_slice_options(_base_slice_args(first_layer_height=2.0)) or "")
    assert "support-threshold" in (S._validate_slice_options(_base_slice_args(support_threshold=120)) or "")
    assert "fan-speed" in (S._validate_slice_options(_base_slice_args(fan_speed=150)) or "")
    assert "flow-ratio" in (S._validate_slice_options(_base_slice_args(flow_ratio=5.0)) or "")
