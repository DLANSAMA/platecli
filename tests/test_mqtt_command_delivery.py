"""A printer command goes out at most once, and an unacknowledged one says so.

Found in the 2026-11 audit; each test failed before its fix:

* paho's network loop reconnected after a drop and resent the unacknowledged
  QoS-1 PUBLISH (DUP set), so a command that reached the printer just before
  the connection died ran twice — while ``send_command`` reported success;
* a print start whose acknowledgement never came was reported as
  ``printed: false`` although the command had been published, inviting a
  retry that starts a second print;
* a malformed report (deeply nested JSON, an over-long integer, non-object
  JSON) raised out of ``on_message`` and killed paho's network thread.
"""

from __future__ import annotations

import importlib
import socket
import sys
import threading
import types

import pytest

from bambu_cli import utils
from bambu_cli.constants import EXIT_NETWORK_ERROR, EXIT_TIMEOUT
from bambu_cli.errors import BambuError, CommandUnconfirmed
from bambu_cli.printer import BambuPrinter
from bambu_cli.protocols import mqtt_cmd, mqtt_print, mqtt_tls
from bambu_cli.protocols.mqtt_cmd import decode_report
from bambu_cli.protocols.mqtt_session import MqttSession

# --- a broker that drops the connection instead of sending PUBACK -----------


def _read_packet(conn):
    header = conn.recv(1)
    if not header:
        return None
    multiplier, length = 1, 0
    while True:
        byte = conn.recv(1)[0]
        length += (byte & 127) * multiplier
        multiplier *= 128
        if not byte & 128:
            break
    body = b""
    while len(body) < length:
        chunk = conn.recv(length - len(body))
        if not chunk:
            return None
        body += chunk
    return header[0], body


class _DroppingBroker:
    """Accepts CONNECT, records every PUBLISH, and hangs up instead of a PUBACK."""

    def __init__(self):
        self.connections = 0
        self.publishes = 0
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                continue
            self.connections += 1
            with conn:
                conn.settimeout(2)
                try:
                    while True:
                        packet = _read_packet(conn)
                        if packet is None:
                            break
                        kind = packet[0] >> 4
                        if kind == 1:  # CONNECT -> CONNACK
                            conn.sendall(b"\x20\x02\x00\x00")
                        elif kind == 3:  # PUBLISH -> hang up, no PUBACK
                            self.publishes += 1
                            break
                        elif kind == 14:  # DISCONNECT
                            break
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)
        self._server.close()


@pytest.fixture
def real_paho(monkeypatch):
    """The real paho client module for one test.

    tests/bambu_test_base.py replaces paho with a MagicMock for the whole run;
    the redelivery bug lives inside paho's own reconnect loop, so only the real
    library can show it. The stub is put back before the test body runs.
    """
    is_paho = lambda name: name == "paho" or name.startswith("paho.")  # noqa: E731
    stub = {name: sys.modules.pop(name) for name in list(sys.modules) if is_paho(name)}
    try:
        real = importlib.import_module("paho.mqtt.client")
    finally:
        for name in [name for name in sys.modules if is_paho(name)]:
            del sys.modules[name]
        sys.modules.update(stub)
    monkeypatch.setattr(mqtt_tls, "mqtt", real)
    return real


@pytest.fixture
def broker():
    b = _DroppingBroker()
    yield b
    b.close()


def _printer(port, timeout=3.0):
    printer = BambuPrinter("127.0.0.1", "SER", "code")
    printer.mqtt_port = port
    printer.mqtt_timeout = timeout
    return printer


