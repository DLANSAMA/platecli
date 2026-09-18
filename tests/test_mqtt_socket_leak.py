"""Tests verifying socket file descriptors are closed on TLS pin failure."""

import ssl
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.protocols.mqtt_tls import pinning_ssl_context


def test_pinning_ssl_context_closes_socket_on_pin_mismatch():
    ctx = pinning_ssl_context("a" * 64)

    mock_sock = MagicMock()
    mock_tls_sock = MagicMock()
    mock_tls_sock.getpeercert.return_value = b"der_cert_bytes"

    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=mock_tls_sock),
        patch("bambu_cli.tlspin.verify_cert_fingerprint", side_effect=ssl.SSLError("Pin mismatch")),
        pytest.raises(ssl.SSLError),
    ):
        ctx.wrap_socket(mock_sock)

    # Socket must be closed on handshake/pinning failure to prevent FD leaks
    mock_tls_sock.close.assert_called_once()
