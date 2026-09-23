"""Tests for socket teardown when a pinned TLS wrap fails.

A note on why the central assertion here is a mock, because it looks like a
weaker choice than it is. The obvious "stronger" test -- connect repeatedly with
a failing pin and count /proc/self/fd -- cannot fail. ``wrap_socket`` detaches
the caller's socket and the returned ``SSLSocket`` becomes the sole owner of the
fd; when the exception propagates, that object's last reference dies and CPython
refcounting closes the fd immediately, leak or no leak. Such a test was written,
run against a deliberately reintroduced leak, and passed anyway. It was removed
rather than kept as reassuring decoration.

So the explicit ``close.assert_called_once()`` below is the assertion that
actually catches the regression (verified: it fails when the ``except`` branch's
close is deleted). The real-server test alongside it covers the genuine
``do_handshake()`` failure path end to end -- real socket, real SSLSocket, real
``ssl.SSLError`` -- which no mocked test reaches.
"""

import contextlib
import socket
import ssl
import threading
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.protocols.mqtt_tls import pinning_ssl_context

PIN = "a" * 64


@pytest.fixture
def garbage_tls_server():
    """Accepts TCP, then answers with bytes that are not a TLS ServerHello."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    stop = threading.Event()

    def _serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn, contextlib.suppress(OSError):
                conn.recv(4096)  # the ClientHello
                conn.sendall(b"definitely not a TLS record\n")

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=5)


def test_pin_mismatch_closes_the_tls_socket_and_reraises():
    """The regression guard: the failure path must close explicitly, not lean on GC."""
    ctx = pinning_ssl_context(PIN)
    mock_tls_sock = MagicMock()
    mock_tls_sock.getpeercert.return_value = b"der_cert_bytes"

    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=mock_tls_sock),
        patch("bambu_cli.tlspin.verify_cert_fingerprint", side_effect=ssl.SSLError("Pin mismatch")),
        pytest.raises(ssl.SSLError),
    ):
        ctx.wrap_socket(MagicMock())

    mock_tls_sock.close.assert_called_once()


def test_handshake_failure_closes_the_tls_socket_and_reraises():
    """Same guard for the other branch: do_handshake() raising, not the pin check."""
    ctx = pinning_ssl_context(PIN)
    mock_tls_sock = MagicMock()
    mock_tls_sock.do_handshake.side_effect = ssl.SSLError("handshake failed")

    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=mock_tls_sock),
        pytest.raises(ssl.SSLError),
    ):
        ctx.wrap_socket(MagicMock())

    mock_tls_sock.close.assert_called_once()


def test_a_close_that_itself_raises_does_not_mask_the_original_error():
    ctx = pinning_ssl_context(PIN)
    mock_tls_sock = MagicMock()
    mock_tls_sock.do_handshake.side_effect = ssl.SSLError("original failure")
    mock_tls_sock.close.side_effect = OSError("close blew up too")

    with (
        patch.object(ssl.SSLContext, "wrap_socket", return_value=mock_tls_sock),
        pytest.raises(ssl.SSLError) as excinfo,
    ):
        ctx.wrap_socket(MagicMock())

    assert "original failure" in str(excinfo.value)


def test_real_failed_handshake_surfaces_as_sslerror(garbage_tls_server):
    """End-to-end over a real socket: no mocks between here and OpenSSL."""
    ctx = pinning_ssl_context(PIN)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sock = socket.create_connection(garbage_tls_server, timeout=10)
    try:
        with pytest.raises(ssl.SSLError):
            ctx.wrap_socket(sock, server_hostname="localhost")
    finally:
        with contextlib.suppress(OSError):
            sock.close()
