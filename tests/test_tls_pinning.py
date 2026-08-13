"""TLS certificate fingerprint pinning for MQTT and FTPS (roadmap T1.1 / T1.5).

No real network: SSLContext and sockets are mocked; only pin verification logic runs.
"""

from __future__ import annotations

import hashlib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.protocols import ftps as ftps_mod  # noqa: E402
from bambu_cli.protocols import mqtt_tls as mqtt_tls  # noqa: E402
from tests.bambu_test_base import _test_printer  # noqa: E402

pytestmark = pytest.mark.security

_DER = b"\x30\x82fake-der-bytes-for-pin-tests"
_FP = hashlib.sha256(_DER).hexdigest()
_FP_OTHER = "ab" * 32

def _tls_sock(der=_DER):
    tls = MagicMock(name="tls_sock")
    state = {"ready": False}

    def do_handshake(*a, **k):
        state["ready"] = True

    def getpeercert(binary_form=False):
        if not state["ready"]:
            raise ValueError("handshake not done")
        return der

    tls.do_handshake.side_effect = do_handshake
    tls.getpeercert.side_effect = getpeercert
    tls._pin_state = state
    return tls

def test_mqtt_create_client_with_pin_uses_pinning_context():
    mock_client = MagicMock(name="mqtt_client")
    with patch.object(mqtt_tls, "mqtt") as mock_mqtt_mod:
        mock_mqtt_mod.Client.return_value = mock_client
        mock_mqtt_mod.CallbackAPIVersion.VERSION2 = "v2"
        client = mqtt_tls.create_mqtt_client(_test_printer(cert_fingerprint=_FP, insecure_tls=False))
    assert client is mock_client
    mock_client.tls_set_context.assert_called_once()
    ctx = mock_client.tls_set_context.call_args[0][0]
    assert isinstance(ctx, mqtt_tls.PinningSSLContext)
    assert ctx.expected_fingerprint == _FP
    mock_client.tls_insecure_set.assert_called_once_with(True)
    mock_client.tls_set.assert_not_called()

def test_pinning_context_match_handshakes_then_verifies():
    tls = _tls_sock(_DER)
    ctx = mqtt_tls.pinning_ssl_context(_FP)
    with patch.object(ssl.SSLContext, "wrap_socket", return_value=tls) as super_wrap:
        assert ctx.wrap_socket(object(), server_hostname="printer.local") is tls
    super_wrap.assert_called()
    tls.do_handshake.assert_called_once()
    assert tls._pin_state["ready"] is True

def test_pinning_context_mismatch_raises_sslerror():
    tls = _tls_sock(b"\x00wrong-cert")
    ctx = mqtt_tls.pinning_ssl_context(_FP)
    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=tls),
        pytest.raises(ssl.SSLError, match="fingerprint mismatch"),
    ):
        ctx.wrap_socket(object())
    tls.do_handshake.assert_called_once()

def test_pinning_context_missing_peer_cert_raises():
    tls = _tls_sock(None)
    ctx = mqtt_tls.pinning_ssl_context(_FP)
    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=tls),
        pytest.raises(ssl.SSLError, match="No peer certificate"),
    ):
        ctx.wrap_socket(object())

def test_pinning_context_malformed_pin_raises():
    tls = _tls_sock(_DER)
    ctx = mqtt_tls.pinning_ssl_context("а" + "b" * 63)
    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=tls),
        pytest.raises(ssl.SSLError, match="[Mm]alformed"),
    ):
        ctx.wrap_socket(object())

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

def test_ftps_data_channel_pin_mismatch_closes_socket():
    """Fingerprint mismatch must close the data socket before re-raising (no FD leak)."""
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
    data_tls.close = MagicMock()
    control_ctx.wrap_socket.return_value = data_tls

    with (
        patch.object(ftps_mod.ftplib.FTP, "ntransfercmd", return_value=(data_raw, 100)),
        pytest.raises(ssl.SSLError, match="fingerprint mismatch"),
    ):
        ftp.ntransfercmd("STOR /model/x.3mf")

    data_tls.close.assert_called_once()

def test_ftps_data_channel_malformed_pin_closes_socket():
    """A malformed (non-ASCII) pin must also fail closed as an ssl.SSLError and
    close the data socket — not escape as a raw TypeError that skips the
    close-before-raise and leaks the FD."""
    ftp = ftps_mod.ImplicitFTPS()
    # 64 chars incl. a Cyrillic 'а' — survives normalize, non-ASCII.
    ftp.printer = _test_printer(cert_fingerprint="а" + "b" * 63, insecure_tls=False)
    ftp.host = "192.168.1.1"
    ftp._prot_p = True

    control_tls = MagicMock(spec=ssl.SSLSocket)
    control_tls.session = object()
    control_ctx = MagicMock()
    control_tls.context = control_ctx
    ftp.sock = control_tls

    data_raw = MagicMock()
    data_tls = MagicMock()
    data_tls.getpeercert.return_value = _DER
    data_tls.close = MagicMock()
    control_ctx.wrap_socket.return_value = data_tls

    with (
        patch.object(ftps_mod.ftplib.FTP, "ntransfercmd", return_value=(data_raw, 100)),
        pytest.raises(ssl.SSLError, match="[Mm]alformed"),
    ):
        ftp.ntransfercmd("STOR /model/x.3mf")

    data_tls.close.assert_called_once()
