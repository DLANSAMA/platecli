"""TLS certificate fingerprint pinning for MQTT and FTPS (roadmap T1.1 / T1.5).

No real network: SSLContext and sockets are mocked; only pin verification logic runs.
"""

from __future__ import annotations

import hashlib
import ssl
import sys
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.protocols import ftps as ftps_mod  # noqa: E402
from bambu_cli.protocols import mqtt as mqtt_mod  # noqa: E402
from tests.bambu_test_base import _test_printer  # noqa: E402

pytestmark = pytest.mark.security

_DER = b"\x30\x82fake-der-bytes-for-pin-tests"
_FP = hashlib.sha256(_DER).hexdigest()
_FP_OTHER = "ab" * 32


def _mqtt_client_with_pin_context(cert_fingerprint: str):
    """Build an MQTT client under a fake SSLContext; return (client, ctx, base_wrap)."""
    tls_sock = MagicMock(name="tls_sock")
    tls_sock.getpeercert.return_value = _DER

    ctx_inst = MagicMock(name="ssl_context")
    base_wrap = MagicMock(return_value=tls_sock)
    ctx_inst.wrap_socket = base_wrap

    mock_client = MagicMock(name="mqtt_client")
    with (
        patch.object(mqtt_mod, "mqtt") as mock_mqtt_mod,
        patch("ssl.SSLContext", return_value=ctx_inst),
    ):
        mock_mqtt_mod.Client.return_value = mock_client
        mock_mqtt_mod.CallbackAPIVersion.VERSION2 = "v2"
        printer = _test_printer(cert_fingerprint=cert_fingerprint, insecure_tls=False)
        client = mqtt_mod.create_mqtt_client(printer)
    return client, ctx_inst, base_wrap, tls_sock, mock_client


def test_mqtt_create_client_with_pin_uses_context_and_insecure_flag():
    client, ctx_inst, _base, _tls, mock_client = _mqtt_client_with_pin_context(_FP)
    assert client is mock_client
    mock_client.tls_set_context.assert_called_once_with(ctx_inst)
    mock_client.tls_insecure_set.assert_called_once_with(True)
    mock_client.tls_set.assert_not_called()


def test_mqtt_pin_match_allows_wrap():
    _client, ctx_inst, base_wrap, tls_sock, _mc = _mqtt_client_with_pin_context(_FP)
    pinned_wrap = ctx_inst.wrap_socket
    assert pinned_wrap is not base_wrap
    assert pinned_wrap(object(), server_hostname="printer.local") is tls_sock
    base_wrap.assert_called()


def test_mqtt_pin_mismatch_raises_sslerror():
    _client, ctx_inst, base_wrap, tls_sock, _mc = _mqtt_client_with_pin_context(_FP)
    tls_sock.getpeercert.return_value = b"\x00wrong-cert"
    with pytest.raises(ssl.SSLError, match="fingerprint mismatch"):
        ctx_inst.wrap_socket(object())


def test_mqtt_pin_missing_peer_cert_raises():
    _client, ctx_inst, _base, tls_sock, _mc = _mqtt_client_with_pin_context(_FP)
    tls_sock.getpeercert.return_value = None
    with pytest.raises(ssl.SSLError, match="No peer certificate"):
        ctx_inst.wrap_socket(object())


def test_mqtt_pin_deferred_until_handshake():
    """paho often connects with handshake deferred; pin must run on do_handshake."""
    der = _DER
    fp = _FP
    tls_sock = MagicMock()
    # First getpeercert (handshake probe) raises; after do_handshake it returns der.
    state = {"ready": False}

    def getpeercert(binary_form=False):
        if not state["ready"]:
            raise ValueError("handshake not done")
        return der

    tls_sock.getpeercert.side_effect = getpeercert

    def do_handshake(*a, **k):
        state["ready"] = True
        return None

    tls_sock.do_handshake = do_handshake

    ctx_inst = MagicMock()
    base_wrap = MagicMock(return_value=tls_sock)
    ctx_inst.wrap_socket = base_wrap
    mock_client = MagicMock()

    with (
        patch.object(mqtt_mod, "mqtt") as mock_mqtt_mod,
        patch("ssl.SSLContext", return_value=ctx_inst),
    ):
        mock_mqtt_mod.Client.return_value = mock_client
        mock_mqtt_mod.CallbackAPIVersion.VERSION2 = "v2"
        mqtt_mod.create_mqtt_client(_test_printer(cert_fingerprint=fp))

    out = ctx_inst.wrap_socket(object())
    assert out is tls_sock
    # Handshake wrapper installed; invoking it must pin successfully.
    out.do_handshake()
    assert state["ready"] is True


