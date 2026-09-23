"""Slicing leaves exactly one file in the output directory: the sliced 3MF.

OrcaSlicer also writes ``plate_1.gcode`` and ``result.json`` into its
``--outputdir``. platecli pointed that at the user's output directory -- by
default the folder the model lives in -- so every slice left both behind and
overwrote any same-named file already there (found 2026-09-22; the stray
``plate_1.gcode`` in the repo root came from this). OrcaSlicer now writes into
a private directory and only the 3MF is moved out.
"""

from __future__ import annotations

import argparse

import pytest

from bambu_cli.slicer import cmd_slice
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl


@pytest.fixture
def slice_into(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model = write_stl(str(model_dir / "model.stl"))

    def run(scenario="success", output=None):
        monkeypatch.setenv("ORCA_STUB_SCENARIO", scenario)
        args = argparse.Namespace(
            file=model, output=output, quality="standard", copies=1, infill=15, pattern="grid",
            supports=False, nozzle_temp=None, bed_temp=None, filament="PLA Basic", json=False,
            threads=None, list_settings=False,
        )  # fmt: skip
        with settings_ctx(orca_slicer=launcher, profiles_dir=profiles, printer_model="P1P", nozzle_size="0.4"):
            return cmd_slice(args)

    run.model_dir = model_dir
    return run


def test_only_the_3mf_is_left_next_to_the_model(slice_into):
    out = slice_into()
    assert sorted(p.name for p in slice_into.model_dir.iterdir()) == ["model.stl", "model_sliced.3mf"]
    assert out == str(slice_into.model_dir / "model_sliced.3mf")


def test_same_named_user_files_are_not_touched(slice_into):
    mine = slice_into.model_dir / "plate_1.gcode"
    mine.write_text("; my own gcode\n", encoding="utf-8")
    (slice_into.model_dir / "result.json").write_text('{"mine": true}', encoding="utf-8")
    slice_into()
    assert mine.read_text(encoding="utf-8") == "; my own gcode\n"
    assert (slice_into.model_dir / "result.json").read_text(encoding="utf-8") == '{"mine": true}'


def test_no_private_work_dir_survives_a_failed_slice(slice_into):
    from bambu_cli.errors import BambuError

    with pytest.raises(BambuError):
        slice_into("fail")
    assert sorted(p.name for p in slice_into.model_dir.iterdir()) == ["model.stl"]
