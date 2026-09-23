"""`status --monitor` must fail loudly when it never reaches the printer.

Before the 2026-09-22 fix a refused connection (wrong access code, rc=5) set
the "terminal" event and returned normally: exit 0, no NDJSON on stdout, so an
agent could not tell "never connected" from success. A socket/TLS error
escaped as a raw OSError ("Unexpected error", exit 5), and a broker that never
answered CONNACK left the monitor waiting forever.
"""

from __future__ import annotations

import argparse
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from bambu_cli.constants import EXIT_NETWORK_ERROR
from bambu_cli.errors import PrinterConnectionError
from bambu_cli.protocols import mqtt_monitor


class _Client:
    def __init__(self, on_loop_start=None):
        self.on_connect = self.on_message = None
        self._on_loop_start = on_loop_start
        self.stopped = False
        self.userdata = None

    def user_data_set(self, data):
        # paho hands this object back as the callbacks' userdata argument.
        self.userdata = data

    def subscribe(self, topic):
        pass

    def publish(self, topic, payload, qos=0):
        pass

    def loop_start(self):
        if self._on_loop_start:
            self._on_loop_start(self)

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        pass


def _printer(timeout=0.2):
    return SimpleNamespace(simulation_mode=False, serial="SN", mqtt_timeout=timeout)


def _run(client, *, connect=lambda p, c: None, timeout=0.2):
    with (
        mock.patch.object(mqtt_monitor, "_client_factory", lambda f: lambda printer: client),
        mock.patch.object(mqtt_monitor, "_connect", connect),
    ):
        return mqtt_monitor.monitor_status(argparse.Namespace(json=True), _printer(timeout))


def test_refused_connection_is_a_network_error(capsys):
    client = _Client(on_loop_start=lambda c: c.on_connect(c, None, None, 5))
    with pytest.raises(PrinterConnectionError) as caught:
        _run(client)
    assert caught.value.exit_code == EXIT_NETWORK_ERROR
    assert "rc=5" in str(caught.value) and "access code" in str(caught.value)
    assert capsys.readouterr().out == ""
    assert client.stopped


def test_socket_error_on_connect_is_a_network_error():
    def refuse(printer, client):
        raise ConnectionRefusedError(111, "Connection refused")

    with pytest.raises(PrinterConnectionError) as caught:
        _run(_Client(), connect=refuse)
    assert caught.value.exit_code == EXIT_NETWORK_ERROR
    assert "Connection refused" in str(caught.value)


def test_silent_broker_times_out_instead_of_waiting_forever():
    outcome = {}

    def target():
        try:
            _run(_Client(), timeout=0.1)
            outcome["result"] = "returned"
        except PrinterConnectionError as exc:
            outcome["result"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "monitor never gave up on a silent broker"
    assert isinstance(outcome.get("result"), PrinterConnectionError)
    assert outcome["result"].exit_code == EXIT_NETWORK_ERROR


def test_messages_count_as_connected(capsys):
    import json

    def deliver(c):
        c.on_connect(c, c.userdata, None, 0)
        msg = mock.MagicMock()
        msg.payload = json.dumps({"print": {"gcode_state": "FINISH", "mc_percent": 100}}).encode()
        c.on_message(c, c.userdata, msg)

    _run(_Client(on_loop_start=deliver))
    assert '"terminal"' in capsys.readouterr().out
