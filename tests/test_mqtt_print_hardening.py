"""Hardening tests for unique MQTT print client IDs and QoS-1 print publish."""

from __future__ import annotations

import types

from bambu_cli.protocols import mqtt_print, mqtt_tls
from tests.bambu_test_base import _test_printer


def test_execute_print_command_generates_unique_bounded_client_ids(monkeypatch):
    captured_ids: list[str] = []

    class _FakePrintClient:
        def __init__(self):
            self.on_connect = None
            self.on_message = None

        def connect(self, *_args, **_kwargs):
            pass

        def subscribe(self, _topic):
            pass

        def publish(self, _topic, _payload, qos=0):
            pass

        def loop_start(self):
            self.on_connect(self, None, None, 0)
            msg = types.SimpleNamespace(payload=b'{"print": {"command": "project_file"}}')
            self.on_message(self, None, msg)

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    monkeypatch.setattr(mqtt_print, "_connect", lambda client, printer: None)
    printer = _test_printer(simulation_mode=False)

    def _factory(_printer, client_id=""):
        captured_ids.append(client_id)
        return _FakePrintClient()

    mqtt_print.execute_print_command(printer, '{"print": {}}', "cube.3mf", client_factory=_factory)
    mqtt_print.execute_print_command(printer, '{"print": {}}', "cube.3mf", client_factory=_factory)

    assert len(captured_ids) == 2
    assert captured_ids[0] != captured_ids[1]
    for cid in captured_ids:
        assert cid.startswith("bambu_print-")
        assert len(cid) <= 23


def test_execute_print_command_publishes_with_qos_1(monkeypatch):
    published: list[tuple[str, str, int]] = []

    class _FakePrintClient:
        def __init__(self):
            self.on_connect = None
            self.on_message = None

        def connect(self, *_args, **_kwargs):
            pass

        def subscribe(self, _topic):
            pass

        def publish(self, topic, payload, qos=0):
            published.append((topic, payload, qos))

        def loop_start(self):
            self.on_connect(self, None, None, 0)
            msg = types.SimpleNamespace(payload=b'{"print": {"command": "project_file"}}')
            self.on_message(self, None, msg)

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    monkeypatch.setattr(mqtt_print, "_connect", lambda client, printer: None)

    printer = _test_printer(simulation_mode=False)
    payload = '{"print": {"command": "project_file"}}'
    fake_client = _FakePrintClient()

    mqtt_print.execute_print_command(
        printer,
        payload,
        "cube.3mf",
        client_factory=lambda _printer, _client_id="": fake_client,
    )

    assert published == [(f"device/{printer.serial}/request", payload, 1)]


def test_sim_mqtt_client_publish_accepts_qos_and_invokes_on_publish():
    client = mqtt_tls._SimMqttClient()
    events: list[tuple[object, object, int]] = []
    client.on_publish = lambda c, userdata, mid: events.append((c, userdata, mid))

    client.publish("device/x/request", "{}", qos=1)

    assert events == [(client, None, 1)]
