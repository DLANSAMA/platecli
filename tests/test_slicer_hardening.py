"""Hardening regressions for slicer filament matching and non-zero OrcaSlicer exits."""

from __future__ import annotations

import argparse
import os
import subprocess

import pytest

from bambu_cli.constants import EXIT_COMMAND_ERROR
from bambu_cli.errors import BambuError, FileError, SliceError
from bambu_cli.slicer import cmd_slice
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl
from tests.json_contract_base import sliced_3mf_bytes


def _make_slice_args(filepath: str, outdir: str, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        file=filepath,
        output=outdir,
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
        setattr(ns, k, v)
    return ns


def test_unmatched_filament_fails_closed_instead_of_pla_fallback(tmp_path, monkeypatch):
    orca_launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    assert os.path.isfile(os.path.join(profiles, "filament", "Bambu PLA Basic @base.json"))
    model = write_stl(str(tmp_path / "cube.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    monkeypatch.setattr(
        "bambu_cli.slicer.cmd._run_orcaslicer",
        lambda *a, **k: pytest.fail("OrcaSlicer must not run when filament profile is unmatched"),
    )

    with settings_ctx(orca_slicer=orca_launcher, profiles_dir=profiles), pytest.raises(BambuError) as ei:
        cmd_slice(_make_slice_args(model, str(outdir), filament="PET-G"))

    assert ei.value.exit_code == EXIT_COMMAND_ERROR
    assert ei.value.failed_step == "validate"
    assert "PET-G" in str(ei.value)
    assert not (outdir / "cube_sliced.3mf").exists()


def test_unmatched_filament_json_envelope(tmp_path, monkeypatch, capsys):
    orca_launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "cube.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    monkeypatch.setattr(
        "bambu_cli.slicer.cmd._run_orcaslicer",
        lambda *a, **k: pytest.fail("OrcaSlicer must not run when filament profile is unmatched"),
    )

    with settings_ctx(orca_slicer=orca_launcher, profiles_dir=profiles), pytest.raises(BambuError) as ei:
        cmd_slice(_make_slice_args(model, str(outdir), filament="PET-G", json=True))

    assert capsys.readouterr().out == ""
    payload = ei.value.to_error_payload("slice")
    assert payload["status"] == "error"
    assert payload["command"] == "slice"
    assert payload["exit_code"] == EXIT_COMMAND_ERROR
    assert payload["failed_step"] == "validate"
    assert payload["filament"] == "PET-G"


@pytest.mark.parametrize("bad_bytes", [b"", b"not-a-valid-3mf"])
def test_nonzero_orcaslicer_exit_with_bad_produced_raises_slice_error_and_preserves_existing_outpath(
    tmp_path, monkeypatch, bad_bytes
):
    orca_launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()
    existing = outdir / "model_sliced.3mf"
    original_bytes = sliced_3mf_bytes(plates=(1,))
    existing.write_bytes(original_bytes)

    def _fake_run(cmd, *a, **k):
        out_idx = cmd.index("--outputdir") + 1
        with open(os.path.join(cmd[out_idx], "model_sliced.3mf"), "wb") as fh:
            fh.write(bad_bytes)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="[error] slice failed")

    monkeypatch.setattr("bambu_cli.slicer.cmd._run_orcaslicer", _fake_run)
    with settings_ctx(orca_slicer=orca_launcher, profiles_dir=profiles), pytest.raises(SliceError) as ei:
        cmd_slice(_make_slice_args(model, str(outdir)))

    assert not isinstance(ei.value, FileError)
    assert ei.value.exit_code == EXIT_COMMAND_ERROR
    assert ei.value.failed_step == "slicer"
    assert ei.value.extra["returncode"] == 1
    assert ei.value.extra["output"] == str(existing)
    assert existing.read_bytes() == original_bytes
    assert [p.name for p in outdir.iterdir()] == ["model_sliced.3mf"]
