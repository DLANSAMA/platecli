"""The profiles handed to OrcaSlicer must carry the values their names promise.

Before the 2026-09-22 fix, three things combined so that a slice did not use
Bambu's own settings:

* ``--nozzle-temp`` / ``--bed-temp`` defaulted to 220 / 60 and overwrote every
  filament, so ``--filament PETG`` printed at PLA temperatures;
* the process and filament temp profiles kept their ``inherits`` reference,
  which OrcaSlicer does not resolve for a file outside its profile tree, so
  every inherited value fell back to a generic default (PETG came out as
  ``filament_type = PLA`` at 200 C, acceleration 500 instead of 10000);
* the printer's ``default_bed_type`` was never applied, so OrcaSlicer sliced
  for the Cool Plate, where Bambu's PETG/ABS profiles set 0 C.

These tests read back the files OrcaSlicer is given.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from bambu_cli.slicer import profiles as P


def _write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return str(path)


@pytest.fixture
def profiles(tmp_path):
    root = tmp_path / "profiles"
    _write(root / "process" / "fdm_process_common.json", {"default_acceleration": ["10000"], "wall_loops": "2"})
    process = _write(
        root / "process" / "0.20mm Standard @BBL P1P.json",
        {"inherits": "fdm_process_common", "layer_height": "0.2", "name": "0.20mm Standard @BBL P1P"},
    )
    _write(
        root / "filament" / "fdm_filament_pet.json",
        {
            "filament_type": ["PETG"],
            "nozzle_temperature": ["255"],
            "nozzle_temperature_initial_layer": ["255"],
            "hot_plate_temp": ["70"],
            "cool_plate_temp": ["0"],
        },
    )
    filament = _write(
        root / "filament" / "Bambu PETG Basic @base.json",
        {"inherits": "fdm_filament_pet", "textured_plate_temp": ["70"], "name": "Bambu PETG Basic @base"},
    )
    _write(root / "machine" / "Bambu Lab P1P.json", {"default_bed_type": "Textured PEI Plate"})
    return argparse.Namespace(root=str(root), process=process, filament=filament)


def _no_temp_flags(**over):
    ns = argparse.Namespace(nozzle_temp=None, bed_temp=None, infill=15, pattern="grid", supports=False)
    for key, value in over.items():
        setattr(ns, key, value)
    return ns


def _read_back(handles):
    out = []
    for handle in handles:
        with open(handle.name, encoding="utf-8") as fh:
            out.append(json.load(fh))
        os.unlink(handle.name)
    return out


def test_temperature_flags_default_to_the_filament_profile():
    from bambu_cli.cliparse import build_parser

    parser = build_parser()
    for argv in (["slice", "m.stl"], ["job", "m.stl"], ["send", "m.stl"]):
        ns = parser.parse_args(argv)
        assert ns.nozzle_temp is None and ns.bed_temp is None, argv


def test_job_passes_no_temperature_through_when_none_was_given():
    from bambu_cli.job.predict import _slice_args_for_job

    ns = _slice_args_for_job("m.stl", argparse.Namespace(), "/tmp/out")
    assert ns.nozzle_temp is None and ns.bed_temp is None


def test_filament_keeps_its_inherited_temperatures_and_type(profiles):
    proc, fil = _read_back(P._create_temp_profiles(profiles.process, profiles.filament, _no_temp_flags()))
    assert fil["nozzle_temperature"] == ["255"]
    assert fil["filament_type"] == ["PETG"]
    assert fil["hot_plate_temp"] == ["70"] and fil["textured_plate_temp"] == ["70"]
    assert "inherits" not in fil


def test_process_inherited_settings_are_flattened(profiles):
    proc, _fil = _read_back(P._create_temp_profiles(profiles.process, profiles.filament, _no_temp_flags()))
    assert proc["default_acceleration"] == ["10000"]
    assert proc["layer_height"] == "0.2"
    assert "inherits" not in proc


def test_explicit_temperature_flags_still_override(profiles):
    _proc, fil = _read_back(
        P._create_temp_profiles(profiles.process, profiles.filament, _no_temp_flags(nozzle_temp=240, bed_temp=65))
    )
    assert fil["nozzle_temperature"] == ["240"]
    assert fil["cool_plate_temp"] == ["65"] and fil["textured_plate_temp"] == ["65"]


def test_printer_default_bed_type_is_applied(profiles):
    assert P._default_bed_type(profiles.root, "Bambu Lab P1P") == "Textured PEI Plate"
    # No printer definition on disk: the plate every Bambu printer declares.
    assert P._default_bed_type(profiles.root, "Bambu Lab X1E") == "Textured PEI Plate"
    proc, _fil = _read_back(
        P._create_temp_profiles(profiles.process, profiles.filament, _no_temp_flags(), bed_type="Textured PEI Plate")
    )
    assert proc["curr_bed_type"] == "Textured PEI Plate"


def test_bed_type_can_still_be_overridden(profiles):
    args = _no_temp_flags(set_process=["curr_bed_type=Cool Plate"], set_filament=None, settings_json=None)
    proc, _fil = _read_back(
        P._create_temp_profiles(profiles.process, profiles.filament, args, bed_type="Textured PEI Plate")
    )
    assert proc["curr_bed_type"] == "Cool Plate"


def test_inherits_cycle_is_an_error_not_a_hang(tmp_path):
    a = _write(tmp_path / "process" / "a.json", {"inherits": "b"})
    _write(tmp_path / "process" / "b.json", {"inherits": "a"})
    with pytest.raises(ValueError, match="cycle"):
        P._load_flattened_profile(a)


def test_cmd_slice_hands_orca_the_printer_bed_type(tmp_path, monkeypatch):
    from bambu_cli.slicer import cmd as slice_cmd
    from tests.bambu_test_base import settings_ctx
    from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ORCA_STUB_SCENARIO", "success")
    launcher = make_orca_launcher(str(tmp_path))
    root = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    seen = {}
    real = slice_cmd._create_temp_profiles

    def spy(process, filament, args, **kwargs):
        seen.update(kwargs)
        return real(process, filament, args, **kwargs)

    monkeypatch.setattr(slice_cmd, "_create_temp_profiles", spy)
    args = argparse.Namespace(
        file=model, output=str(tmp_path), quality="standard", copies=1, infill=15, pattern="grid",
        supports=False, nozzle_temp=None, bed_temp=None, filament="PLA Basic", json=False, threads=None,
        list_settings=False,
    )  # fmt: skip
    with settings_ctx(orca_slicer=launcher, profiles_dir=root, printer_model="P1P", nozzle_size="0.4"):
        slice_cmd.cmd_slice(args)
    assert seen.get("bed_type") == "Textured PEI Plate"
