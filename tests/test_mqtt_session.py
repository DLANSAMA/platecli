"""Reusable MQTT session: one TLS client across status/send_command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from bambu_cli.errors import PrinterStatusIncomplete
from bambu_cli.printer import BambuPrinter
from bambu_cli.protocols.mqtt import get_status, get_version, send_command
from bambu_cli.protocols.mqtt_session import MqttSession

_FULL = {
    "gcode_state": "IDLE",
    "mc_percent": 0,
    "bed_temper": 25.0,
    "nozzle_temper": 25.0,
}


class FakeBrokerClient:
    """In-memory paho stand-in: connect/publish drive the session callbacks."""

    def __init__(self, *, status_replies=None, version_reply=None, connect_rc=0, double_connect=False):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_publish = None
        self.connects = 0
        self.disconnects = 0
        self.publishes: list[tuple] = []
        self.subscribes: list[str] = []
        self.loop_started = False
        self._connected = False
        self._status_replies = list(status_replies or [])
        self._status_repeat = None if status_replies else {"print": dict(_FULL)}
        self._version_reply = version_reply
        self._connect_rc = connect_rc
        self._double_connect = double_connect

    def user_data_set(self, data):
        pass

    def connect(self, host, port, keepalive=10):
        self.connects += 1
        self._connected = self._connect_rc == 0
        if self.on_connect:
            self.on_connect(self, None, None, self._connect_rc)
            if self._double_connect and self._connect_rc == 0:
                self.on_connect(self, None, None, 0)

    def subscribe(self, topic):
        self.subscribes.append(topic)

    def publish(self, topic, payload, qos=0):
        self.publishes.append((topic, payload, qos))
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            data = {}
        if isinstance(data, dict) and "pushing" in data:
            reply = self._status_replies.pop(0) if self._status_replies else self._status_repeat
            if reply is not None:
                self._deliver(reply)
        elif isinstance(data, dict) and isinstance(data.get("info"), dict) and self._version_reply is not None:
            self._deliver(self._version_reply)
        if self.on_publish:
            self.on_publish(self, None, 1)
        return MagicMock(rc=0)

    def _deliver(self, payload):
        if self.on_message is None:
            return
        msg = MagicMock()
        raw = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
        msg.payload = raw
        self.on_message(self, None, msg)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnects += 1
        self._connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, 0)

    def force_drop(self):
        self._connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, 0)

    def is_connected(self):
        return self._connected


def _printer(**kwargs):
    return BambuPrinter(
        ip="192.168.1.9",
        serial="01S",
        access_code="code",
        **kwargs,
    )


def _held(printer, factory, sleep=lambda _s: None):
    session = MqttSession(printer, client_factory=factory, sleep=sleep)
    printer._mqtt_session = session
    return session


def test_hold_mqtt_is_noop_in_simulation():
    printer = _printer(simulation_mode=True)
    printer.hold_mqtt()
    assert printer.mqtt_held is False
    assert printer.status()["gcode_state"] == "IDLE"
    printer.release_mqtt()


def test_two_status_calls_reuse_one_client():
    clients = []

    def factory(_printer):
        client = FakeBrokerClient()
        clients.append(client)
        return client

    printer = _printer()
    _held(printer, factory)
    first = get_status(printer, timeout=1)
    second = get_status(printer, timeout=1)
    assert first["gcode_state"] == "IDLE"
    assert second["gcode_state"] == "IDLE"
    assert len(clients) == 1
    assert clients[0].connects == 1
    assert clients[0].disconnects == 0
    assert len([p for p in clients[0].publishes if "pushall" in str(p[1])]) == 2
    printer.release_mqtt()
    assert clients[0].disconnects == 1
    assert printer.mqtt_held is False


def test_drop_reconnects_on_next_status():
    clients = []

    def factory(_printer):
        client = FakeBrokerClient()
        clients.append(client)
        return client

    printer = _printer()
    _held(printer, factory)
    assert get_status(printer, timeout=1)["mc_percent"] == 0
    clients[0].force_drop()
    assert get_status(printer, timeout=1)["gcode_state"] == "IDLE"
    assert len(clients) == 2
    assert clients[0].disconnects >= 1
    printer.release_mqtt()


def test_send_command_on_connect_publishes_once():
    clients = []

    def factory(_printer):
        client = FakeBrokerClient(double_connect=True)
        clients.append(client)
        return client

    printer = _printer()
    _held(printer, factory)
    assert send_command(printer, '{"print":{"command":"pause"}}', timeout=1) is True
    command_publishes = [p for p in clients[0].publishes if p[2] == 1]
    assert len(command_publishes) == 1
    printer.release_mqtt()


def test_reconnect_after_command_does_not_republish():
    clients = []

    def factory(_printer):
        client = FakeBrokerClient()
        clients.append(client)
        return client

    printer = _printer()
    _held(printer, factory)
    assert send_command(printer, '{"print":{"command":"pause"}}', timeout=1) is True
    clients[0].force_drop()
    assert get_status(printer, timeout=1) is not None
    pause_publishes = [
        payload
        for _topic, payload, qos in clients[0].publishes + clients[1].publishes
        if qos == 1 and "pause" in str(payload)
    ]
    assert len(pause_publishes) == 1
    printer.release_mqtt()


def test_oneshot_status_still_disconnects():
    clients = []

    def factory(_printer):
        client = FakeBrokerClient()
        clients.append(client)
        return client

    printer = _printer()
    from unittest.mock import patch

    with patch("bambu_cli.protocols.mqtt.create_mqtt_client", side_effect=factory):
        result = get_status(printer, timeout=1)
    assert result["gcode_state"] == "IDLE"
    assert len(clients) == 1
    assert clients[0].disconnects == 1


def test_session_incomplete_raises():
    def factory(_printer):
        return FakeBrokerClient(status_replies=[{"print": {"wifi_signal": "-40dBm"}}] * 4)

    printer = _printer()
    _held(printer, factory)
    try:
        get_status(printer, timeout=0.01, retries=0)
        raise AssertionError("expected PrinterStatusIncomplete")
    except PrinterStatusIncomplete as exc:
        assert "missing" in str(exc).lower()
    printer.release_mqtt()


def test_session_returns_cached_complete_when_pushall_silent():
    """Background reports already filled state; a silent pushall must not raise."""

    def factory(_printer):
        return FakeBrokerClient(status_replies=[None])

    printer = _printer()
    session = _held(printer, factory)
    assert session.ensure_connected(1)
    session._print_state.update(_FULL)
    result = get_status(printer, timeout=0.01, retries=0)
    assert result is not None
    assert result["gcode_state"] == "IDLE"
    assert result["mc_percent"] == 0
    printer.release_mqtt()


def test_session_second_status_uses_cache_if_pushall_times_out():
    def factory(_printer):
        return FakeBrokerClient(status_replies=[{"print": dict(_FULL)}, None, None])

    printer = _printer()
    _held(printer, factory)
    first = get_status(printer, timeout=0.01, retries=0)
    second = get_status(printer, timeout=0.01, retries=0)
    assert first is not None and first["gcode_state"] == "IDLE"
    assert second is not None and second["gcode_state"] == "IDLE"
    printer.release_mqtt()


def test_session_liveness_accepts_partial():
    def factory(_printer):
        return FakeBrokerClient(status_replies=[{"print": {"wifi_signal": "-40dBm"}}])

    printer = _printer()
    _held(printer, factory)
    result = get_status(printer, timeout=1, retries=0, require_complete=False)
    assert result == {"wifi_signal": "-40dBm"}
    printer.release_mqtt()


def test_session_get_version():
    def factory(_printer):
        return FakeBrokerClient(
            version_reply={"info": {"command": "get_version", "module": [{"name": "ota", "sw_ver": "1.0"}]}}
        )

    printer = _printer()
    _held(printer, factory)
    assert get_version(printer, timeout=1) == [{"name": "ota", "sw_ver": "1.0"}]
    printer.release_mqtt()


def test_session_get_version_timeout_returns_none():
    def factory(_printer):
        return FakeBrokerClient(version_reply=None)

    printer = _printer()
    _held(printer, factory)
    assert get_version(printer, timeout=0.01, retries=0) is None
    printer.release_mqtt()


def test_ready_snapshot_rejects_empty_and_incomplete():
    printer = _printer()
    session = _held(printer, lambda _p: FakeBrokerClient(status_replies=[None]))
    assert session.ensure_connected(1)
    assert session._ready_snapshot(require_complete=True) is None
    session._print_state.update({"wifi_signal": "-40dBm"})
    assert session._ready_snapshot(require_complete=True) is None
    assert session._ready_snapshot(require_complete=False) == {"wifi_signal": "-40dBm"}
    printer.release_mqtt()


def test_connect_rc_failure_returns_none():
    def factory(_printer):
        return FakeBrokerClient(connect_rc=4)

    printer = _printer()
    _held(printer, factory)
    assert get_status(printer, timeout=1, retries=0) is None
    assert send_command(printer, "{}", timeout=1, retries=0) is False
    printer.release_mqtt()


def test_oserror_on_connect_retries_then_succeeds():
    calls = {"n": 0}

    def factory(_printer):
        calls["n"] += 1
        if calls["n"] == 1:

            class Boom:
                def user_data_set(self, data):
                    pass

                def connect(self, *args, **kwargs):
                    raise OSError("down")

                def loop_start(self):
                    pass

                def loop_stop(self):
                    pass

                def disconnect(self):
                    pass

            return Boom()
        return FakeBrokerClient()

    printer = _printer()
    _held(printer, factory)
    assert get_status(printer, timeout=1, retries=1)["gcode_state"] == "IDLE"
    assert calls["n"] == 2
    printer.release_mqtt()


def test_status_timeout_then_retry_succeeds():
    def factory(_printer):
        return FakeBrokerClient(status_replies=[None, {"print": dict(_FULL)}])

    printer = _printer()
    _held(printer, factory)
    result = get_status(printer, timeout=0.01, retries=1)
    assert result is not None
    assert result["gcode_state"] == "IDLE"
    printer.release_mqtt()


def test_hold_mqtt_and_release_are_idempotent():
    printer = _printer()
    created = []

    def factory(_printer):
        created.append(FakeBrokerClient())
        return created[-1]

    printer.hold_mqtt(client_factory=factory)
    printer.hold_mqtt(client_factory=factory)
    assert printer.mqtt_held is True
    get_status(printer, timeout=1)
    assert len(created) == 1
    printer.release_mqtt()
    printer.release_mqtt()
    assert printer.mqtt_held is False
    assert created[0].disconnects == 1
