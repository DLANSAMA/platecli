"""Tests for external tray (vt_tray) parsing in ams.py and interactive detection."""

import argparse
from unittest.mock import MagicMock

from bambu_cli.ams import parse_ams
from bambu_cli.interactive.core import read_loaded_ams_material


def test_parse_ams_external_tray_only():
    status = {
        "gcode_state": "IDLE",
        "mc_percent": 0,
        "vt_tray": {
            "id": "254",
            "tray_type": "PETG",
            "tray_color": "FF0000FF",
            "remain": 75,
        },
        "tray_now": "254",
    }
    result = parse_ams(status)
    assert result is not None
    assert result["active_tray"] is None  # normalized sentinel
    assert result["units"] == []
    ext = result["external_tray"]
    assert ext is not None
    assert ext["slot"] == 254
    assert ext["type"] == "PETG"
    assert ext["color"] == "FF0000"
    assert ext["remain"] == 75
    assert ext["empty"] is False
    assert ext["active"] is True


def test_parse_ams_external_tray_with_units_inactive():
    status = {
        "gcode_state": "RUNNING",
        "mc_percent": 20,
        "ams": {
            "ams": [
                {
                    "id": "0",
                    "humidity": "3",
                    "temp": "27.0",
                    "tray": [
                        {"id": "0", "tray_type": "PLA", "tray_color": "FFFFFF"},
                    ],
                }
            ],
            "tray_now": "0",
            "vt_tray": {
                "id": "254",
                "tray_type": "TPU",
                "tray_color": "000000FF",
            },
        },
    }
    result = parse_ams(status)
    assert result is not None
    assert result["active_tray"] == 0
    assert len(result["units"]) == 1
    assert result["units"][0]["trays"][0]["active"] is True
    ext = result["external_tray"]
    assert ext is not None
    assert ext["type"] == "TPU"
    assert ext["color"] == "000000"
    assert ext["active"] is False  # tray_now is 0 (AMS unit 0 slot 0), not external


def test_read_loaded_ams_material_external_spool_active(monkeypatch):
    from bambu_cli.context import RuntimeContext

    status = {
        "gcode_state": "RUNNING",
        "mc_percent": 50,
        "ams": {
            "ams": [
                {"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]},
            ],
            "tray_now": "254",
        },
        "vt_tray": {
            "id": "254",
            "tray_type": "PETG",
            "tray_color": "00FF00FF",
        },
    }
    fake_printer = MagicMock()
    fake_printer.status.return_value = status
    fake_ctx = MagicMock()
    fake_ctx.printer.return_value = fake_printer
    monkeypatch.setattr(RuntimeContext, "for_request", classmethod(lambda cls, args: fake_ctx))

    active_slot_recorded = []
    material = read_loaded_ams_material(
        argparse.Namespace(),
        on_active_slot=lambda slot: active_slot_recorded.append(slot),
    )
    assert material == "PETG"
    # External spool prints must NOT record an AMS slot for --ams-mapping
    assert active_slot_recorded == []


def test_read_loaded_ams_material_no_ams_units_external_spool(monkeypatch):
    from bambu_cli.context import RuntimeContext

    status = {
        "gcode_state": "IDLE",
        "mc_percent": 0,
        "vt_tray": {
            "id": "254",
            "tray_type": "ABS",
            "tray_color": "000000FF",
        },
    }
    fake_printer = MagicMock()
    fake_printer.status.return_value = status
    fake_ctx = MagicMock()
    fake_ctx.printer.return_value = fake_printer
    monkeypatch.setattr(RuntimeContext, "for_request", classmethod(lambda cls, args: fake_ctx))

    material = read_loaded_ams_material(argparse.Namespace())
    assert material == "ABS"


def _status_with_tray_now(tray_now):
    return {
        "gcode_state": "IDLE",
        "mc_percent": 0,
        "vt_tray": {"id": "254", "tray_type": "PLA", "tray_color": "00FF00FF", "remain": 40},
        "tray_now": tray_now,
    }


def test_tray_now_255_means_nothing_loaded_not_external_spool_active():
    """254 and 255 are different states: 254 = feeding from the external spool,
    255 = nothing loaded. Conflating them showed a false active filament for an
    idle printer with a spool sitting in the holder but not loaded."""
    result = parse_ams(_status_with_tray_now("255"))
    assert result is not None
    assert result["active_tray"] is None  # both sentinels normalize away here
    assert result["external_tray"]["active"] is False


def test_tray_now_254_still_marks_the_external_spool_active():
    result = parse_ams(_status_with_tray_now("254"))
    assert result["external_tray"]["active"] is True


def test_external_spool_inactive_while_an_ams_tray_is_feeding():
    status = _status_with_tray_now("1")
    status["ams"] = {"ams": [{"id": "0", "tray": [{"id": "1", "tray_type": "ABS"}]}], "tray_now": "1"}
    result = parse_ams(status)
    assert result["active_tray"] == 1
    assert result["external_tray"]["active"] is False
    assert result["units"][0]["trays"][0]["active"] is True


def test_external_tray_slot_falls_back_to_the_sentinel_when_id_is_unparseable():
    status = _status_with_tray_now("254")
    status["vt_tray"]["id"] = "not-a-number"
    assert parse_ams(status)["external_tray"]["slot"] == 254
