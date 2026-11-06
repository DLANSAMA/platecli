"""Slicer audit round 4: numeric quality, overflow, filament choice, glob escape, failed-run output."""

from __future__ import annotations

import argparse
import json
import os
import pathlib

import pytest

from bambu_cli.errors import BambuError
from bambu_cli.slicer import cmd_slice
from bambu_cli.slicer import options as slicer_options
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl
from tests.test_slice_stub_integration import _slice_args, orca_env  # noqa: F401


def _write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


# --- S1 ----------------------------------------------------------------------


@pytest.mark.parametrize("quality", ["0.08", "0.10", "0.14", "0.18"])
def test_unmapped_numeric_quality_without_profile_errors(orca_env, quality):  # noqa: F811
    args = _slice_args(orca_env.model, orca_env.outdir, quality=quality)
    with pytest.raises(BambuError) as exc:
        orca_env("success", args=args)
    assert quality in str(exc.value)
    assert "0.20" in str(exc.value)  # lists the available heights
    assert not os.path.exists(orca_env.outpath)


def test_unmapped_numeric_quality_uses_matching_profile(orca_env):  # noqa: F811
    _write(
        os.path.join(orca_env.profiles, "process", "0.08mm Extra Fine @BBL P1P.json"),
        {"type": "process", "name": "0.08mm Extra Fine @BBL P1P", "layer_height": "0.08"},
    )
    dump = os.path.join(os.path.dirname(orca_env.profiles), "dump.json")
    args = _slice_args(orca_env.model, orca_env.outdir, quality="0.08")
    orca_env("success", args=args, ORCA_STUB_PROFILE_DUMP=dump)
    assert "0.08" in pathlib.Path(dump).read_text(encoding="utf-8")


# --- S2 ----------------------------------------------------------------------


def test_huge_integer_temperature_is_a_validation_error():
    huge = "1" + "0" * 400
    assert slicer_options._temperature_values(f"[{huge}]") is None
    args = argparse.Namespace(set_filament=[f"nozzle_temperature=[{huge}]"], set=[], settings_json=None)
    problem = slicer_options._override_safety_problem(args)
    assert problem and "nozzle_temperature" in problem


# --- S3 ----------------------------------------------------------------------


def test_filament_choice_ignores_directory_order(orca_env, monkeypatch, caplog):  # noqa: F811
    import logging

    fdir = os.path.join(orca_env.profiles, "filament")
    for name in ("Bambu ASA-Aero @base.json", "Bambu ASA @base.json", "Bambu Support For ASA @base.json"):
        _write(os.path.join(fdir, name), {"type": "filament", "name": name[:-5], "filament_type": ["ASA"]})
    real = os.listdir

    def reverse_first(path):
        names = real(path)
        return sorted(names) if os.path.basename(path) != "filament" else sorted(names, reverse=True)

    monkeypatch.setattr(os, "listdir", reverse_first)
    args = _slice_args(orca_env.model, orca_env.outdir, filament="ASA")
    with caplog.at_level(logging.INFO, logger="bambu"):
        orca_env("success", args=args)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Bambu ASA," in text and "ASA-Aero," not in text
    assert "matched" in text


# --- S4 ----------------------------------------------------------------------


def test_known_keys_found_in_bracketed_profiles_dir(tmp_path):
    base = tmp_path / "Orca [beta]" / "profiles" / "BBL"
    _write(str(base / "machine" / "m.json"), {"min_layer_height": "0.07"})
    slicer_options._known_setting_keys.cache_clear()
    assert "min_layer_height" in slicer_options._known_setting_keys(str(base), "machine")


# --- S5 ----------------------------------------------------------------------


def test_failed_run_does_not_clobber_previous_output(orca_env):  # noqa: F811
    before = b"previous good output"
    with open(orca_env.outpath, "wb") as fh:
        fh.write(before)
    with pytest.raises(BambuError):
        orca_env("fail_real_gl")
    assert pathlib.Path(orca_env.outpath).read_bytes() == before
    assert not [n for n in os.listdir(orca_env.outdir) if n.startswith(".plate-slice-")]
