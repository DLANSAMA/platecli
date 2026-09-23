"""A printer command is published at most once, whatever the retries.

MQTT QoS 1 confirms delivery with a PUBACK. When the PUBACK arrives after the
timeout (a slow TLS connect eats most of the 5 s budget), the command has
already reached the printer. The old retry loop built a fresh client with a
fresh "published" flag on every attempt, so ``plate gcode "G1 E50"`` could run
three times and then report ``sent: false`` -- inviting the caller to send it
a fourth time. Retries are still fine while nothing has been published (the
connection never came up); once a publish went out, the result is "sent but
unacknowledged", reported distinctly.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from bambu_cli.constants import EXIT_TIMEOUT
from bambu_cli.errors import BambuError, CommandUnconfirmed
from bambu_cli.protocols import mqtt_cmd
from bambu_cli.protocols.mqtt_session import MqttSession
from tests.test_mqtt_session import FakeBrokerClient, _printer

PAYLOAD = '{"print":{"command":"gcode_line","param":"G1 E50"}}'


class _SlowAck:
    """The broker takes the publish, but the PUBACK lands after the timeout."""

    published: list = []

    def __init__(self, printer, *args):
        self.on_connect = self.on_publish = None

    def user_data_set(self, data):
        pass

    def loop_start(self):
        self.on_connect(self, None, None, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0):
        _SlowAck.published.append(payload)


class _NeverConnects(_SlowAck):
    """TLS/CONNACK never completes within the timeout: nothing is published."""

    attempts = 0

    def loop_start(self):
        _NeverConnects.attempts += 1


def _one_shot_printer():
    return SimpleNamespace(simulation_mode=False, serial="SN", mqtt_timeout=0.05, ip="192.0.2.1", _mqtt_session=None)


@pytest.fixture(autouse=True)
def _reset_fakes():
    _SlowAck.published = []
    _NeverConnects.attempts = 0


def test_one_shot_publishes_once_then_reports_unconfirmed():
    with mock.patch.object(mqtt_cmd, "_connect", lambda p, c: None), pytest.raises(CommandUnconfirmed) as caught:
        mqtt_cmd.send_command(_one_shot_printer(), PAYLOAD, timeout=0.05, client_factory=_SlowAck, sleep=lambda s: None)
    assert len(_SlowAck.published) == 1
    assert caught.value.exit_code == EXIT_TIMEOUT
    assert caught.value.extra["sent"] is True and caught.value.extra["acknowledged"] is False


def test_one_shot_still_retries_while_nothing_was_published():
    with mock.patch.object(mqtt_cmd, "_connect", lambda p, c: None):
        result = mqtt_cmd.send_command(
            _one_shot_printer(), PAYLOAD, timeout=0.01, retries=2, client_factory=_NeverConnects, sleep=lambda s: None
        )
    assert result is False
    assert _NeverConnects.attempts == 3
    assert _SlowAck.published == []


class _SilentAckClient(FakeBrokerClient):
    """A held-session broker that never sends PUBACK for the command."""

    def publish(self, topic, payload, qos=0):
        self.publishes.append((topic, payload, qos))
        return mock.MagicMock(rc=0)


def test_held_session_publishes_once_then_reports_unconfirmed():
    clients = []

    def factory(_printer):
        client = _SilentAckClient()
        clients.append(client)
        return client

    printer = _printer()
    printer._mqtt_session = MqttSession(printer, client_factory=factory, sleep=lambda s: None)
    with pytest.raises(CommandUnconfirmed):
        mqtt_cmd.send_command(printer, PAYLOAD, timeout=0.01, retries=2)
    commands = [p for c in clients for (_t, p, qos) in c.publishes if qos == 1]
    assert commands == [PAYLOAD]
    # A later reconnect must not flush the abandoned command either.
    clients[0].force_drop()
    printer.status(timeout=0.2, require_complete=False)
    commands = [p for c in clients for (_t, p, qos) in c.publishes if qos == 1]
    assert commands == [PAYLOAD]
    printer.release_mqtt()


def test_gcode_command_reports_sent_but_unacknowledged(capsys):
    from bambu_cli.commands.gcode import cmd_gcode

    class _Printer:
        def send_command(self, payload):
            raise CommandUnconfirmed("sent, not acknowledged", extra={"sent": True, "acknowledged": False})

    ctx = SimpleNamespace(printer=lambda: _Printer())
    args = argparse.Namespace(code="G1 E50", confirm=True, json=True)
    with pytest.raises(BambuError) as caught:
        cmd_gcode(args, ctx=ctx)
    assert caught.value.exit_code == EXIT_TIMEOUT
    assert caught.value.extra == {"gcode": "G1 E50", "sent": True, "acknowledged": False}
    assert caught.value.next_command == ["status", "--json"]
    assert json.loads(json.dumps(caught.value.to_error_payload("gcode")))["sent"] is True


class _AckDuringTeardown(_SlowAck):
    """The PUBACK arrives while the network loop is being stopped."""

    def loop_stop(self):
        self.on_publish(self, None, 1)


def test_ack_that_lands_during_teardown_counts():
    # Found in review: the decision used the already-expired wait() result, so
    # an acknowledged command was reported as unacknowledged (exit 6).
    with mock.patch.object(mqtt_cmd, "_connect", lambda p, c: None):
        result = mqtt_cmd.send_command(
            _one_shot_printer(), PAYLOAD, timeout=0.02, client_factory=_AckDuringTeardown, sleep=lambda s: None
        )
    assert result is True
    assert len(_SlowAck.published) == 1