def test_a_dropped_command_is_not_resent_by_the_client(real_paho, broker):
    """Real paho against a local broker: one PUBLISH, reported as unconfirmed."""
    printer = _printer(broker.port)
    with pytest.raises(CommandUnconfirmed) as excinfo:
        mqtt_cmd.send_command(
            printer,
            '{"print": {"command": "gcode_line", "param": "G91 G1 Z10"}}',
            retries=2,
            client_factory=lambda _printer, client_id="": mqtt_tls._paho_client(client_id),
            sleep=lambda _s: None,
        )
    assert excinfo.value.extra == {"sent": True, "acknowledged": False}
    assert broker.publishes == 1, "the command reached the printer more than once"
    assert broker.connections == 1, "the client reconnected on its own after the drop"


# --- print start: published but never acknowledged ---------------------------


class _SilentPrinterClient:
    """Connects and accepts the publish; the printer never answers."""

    def __init__(self, fail_after_publish=None):
        self.published = []
        self._fail_after_publish = fail_after_publish

    def user_data_set(self, _data):
        pass

    def connect(self, *_args, **_kwargs):
        pass

    def subscribe(self, _topic):
        pass

    def publish(self, _topic, payload, qos=0):
        self.published.append(payload)

    def loop_start(self):
        self.on_connect(self, None, None, 0)
        if self._fail_after_publish is not None:
            raise self._fail_after_publish

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def _run_print(client):
    printer = BambuPrinter("127.0.0.1", "SER", "code")
    utils._LAST_ERROR_PAYLOAD = None
    with pytest.raises(BambuError) as excinfo:
        mqtt_print.execute_print_command(
            printer,
            '{"print": {"command": "project_file"}}',
            "cube.3mf",
            # The ack wait is command_timeout + 5 s; keep the test fast.
            command_timeout=-4.9,
            client_factory=lambda _printer, _client_id="": client,
        )
    return excinfo.value, utils._LAST_ERROR_PAYLOAD


def test_unacknowledged_print_start_is_reported_as_sent_not_as_not_printed():
    client = _SilentPrinterClient()
    exc, payload = _run_print(client)
    assert len(client.published) == 1
    assert exc.exit_code == EXIT_TIMEOUT
    assert payload["printed"] is None
    assert payload["sent"] is True
    assert payload["acknowledged"] is False
    assert payload["next_command"] == ["status", "--json"]
    assert "check `plate status` before printing again" in payload["error"]


def test_a_network_error_after_publishing_is_also_unconfirmed():
    client = _SilentPrinterClient(fail_after_publish=OSError("connection reset"))
    exc, payload = _run_print(client)
    assert len(client.published) == 1
    assert exc.exit_code == EXIT_NETWORK_ERROR
    assert payload["printed"] is None
    assert payload["sent"] is True


def test_nothing_published_still_reports_not_printed():
    class _NeverConnects(_SilentPrinterClient):
        def loop_start(self):
            pass

    client = _NeverConnects()
    exc, payload = _run_print(client)
    assert client.published == []
    assert exc.exit_code == EXIT_TIMEOUT
    assert payload["printed"] is False
    assert "sent" not in payload


# --- hostile reports never escape the network thread -------------------------

HOSTILE_PAYLOADS = [
    b"[" * 200_000,  # RecursionError in json.loads
    b"1" * 5_000,  # ValueError: integer string past the digit limit
    b"5",
    b"[1]",
    b'"text"',
    b"null",
    b"\xff\xfe",
    b"{not json",
]


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_decode_report_returns_none_for_anything_but_an_object(payload):
    assert decode_report(types.SimpleNamespace(payload=payload)) is None


def test_decode_report_passes_a_report_through():
    msg = types.SimpleNamespace(payload=b'{"print": {"gcode_state": "IDLE"}}')
    assert decode_report(msg) == {"print": {"gcode_state": "IDLE"}}


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_held_session_survives_a_hostile_report(payload):
    session = MqttSession(BambuPrinter("127.0.0.1", "SER", "code"))
    session._on_message(None, None, types.SimpleNamespace(payload=payload))
    session._on_message(None, None, types.SimpleNamespace(payload=b'{"print": {"gcode_state": "RUNNING"}}'))
    assert session._snapshot() == {"gcode_state": "RUNNING"}


