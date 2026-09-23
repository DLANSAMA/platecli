"""Generic slice overrides (--set / --set-filament / --settings-json) must not
get around the printer-safety checks.

Each case here reached the real OrcaSlicer G-code before the fix
(2026-09-22 audit): ``nozzle_temperature=400,220`` produced ``M109 S400``
because OrcaSlicer reads its own comma-list syntax while the validator only
understood JSON; ``filament_start_gcode`` and ``machine_start_gcode`` injected
raw G-code; ``printable_area`` (a machine setting, sent through ``--set``)
changed the bed size the model is sliced for.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from bambu_cli.slicer import options as S
from tests.bambu_test_base import settings_ctx


def _args(**over):
    base = dict(
        copies=1,
        infill=15,
        nozzle_temp=None,
        bed_temp=None,
        wall_type=None,
        set_process=None,
        set_filament=None,
        settings_json=None,
    )
    base.update(over)
    return Namespace(**base)


# --- temperatures ------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "nozzle_temperature=400,220",  # OrcaSlicer's list syntax: extruder 0 gets 400
        "nozzle_temperature=220,400",
        "nozzle_temperature_initial_layer= 220 , 999 ",
        'nozzle_temperature=["400,220"]',  # a JSON element is split the same way
    ],
)
def test_comma_list_temperatures_are_range_checked(entry):
    err = S._validate_slice_options(_args(set_filament=[entry]))
    assert err is not None and "nozzle temperature override" in err


def test_comma_list_bed_temperature_is_range_checked():
    err = S._validate_slice_options(_args(set_filament=["hot_plate_temp=60,400"]))
    assert err is not None and "bed temperature override" in err


@pytest.mark.parametrize("value", ["400abc", "", "hot", "0x190", "[]", "true", "[[400]]", '{"a": 1}'])
def test_unreadable_temperature_values_fail_closed(value):
    err = S._validate_slice_options(_args(set_filament=[f"nozzle_temperature={value}"]))
    assert err is not None and "must be a number" in err


def test_unreadable_temperature_in_settings_json_fails_closed():
    blob = json.dumps({"filament": {"cool_plate_temp": [True]}})
    err = S._validate_slice_options(_args(settings_json=blob))
    assert err is not None and "must be a number" in err


@pytest.mark.parametrize(
    "entry",
    ["chamber_temperatures=400", "temperature_vitrification=[999]", "nozzle_temperature_range_high=500"],
)
def test_other_temperature_keys_are_bounded_too(entry):
    err = S._validate_slice_options(_args(set_filament=[entry]))
    assert err is not None and "temperature override" in err


@pytest.mark.parametrize(
    "entry",
    ["nozzle_temperature=230", "nozzle_temperature=[230]", "nozzle_temperature=230,230", "hot_plate_temp=[65]"],
)
def test_safe_temperatures_still_pass(entry):
    assert S._validate_slice_options(_args(set_filament=[entry])) is None


# --- G-code and scripts --------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"set_filament": ["filament_start_gcode=M104 S399"]},
        {"set_filament": ["filament_end_gcode=M140 S200"]},
        {"set_process": ["machine_start_gcode=M104 S398"]},
        {"set_process": ["post_process=/bin/sh -c true"]},
        {"settings_json": json.dumps({"process": {"change_filament_gcode": "M104 S400"}})},
    ],
)
def test_gcode_and_script_overrides_are_refused(kw):
    err = S._validate_slice_options(_args(**kw))
    assert err is not None and "cannot be overridden" in err


# --- machine (printer) settings ------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    ["printable_area=0x0,400x0,400x400,0x400", "printable_height=500", "machine_max_acceleration_x=[99999]"],
)
def test_machine_settings_are_refused_without_profiles(entry):
    # No profiles on disk (the test baseline points profiles_dir at a missing
    # directory): the built-in list of printer-geometry keys still applies.
    err = S._validate_slice_options(_args(set_process=[entry]))
    assert err is not None and "printer (machine) setting" in err


def test_machine_settings_are_refused_from_the_installed_profiles(tmp_path):
    for kind, payload in (
        ("machine", {"retraction_length": ["0.8"], "extruder_clearance_radius": "57"}),
        ("process", {"wall_loops": "2"}),
        ("filament", {"filament_retraction_length": ["nil"], "nozzle_temperature": ["220"]}),
    ):
        (tmp_path / kind).mkdir()
        (tmp_path / kind / f"{kind}.json").write_text(json.dumps(payload), encoding="utf-8")
    S._known_setting_keys.cache_clear()
    with settings_ctx(profiles_dir=str(tmp_path)):
        err = S._validate_slice_options(_args(set_process=["retraction_length=0.8"]))
        assert err is not None and "printer (machine) setting" in err
        # Point at the filament-level equivalent when there is one.
        assert "filament_retraction_length" in err
        assert "printer (machine) setting" in (
            S._validate_slice_options(_args(set_process=["extruder_clearance_radius=1"])) or ""
        )
        assert S._validate_slice_options(_args(set_process=["wall_loops=4"])) is None
    S._known_setting_keys.cache_clear()


def test_list_settings_does_not_advertise_refused_gcode_keys(tmp_path):
    (tmp_path / "filament").mkdir()
    (tmp_path / "filament" / "f.json").write_text(
        json.dumps({"filament_start_gcode": ["; x"], "filament_flow_ratio": ["0.98"]}), encoding="utf-8"
    )
    S._known_setting_keys.cache_clear()
    catalog = S.setting_catalog(str(tmp_path))
    S._known_setting_keys.cache_clear()
    assert "filament_flow_ratio" in catalog["filament"]
    assert "filament_start_gcode" not in catalog["filament"]
