"""Tests for bambu_cli.interactive.presets."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.cli import build_parser  # noqa: E402
from bambu_cli.constants import (  # noqa: E402
    MAX_BED_TEMP_C,
    MAX_NOZZLE_TEMP_C,
    MIN_BED_TEMP_C,
    MIN_NOZZLE_TEMP_C,
)
from bambu_cli.interactive.presets import MATERIAL_PRESETS, QUALITY_PRESETS, preset_to_job_args  # noqa: E402


# ---------------------------------------------------------------------------
# MATERIAL_PRESETS schema
# ---------------------------------------------------------------------------


def test_every_material_has_nozzle_and_bed_temp():
    for name, preset in MATERIAL_PRESETS.items():
        assert "nozzle_temp" in preset, f"{name} missing nozzle_temp"
        assert "bed_temp" in preset, f"{name} missing bed_temp"
        assert "filament" in preset, f"{name} missing filament"


@pytest.mark.parametrize("name,preset", MATERIAL_PRESETS.items())
def test_nozzle_temp_in_range(name, preset):
    assert MIN_NOZZLE_TEMP_C < preset["nozzle_temp"] <= MAX_NOZZLE_TEMP_C, (
        f"{name}: nozzle_temp {preset['nozzle_temp']} outside "
        f"[{MIN_NOZZLE_TEMP_C}, {MAX_NOZZLE_TEMP_C}]"
    )


@pytest.mark.parametrize("name,preset", MATERIAL_PRESETS.items())
def test_bed_temp_in_range(name, preset):
    assert MIN_BED_TEMP_C <= preset["bed_temp"] <= MAX_BED_TEMP_C, (
        f"{name}: bed_temp {preset['bed_temp']} outside [{MIN_BED_TEMP_C}, {MAX_BED_TEMP_C}]"
    )


# ---------------------------------------------------------------------------
# Filament substring resolution (regression: see MATERIAL_PRESETS comment)
# ---------------------------------------------------------------------------

# Real @base filenames from OrcaSlicer's BBL vendor profile dir.  These are the
# decoys that make a bare "ABS"/"TPU" ambiguous.
_REAL_BBL_BASE_PROFILES = [
    "Bambu PLA Basic @base.json",
    "Bambu PLA Matte @base.json",
    "Bambu Support For PLA-PETG @base.json",
    "Bambu PETG Basic @base.json",
    "Bambu PETG HF @base.json",
    "Bambu PETG-CF @base.json",
    "Bambu PETG Translucent @base.json",
    "Generic PETG @base.json",
    "Generic PETG HF @base.json",
    "Bambu ABS @base.json",
    "Bambu ABS-GF @base.json",
    "Bambu Support for ABS @base.json",
    "Generic ABS @base.json",
    "Bambu TPU 95A @base.json",
    "Bambu TPU 95A HF @base.json",
    "Bambu TPU for AMS @base.json",
    "Generic TPU for AMS @base.json",
]

_EXPECTED_RESOLUTION = {
    "PLA": "Bambu PLA Basic @base.json",
    "PETG": "Bambu PETG Basic @base.json",
    "ABS": "Bambu ABS @base.json",
    "TPU": "Bambu TPU 95A @base.json",
}


@pytest.mark.parametrize("material", sorted(MATERIAL_PRESETS))
def test_material_filament_substrings_are_unambiguous(material):
    """Each preset substring must match exactly ONE real @base profile.

    slicer/cmd.py takes the first os.listdir hit, and listdir order is
    filesystem-dependent -- so more than one match means the chosen profile
    differs between Linux and Windows.  A bare "ABS" would sometimes select
    "Bambu Support for ABS" (support-interface filament), which is a real
    mis-slice, not a cosmetic issue.
    """
    requested = MATERIAL_PRESETS[material]["filament"].lower()
    hits = [f for f in _REAL_BBL_BASE_PROFILES if requested in f.lower() and "@base" in f]

    assert len(hits) == 1, f"{material}: substring {requested!r} matched {len(hits)} profiles: {hits}"
    assert hits[0] == _EXPECTED_RESOLUTION[material]


# ---------------------------------------------------------------------------
# QUALITY_PRESETS schema
# ---------------------------------------------------------------------------

VALID_QUALITY_VALUES = {"draft", "standard", "high"}


def test_quality_presets_valid_values():
    for name, preset in QUALITY_PRESETS.items():
        assert preset["quality"] in VALID_QUALITY_VALUES, f"{name}: unexpected quality value '{preset['quality']}'"


# ---------------------------------------------------------------------------
# preset_to_job_args
# ---------------------------------------------------------------------------


def test_preset_to_job_args_source_set():
    ns = preset_to_job_args("PLA", "standard", False, "model.stl")
    assert ns.source == "model.stl"


def test_preset_to_job_args_has_all_job_keys():
    """Pin test: result must have at least all attributes from build_parser job defaults."""
    reference = build_parser().parse_args(["job", "dummy.stl"])
    ref_keys = set(vars(reference).keys())

    result = preset_to_job_args("PLA", "standard", False, "dummy.stl")
    result_keys = set(vars(result).keys())

    missing = ref_keys - result_keys
    assert not missing, f"preset_to_job_args result missing keys: {missing}"


def test_preset_pla_standard():
    ns = preset_to_job_args("PLA", "standard", False, "file.stl")
    assert ns.nozzle_temp == 220
    assert ns.bed_temp == 55
    assert ns.quality == "standard"
    assert ns.filament == "Bambu PLA Basic @base"


def test_preset_petg_fine():
    ns = preset_to_job_args("PETG", "fine", False, "file.stl")
    assert ns.quality == "high"
    assert ns.nozzle_temp == 255
    assert ns.bed_temp == 70


def test_supports_enabled():
    ns = preset_to_job_args("PLA", "standard", True, "f.stl")
    assert ns.supports is True
    assert ns.support_type == "tree"


def test_supports_disabled():
    ns = preset_to_job_args("PLA", "standard", False, "f.stl")
    assert ns.supports is False