def test_the_raised_error_carries_the_unconfirmed_fields():
    """The --json envelope is built from the exception, so it must say so too."""
    exc, _payload = _run_print(_SilentPrinterClient())
    envelope = exc.to_error_payload("print")
    assert envelope["printed"] is None
    assert envelope["sent"] is True
    assert envelope["acknowledged"] is False
    assert envelope["next_command"] == ["status", "--json"]
    assert "was sent, but the printer did not acknowledge it" in envelope["error"]
    assert exc.logged, "the line is already logged; the CLI must not print it twice"


# --- the status monitor still rides out a dropped connection ---------------------


def _publish_packet(topic, payload):
    body = len(topic).to_bytes(2, "big") + topic + payload
    length, encoded = len(body), b""
    while True:
        byte, length = length % 128, length // 128
        encoded += bytes([byte | (128 if length else 0)])
        if not length:
            return b"\x30" + encoded + body


class _DropOnceBroker:
    """First connection: a RUNNING report, then a hang-up. Later ones: FINISH."""

    def __init__(self):
        self.connections = 0
        self.client_ids = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _report(self, state, pct):
        return _publish_packet(b"device/SER/report", f'{{"print": {{"gcode_state": "{state}", "mc_percent": {pct}}}}}'.encode())

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                continue
            self.connections += 1
            first = self.connections == 1
            with conn:
                conn.settimeout(3)
                try:
                    while True:
                        packet = _read_packet(conn)
                        if packet is None:
                            break
                        kind, body = packet[0] >> 4, packet[1]
                        if kind == 1:  # CONNECT: protocol name, level, flags, keepalive, then the client id
                            id_len = int.from_bytes(body[10:12], "big")
                            self.client_ids.append(body[12 : 12 + id_len].decode())
                            conn.sendall(b"\x20\x02\x00\x00")
                        elif kind == 8:  # SUBSCRIBE -> SUBACK, then a report
                            conn.sendall(bytes([0x90, 3, body[0], body[1], 0]))
                            conn.sendall(self._report("RUNNING", 10) if first else self._report("FINISH", 100))
                            if first:
                                break  # hang up mid-print
                        elif kind == 14:
                            break
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)
        self._server.close()


def test_status_monitor_reconnects_after_a_drop(real_paho, monkeypatch, capsys):
    """The monitor relied on paho's auto-reconnect; turning it off for commands hung it."""
    from bambu_cli.protocols import mqtt as mqtt_facade
    from bambu_cli.protocols.mqtt_monitor import monitor_status

    broker = _DropOnceBroker()
    monkeypatch.setattr(
        mqtt_facade,
        "create_mqtt_client",
        lambda _printer, client_id="", reconnect=False: mqtt_tls._paho_client(client_id, reconnect=reconnect),
    )
    done = []
    worker = threading.Thread(
        target=lambda: (monitor_status(types.SimpleNamespace(json=True), _printer(broker.port)), done.append(True)),
        daemon=True,
    )
    try:
        worker.start()
        worker.join(15)
    finally:
        broker.close()
    assert done, "the monitor never saw FINISH after the connection dropped"
    assert broker.connections >= 2
    assert all(cid.startswith("plate-") for cid in broker.client_ids), broker.client_ids
    assert '"terminal"' in capsys.readouterr().out


def test_decode_report_drops_reports_nested_past_what_the_json_walkers_handle():
    deep = b'{"a":' * 5000 + b"1" + b"}" * 5000
    assert decode_report(types.SimpleNamespace(payload=deep)) is None
    real = b'{"print": {"ams": {"ams": [{"tray": [{"id": "0", "tray_type": "PLA"}]}]}}}'
    assert decode_report(types.SimpleNamespace(payload=real))["print"]["ams"]["ams"][0]["tray"][0]["id"] == "0"
