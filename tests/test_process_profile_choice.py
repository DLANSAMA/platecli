"""Process-profile discovery picks the requested quality, deterministically.

When a model has no process profile of its own (P1S, X1 and X1E borrow the
X1C ones through ``compatible_printers``), discovery took the first file in
directory order whose name merely *contained* the model code. With real
OrcaSlicer profiles that was ``0.20mm Bambu Support W @BBL X1C`` -- a profile
for printing with support material -- for every "standard" P1S/X1/X1E slice,
and ``"X1"`` also matched every X1C/X1E file by substring (found in review,
2026-09-23).
"""

from __future__ import annotations

import json

import pytest

from bambu_cli.slicer.profiles import _discover_process_profile


def _profiles(tmp_path, files):
    proc = tmp_path / "process"
    proc.mkdir()
    for name, compatible in files.items():
        (proc / f"{name}.json").write_text(json.dumps({"compatible_printers": compatible}), encoding="utf-8")
    return str(tmp_path)


def _quality_map(code):
    return {"standard": f"0.20mm Standard @BBL {code}", "high": f"0.12mm Fine @BBL {code}"}


def _pick(root, code, printer, quality="standard"):
    found = _discover_process_profile(
        quality, _quality_map(code), model_code=code, compatible_printer=f"{printer} 0.4 nozzle", profiles_dir=root
    )
    return found.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][: -len(".json")] if found else None


X1C_FAMILY = ["Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab X1 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle"]


@pytest.mark.parametrize("code, printer", [("P1S", "Bambu Lab P1S"), ("X1", "Bambu Lab X1")])
def test_borrowed_profile_is_the_requested_quality_not_a_specialty_one(tmp_path, code, printer):
    root = _profiles(
        tmp_path,
        {
            "0.20mm Bambu Support W @BBL X1C": X1C_FAMILY,
            "0.20mm Standard @BBL X1C": X1C_FAMILY,
            "0.20mm Strength @BBL X1C": X1C_FAMILY,
        },
    )
    assert _pick(root, code, printer) == "0.20mm Standard @BBL X1C"


def test_model_code_is_not_matched_as_a_substring(tmp_path):
    # "A1" must not pick up an A1 mini ("A1M") profile the A1 cannot use.
    root = _profiles(tmp_path, {"0.20mm Standard @BBL A1M": ["Bambu Lab A1 mini 0.4 nozzle"]})
    assert _pick(root, "A1", "Bambu Lab A1") is None


def test_real_profiles_give_standard_for_every_model():
    import os

    from bambu_cli.config import detect_profiles_dir

    real = detect_profiles_dir()
    if not real or not os.path.isdir(os.path.join(real, "process")):
        pytest.skip("no OrcaSlicer BBL profiles installed on this machine")
    for code, printer in (("P1S", "Bambu Lab P1S"), ("X1", "Bambu Lab X1"), ("X1E", "Bambu Lab X1E")):
        assert _pick(real, code, printer) == "0.20mm Standard @BBL X1C", code