def test_mqtt_pin_deferred_mismatch_on_handshake():
    tls_sock = MagicMock()
    state = {"ready": False}

    def getpeercert(binary_form=False):
        if not state["ready"]:
            raise ValueError("handshake not done")
        return b"\xffnot-the-pinned-cert"

    tls_sock.getpeercert.side_effect = getpeercert

    def do_handshake(*a, **k):
        state["ready"] = True

    tls_sock.do_handshake = do_handshake

    ctx_inst = MagicMock()
    ctx_inst.wrap_socket = MagicMock(return_value=tls_sock)
    mock_client = MagicMock()

    with (
        patch.object(mqtt_mod, "mqtt") as mock_mqtt_mod,
        patch("ssl.SSLContext", return_value=ctx_inst),
    ):
        mock_mqtt_mod.Client.return_value = mock_client
        mock_mqtt_mod.CallbackAPIVersion.VERSION2 = "v2"
        mqtt_mod.create_mqtt_client(_test_printer(cert_fingerprint=_FP))

    sock = ctx_inst.wrap_socket(object())
    with pytest.raises(ssl.SSLError, match="fingerprint mismatch"):
        sock.do_handshake()


def test_ftps_pin_match_on_connect():
    mock_raw = MagicMock()
    mock_raw.family = 2
    mock_tls = MagicMock()
    mock_tls.getpeercert.return_value = _DER
    mock_file = MagicMock()

    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.return_value = mock_tls
    mock_tls.makefile.return_value = mock_file

    ftp = ftps_mod.ImplicitFTPS()
    ftp.printer = _test_printer(cert_fingerprint=_FP, insecure_tls=False)
    ftp.getresp = MagicMock(return_value="220 Welcome")

    with (
        patch.object(ftps_mod.socket, "create_connection", return_value=mock_raw),
        patch("ssl.SSLContext", return_value=mock_ctx),
    ):
        welcome = ftp.connect("192.168.1.1", 990, 5)

    assert welcome == "220 Welcome"
    assert mock_ctx.check_hostname is False
    assert mock_ctx.verify_mode == ssl.CERT_NONE
    mock_ctx.wrap_socket.assert_called_once()


def test_ftps_pin_mismatch_on_connect():
    mock_raw = MagicMock()
    mock_raw.family = 2
    mock_tls = MagicMock()
    mock_tls.getpeercert.return_value = b"\x00other"
    mock_tls.makefile.return_value = MagicMock()

    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.return_value = mock_tls

    ftp = ftps_mod.ImplicitFTPS()
    ftp.printer = _test_printer(cert_fingerprint=_FP, insecure_tls=False)
    ftp.getresp = MagicMock(return_value="220 Welcome")

    with (
        patch.object(ftps_mod.socket, "create_connection", return_value=mock_raw),
        patch("ssl.SSLContext", return_value=mock_ctx),
        pytest.raises(ssl.SSLError, match="fingerprint mismatch"),
    ):
        ftp.connect("192.168.1.1", 990, 5)


def test_ftps_data_channel_pin_mismatch():
    """Data-channel wrap must re-check the pin (not only the control channel)."""
    ftp = ftps_mod.ImplicitFTPS()
    ftp.printer = _test_printer(cert_fingerprint=_FP, insecure_tls=False)
    ftp.host = "192.168.1.1"
    ftp._prot_p = True

    control_tls = MagicMock(spec=ssl.SSLSocket)
    control_tls.session = object()
    control_ctx = MagicMock()
    control_tls.context = control_ctx
    ftp.sock = control_tls

    data_raw = MagicMock()
    data_tls = MagicMock()
    data_tls.getpeercert.return_value = b"\xde\xad"
    control_ctx.wrap_socket.return_value = data_tls

    with (
        patch.object(ftps_mod.ftplib.FTP, "ntransfercmd", return_value=(data_raw, 100)),
        pytest.raises(ssl.SSLError, match="fingerprint mismatch"),
    ):
        ftp.ntransfercmd("STOR /model/x.3mf")
