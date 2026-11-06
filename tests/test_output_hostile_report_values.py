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


_ESC_NAME = "\x1b]52;c;ZXZpbA==\x07\x1b[2Jevil.3mf"


def test_human_status_does_not_pass_terminal_escapes(caplog):
    """gcode_file and tray_type come from the printer; ESC must not reach the terminal."""
    from bambu_cli.commands.status import cmd_status

    data = {
        "gcode_state": "RUNNING",
        "gcode_file": _ESC_NAME,
        "wifi_signal": "-40dBm\x1b[2J",
        "ams": {"tray_now": "0", "ams": [{"id": 0, "tray": [{"id": 0, "remain": 50, "tray_type": "PLA\x1b[2J"}]}]},
        "vt_tray": {"id": 254, "tray_type": "PETG\u202e"},
    }
    printer = MagicMock()
    printer.status.return_value = data
    with caplog.at_level("INFO", logger="bambu"):
        cmd_status(SimpleNamespace(json=False, monitor=False), SimpleNamespace(printer=lambda: printer))
    assert "evil.3mf" in caplog.text
    assert "\x1b" not in caplog.text and "\u202e" not in caplog.text


def test_monitor_status_lines_do_not_pass_terminal_escapes(caplog):
    from bambu_cli.protocols import mqtt_monitor

    client = MagicMock()

    def loop_start():
        userdata = {}
        client.on_connect(client, userdata, None, 0)
        report = {"print": {"gcode_state": "RUNNING\x1b[2J", "mc_percent": 50}}
        client.on_message(client, userdata, SimpleNamespace(payload=json.dumps(report).encode()))
        client.on_message(client, userdata, SimpleNamespace(payload=b'{"print": {"gcode_state": "FINISH"}}'))

    client.loop_start.side_effect = loop_start
    printer = SimpleNamespace(simulation_mode=False, serial="SER", mqtt_timeout=1)
    with (
        caplog.at_level("INFO", logger="bambu"),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(mqtt_monitor, "_client_factory", lambda _f: lambda _printer, **_kw: client)
        mp.setattr(mqtt_monitor, "_connect", lambda _printer, _client: None)
        mp.setattr("sys.stdout.isatty", lambda: False)
        mqtt_monitor.monitor_status(SimpleNamespace(json=False), printer)
    assert "RUNNING" in caplog.text and "FINISH" in caplog.text
    assert "\x1b" not in caplog.text


@pytest.mark.parametrize("bad", [1e999, float("-inf"), float("nan")], ids=repr)
def test_tui_and_monitor_numbers_tolerate_non_finite_values(bad):
    from bambu_cli.protocols.mqtt_monitor import _status_event
    from bambu_cli.tui.services import format_remaining, progress_percent

    assert format_remaining(bad) == "—"
    assert progress_percent(SimpleNamespace(ok=True, raw={"mc_percent": bad})) == 0
    event = _status_event({"gcode_state": "FINISH", "mc_percent": bad, "mc_remaining_time": bad}, "terminal")
    assert event["mc_percent"] == 0 and event["mc_remaining_time"] == 0
