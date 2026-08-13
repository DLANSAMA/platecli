"""MQTT TLS client construction and connect.

Pinning uses :class:`PinningSSLContext` — a real ``ssl.SSLContext`` subclass
whose ``wrap_socket`` completes the handshake and calls
``verify_cert_fingerprint``. paho 2.x wraps with
``do_handshake_on_connect=False`` and then calls ``do_handshake()`` itself;
we handshake and pin inside ``wrap_socket`` so a later ``do_handshake`` is a
no-op and the pin is a straight ``tlspin`` call.
"""

from __future__ import annotations

import socket
import ssl

from bambu_cli.logging_utils import logger
from bambu_cli.utils import _resolve_ip

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


def _require_mqtt():
    """Ensure paho-mqtt is importable; abort with config exit if missing."""
    global mqtt
    if mqtt is not None:
        return
    try:
        import paho.mqtt.client as paho_mqtt

        mqtt = paho_mqtt
    except ImportError:
        logger.error(
            "Missing dependency: paho-mqtt. Reinstall the package "
            "(e.g. `uv pip install -e .` from a source checkout, or `pip install platecli`)."
        )
        from bambu_cli.constants import EXIT_CONFIG_ERROR
        from bambu_cli.errors import abort

        abort("", exit_code=EXIT_CONFIG_ERROR)


class PinningSSLContext(ssl.SSLContext):
    """TLS client context that pins the peer cert after handshake."""

    expected_fingerprint: str

    def wrap_socket(self, sock, *args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["do_handshake_on_connect"] = False
        tls_sock = super().wrap_socket(sock, *args, **kwargs)
        tls_sock.do_handshake()
        from bambu_cli.tlspin import verify_cert_fingerprint

        verify_cert_fingerprint(tls_sock.getpeercert(binary_form=True), self.expected_fingerprint)
        return tls_sock


def pinning_ssl_context(expected_fingerprint: str) -> PinningSSLContext:
    ctx = PinningSSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.expected_fingerprint = expected_fingerprint
    return ctx


class _SimMqttClient:
    """Small MQTT stand-in for --sim without importing test-only mocks."""

    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.on_publish = None

    def username_pw_set(self, username, password):
        pass

    def tls_set(self, *args, **kwargs):
        pass

    def tls_insecure_set(self, *args, **kwargs):
        pass

    def connect(self, host, port, keepalive=10):
        if self.on_connect:
            self.on_connect(self, None, None, 0)

    def subscribe(self, topic):
        pass

    def publish(self, topic, payload):
        if self.on_publish:
            self.on_publish(self, None, 1)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def socket(self):
        return None


def probe_cert_fingerprint(host, port=990, timeout=5):
    """Open a TLS connection purely to read the server cert's SHA-256 fingerprint."""
    from bambu_cli.config import fingerprint_sha256

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
        return fingerprint_sha256(tls.getpeercert(binary_form=True))


def create_mqtt_client(printer, client_id=""):
    if printer.simulation_mode:
        return _SimMqttClient()

    _require_mqtt()
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
    except AttributeError:
        client = mqtt.Client(client_id)
    client.username_pw_set("bblp", printer.access_code)

    if printer.insecure_tls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    elif printer.cert_fingerprint:
        client.tls_set_context(pinning_ssl_context(printer.cert_fingerprint))
        client.tls_insecure_set(True)
    else:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def _mqtt_connect(printer, client):
    resolved_ip = _resolve_ip(printer.ip)
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(printer.mqtt_timeout)
        if hasattr(client, "_connect_timeout"):
            client._connect_timeout = printer.mqtt_timeout
        client.connect(resolved_ip, 8883, keepalive=10)
    finally:
        socket.setdefaulttimeout(old_timeout)
