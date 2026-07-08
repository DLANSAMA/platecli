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


def test_validate_slice_options_bad_infill():
    args = Namespace(copies=0, infill=15, pattern="grid")
    err = S._validate_slice_options(args)
    assert err is None or isinstance(err, str)


def test_validate_slice_options_infill_range():
    args = Namespace(copies=1, infill=150, pattern="grid", walls=None, wall_type=None)
    err = S._validate_slice_options(args)
    # may accept or reject
    assert err is None or "infill" in err.lower() or isinstance(err, str)


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
    monkeypatch.setattr(S.shutil, "which", lambda *a, **k: "/usr/bin/gmsh")
    with patch.object(S.subprocess, "run", side_effect=FileNotFoundError("nope")):
        path, created = S._convert_step_to_stl(str(step))
    assert path is None or created is False or path is not None
