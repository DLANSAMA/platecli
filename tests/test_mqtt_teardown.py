"""MQTT teardown must never block on a publish the printer did not acknowledge.

A P1P on firmware 01.09.01.00 applied ``light on`` but never sent the QoS 1
acknowledgement. The caller's wait expired, ``loop_stop()`` then joined paho's
network thread, and that thread does not exit while a QoS 1 publish is
unacknowledged, so ``plate light`` hung forever and printed nothing. Disconnecting
first lets the thread exit. These tests pin the order and prove it against a real
paho client talking to a loopback broker that never acknowledges.
"""

import ast
import pathlib
import subprocess
import sys

import pytest

from bambu_cli.protocols.mqtt_cmd import _teardown_mqtt_client

REPO = pathlib.Path(__file__).resolve().parent.parent
PROTOCOLS = REPO / "bambu_cli" / "protocols"


class _RecordingClient:
    def __init__(self, fail_on=None):
        self.calls = []
        self._fail_on = fail_on

    def disconnect(self):
        self.calls.append("disconnect")
        if self._fail_on == "disconnect":
            raise RuntimeError("boom")

    def loop_stop(self):
        self.calls.append("loop_stop")
        if self._fail_on == "loop_stop":
            raise RuntimeError("boom")


def test_teardown_disconnects_before_it_stops_the_loop():
    client = _RecordingClient()
    _teardown_mqtt_client(client)
    assert client.calls == ["disconnect", "loop_stop"]


@pytest.mark.parametrize("failing", ["disconnect", "loop_stop"])
def test_teardown_still_does_both_when_one_step_raises(failing):
    client = _RecordingClient(fail_on=failing)
    _teardown_mqtt_client(client)
    assert client.calls == ["disconnect", "loop_stop"]


def test_no_mqtt_module_stops_the_loop_except_through_the_shared_teardown():
    """Every MQTT path shares one teardown, so the order cannot drift in a sister command."""
    offenders = []
    for path in sorted(PROTOCOLS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                is_call = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                if is_call and node.func.attr == "loop_stop" and func.name not in {"_teardown_mqtt_client"}:
                    offenders.append(f"{path.name}:{node.lineno} in {func.name}")
    # _SimMqttClient defines loop_stop (a def, not a call), so only real calls appear here.
    assert offenders == []


_CHILD = r"""
import importlib.util, socket, sys, threading
import paho.mqtt.client as mqtt
from bambu_cli.protocols.mqtt_cmd import _teardown_mqtt_client

spec = importlib.util.spec_from_file_location("loopback_broker", sys.argv[2])
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)

acknowledge = sys.argv[1] == "ack"
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(1)
threading.Thread(target=broker.serve, args=(server, acknowledge), daemon=True).start()
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
acknowledged = threading.Event()
client.on_connect = lambda *a, **k: client.publish("device/x/request", b"{}", qos=1)
client.on_publish = lambda *a, **k: acknowledged.set()
client.connect("127.0.0.1", server.getsockname()[1], keepalive=10)
client.loop_start()
assert acknowledged.wait(1.5) is acknowledge, "broker did not behave as asked"
_teardown_mqtt_client(client)
print("teardown returned")
"""


@pytest.mark.parametrize("mode", ["ack", "never-ack"])
def test_teardown_returns_against_a_real_paho_client(mode):
    """Run in a child process: the suite replaces paho with a mock for every in-process test."""
    broker = REPO / "tests" / "fakes" / "loopback_broker.py"
    try:
        done = subprocess.run(
            [sys.executable, "-c", _CHILD, mode, str(broker)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("teardown blocked on an unacknowledged QoS 1 publish")
    assert done.returncode == 0, done.stderr
    assert "teardown returned" in done.stdout
