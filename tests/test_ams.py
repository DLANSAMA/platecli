"""Tests for AMS status parsing and its exposure through `status --json`."""
import json
import types
from unittest.mock import MagicMock, patch

from bambu_cli import commands, context
from bambu_cli.ams import parse_ams

STATUS_WITH_AMS = {
    "gcode_state": "RUNNING",
    "mc_percent": 40,
    "ams": {
        "ams": [
            {
                "id": "0",
                "humidity": "4",
                "temp": "28.5",
                "tray": [
                    {"id": "0", "tray_type": "PLA", "tray_color": "F2F2F2FF", "remain": 80},
                    {"id": "1", "tray_type": "PETG", "tray_color": "0A0AC8FF", "remain": 55},
                    {"id": "2"},  # empty slot
                    {"id": "3", "tray_type": "ABS", "tray_color": "000000FF", "remain": -1},
                ],
            }
        ],
        "tray_now": "1",
    },
}


def test_parse_ams_normalizes_units_and_trays():
    result = parse_ams(STATUS_WITH_AMS)
    assert result["active_tray"] == 1
    assert len(result["units"]) == 1

    unit = result["units"][0]
    assert unit["id"] == 0
    assert unit["humidity"] == 4
    assert unit["temp"] == 28.5

    trays = unit["trays"]
    assert [t["slot"] for t in trays] == [0, 1, 2, 3]

    pla = trays[0]
    assert pla["type"] == "PLA"
    assert pla["color"] == "F2F2F2"   # alpha dropped, upper-cased
    assert pla["remain"] == 80
    assert pla["empty"] is False
    assert pla["active"] is False

    petg = trays[1]
    assert petg["active"] is True     # absolute index 1 == tray_now

    empty = trays[2]
    assert empty["type"] is None
    assert empty["color"] is None
    assert empty["remain"] is None
    assert empty["empty"] is True
    assert empty["active"] is False

    abs_tray = trays[3]
    assert abs_tray["remain"] == -1   # printer reports -1 when it can't measure


def test_active_tray_across_second_unit():
    status = {
        "ams": {
            "tray_now": "5",  # unit 1, slot 1 -> 1*4 + 1
            "ams": [
                {"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]},
                {"id": "1", "tray": [
                    {"id": "0", "tray_type": "PLA"},
                    {"id": "1", "tray_type": "TPU"},
                ]},
            ],
        }
    }
    result = parse_ams(status)
    unit1_slot1 = result["units"][1]["trays"][1]
    assert unit1_slot1["type"] == "TPU"
    assert unit1_slot1["active"] is True
    assert result["units"][0]["trays"][0]["active"] is False


def test_parse_ams_returns_none_without_ams():
    assert parse_ams(None) is None
    assert parse_ams({}) is None
    assert parse_ams({"ams": {}}) is None           # no units list
    assert parse_ams({"ams": {"ams": []}}) is None   # empty units list
    # A simulated status (no ams key) yields None.
    assert parse_ams({"gcode_state": "IDLE", "mc_percent": 0}) is None


def test_cmd_status_json_includes_parsed_ams(capsys):
    args = types.SimpleNamespace(json=True, monitor=False, sim=False, verbose=False)
    fake_printer = MagicMock()
    fake_printer.status.return_value = STATUS_WITH_AMS
    ctx = context.RuntimeContext(
        settings=context.Settings(printer_ip="1.2.3.4", serial="S"),
        simulation=False,
    )
    with patch.object(ctx, "printer", return_value=fake_printer):
        commands.cmd_status(args, ctx=ctx)

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "status"
    assert payload["ams"]["active_tray"] == 1
    assert payload["ams"]["units"][0]["trays"][1]["type"] == "PETG"
    assert payload["ams"]["units"][0]["trays"][1]["active"] is True


def test_cmd_status_json_ams_none_without_hardware(capsys):
    args = types.SimpleNamespace(json=True, monitor=False, sim=False, verbose=False)
    fake_printer = MagicMock()
    fake_printer.status.return_value = {"gcode_state": "IDLE", "mc_percent": 0}
    ctx = context.RuntimeContext(
        settings=context.Settings(printer_ip="1.2.3.4", serial="S"),
        simulation=False,
    )
    with patch.object(ctx, "printer", return_value=fake_printer):
        commands.cmd_status(args, ctx=ctx)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ams"] is None


def test_sim_status_exposes_parseable_ams():
    from bambu_cli.protocols.mqtt import get_status

    printer = MagicMock()
    printer.simulation_mode = True
    ams = parse_ams(get_status(printer))
    assert ams is not None
    types_loaded = [t["type"] for t in ams["units"][0]["trays"]]
    assert "PLA" in types_loaded
    assert any(t["empty"] for t in ams["units"][0]["trays"])
