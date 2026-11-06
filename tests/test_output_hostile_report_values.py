"""Hostile MQTT report values must neither crash parsing nor produce invalid JSON."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bambu_cli.ams import parse_ams

_BAD = [
    float("inf"),
    float("-inf"),
    float("nan"),
    1e999,
    10**400,
    "x",
    [],
    {},
    None,
    [1],
    {"a": 1},
]


def _reject(name):
    raise ValueError(f"invalid JSON constant {name}")


def test_status_json_is_strict_json(capsys):
    from bambu_cli.ams import parse_ams
    from bambu_cli.commands.status import _status_payload
    from bambu_cli.utils import emit_json

    data = json.loads('{"nozzle_temper": NaN, "bed_temper": Infinity, "extra": [-Infinity, {"a": NaN}]}')
    emit_json(_status_payload(data, parse_ams(data)))
    out = json.loads(capsys.readouterr().out, parse_constant=_reject)
    assert out["printer"]["nozzle_temper"] is None
    assert out["printer"]["bed_temper"] is None
    assert out["printer"]["extra"] == [None, {"a": None}]


@pytest.mark.parametrize("bad", _BAD, ids=repr)
def test_parse_ams_tolerates_any_value(bad):
    status = {
        "ams": {
            "tray_now": bad,
            "ams": [
                {"id": bad, "humidity": bad, "temp": bad, "tray": bad},
                {"id": 0, "humidity": bad, "tray": [{"id": bad, "remain": bad, "tray_type": bad, "tray_color": bad}]},
            ],
        },
        "vt_tray": {"id": bad, "remain": bad, "tray_type": "PLA"},
        "tray_now": bad,
    }
    result = parse_ams(status)
    assert result is None or isinstance(result["units"], list)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("bad", _BAD, ids=repr)
def test_human_status_tolerates_any_value(bad):
    from bambu_cli.commands.status import cmd_status

    data = {
        "gcode_state": "RUNNING",
        "mc_remaining_time": bad,
        "mc_percent": bad,
        "layer_num": bad,
        "ams": {"tray_now": bad, "ams": [{"id": 0, "tray": [{"id": 0, "remain": bad, "tray_type": "PLA"}]}]},
    }
    printer = MagicMock()
    printer.status.return_value = data
    ctx = SimpleNamespace(printer=lambda: printer)
    cmd_status(SimpleNamespace(json=False, monitor=False), ctx)
