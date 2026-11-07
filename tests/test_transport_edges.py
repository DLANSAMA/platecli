"""Edge paths in the FTPS transport and the SSRF layer that the main suites skip.

Roadmap section 3.1 sets per-module targets for ``protocols/ftps.py`` and
``netsafety.py``. These tests cover what was left: default-argument handling in
``ImplicitFTPS.connect``, cleanup when a socket or file will not close, the
non-TLS data-channel branch, the redirect handler's header rules, the HTTPS
handler's optional context, and the per-host throttle table's size cap. Nothing
here opens a socket.
"""

from __future__ import annotations

import ftplib
import ipaddress
import socket
import ssl
import types
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from bambu_cli import netsafety
from bambu_cli.protocols import ftps

_DER = b"\x30\x82edge-case-der"


# ---------------------------------------------------------------------------
# protocols/ftps.py
# ---------------------------------------------------------------------------
def test_sim_ftp_voidcmd_acknowledges():
    assert ftps._SimFtp().voidcmd("NOOP") == "200 OK"


class _FakeTlsSock:
    """A wrapped socket that records closes and can refuse to close."""

    def __init__(self, *, close_error=None, file=None):
        self.family = socket.AF_INET
        self.closed = False
        self._close_error = close_error
        self._file = file

    def getpeercert(self, binary_form=False):
        return _DER

    def makefile(self, mode, encoding=None):
        return self._file

    def close(self):
        self.closed = True
        if self._close_error:
            raise self._close_error


class _FakeContext:
    def __init__(self, tls):
        self._tls = tls
        self.check_hostname = None
        self.verify_mode = None

    def load_default_certs(self):
        pass

    def wrap_socket(self, sock, server_hostname=None):
        return self._tls


def _plain_printer():
    return types.SimpleNamespace(cert_fingerprint=None, insecure_tls=True)


def test_connect_keeps_preset_host_port_and_timeout_when_given_defaults():
    """host='', port<=0 and the -999 timeout sentinel mean 'keep what is set'."""
    seen = {}

    def create_connection(address, timeout):
        seen["address"] = address
        seen["timeout"] = timeout
        return _FakeTlsSock()

    tls = _FakeTlsSock(file=Mock())
    ftp = ftps.ImplicitFTPS()
    ftp.printer = _plain_printer()
    ftp.host = "printer.local"
    ftp.port = 990
    ftp.getresp = lambda: "220 hi"  # type: ignore[method-assign]

    welcome = ftp.connect("", 0, create_connection=create_connection, ssl_context_cls=lambda proto: _FakeContext(tls))

    assert welcome == "220 hi"
    assert seen["address"] == ("printer.local", 990)
    assert seen["timeout"] == ftp.timeout


def test_connect_failure_survives_files_and_sockets_that_will_not_close():
    """The handshake error is what the caller sees, not a secondary close error."""
    bad_file = Mock()
    bad_file.close.side_effect = OSError("file close failed")
    tls = _FakeTlsSock(close_error=OSError("socket close failed"), file=bad_file)

    ftp = ftps.ImplicitFTPS()
    ftp.printer = _plain_printer()

    def refuse_greeting():
        raise ftplib.error_temp("421 try later")

    ftp.getresp = refuse_greeting  # type: ignore[method-assign]

    with pytest.raises(ftplib.error_temp, match="421"):
        ftp.connect(
            "printer.local",
            990,
            5,
            create_connection=lambda address, timeout: _FakeTlsSock(),
            ssl_context_cls=lambda proto: _FakeContext(tls),
        )

    bad_file.close.assert_called_once()
    assert tls.closed is True


def _data_channel_client(printer):
    client = ftps.ImplicitFTPS.__new__(ftps.ImplicitFTPS)
    client._prot_p = True
    client.host = "printer.local"
    client.printer = printer
    client.sock = Mock(spec=ssl.SSLSocket)
    client.sock.session = None
    return client


def test_ntransfercmd_returns_the_raw_channel_when_the_session_is_not_protected(monkeypatch):
    client = _data_channel_client(_plain_printer())
    client._prot_p = False
    raw = Mock()
    monkeypatch.setattr(ftplib.FTP, "ntransfercmd", lambda self, cmd, rest=None: (raw, 7))

    conn, size = client.ntransfercmd("RETR x")

    assert conn is raw and size == 7
    client.sock.context.wrap_socket.assert_not_called()


def test_ntransfercmd_wrap_failure_closes_the_data_socket_and_reraises(monkeypatch):
    client = _data_channel_client(_plain_printer())
    raw = Mock()
    raw.close.side_effect = OSError("already detached")
    client.sock.context.wrap_socket.side_effect = ssl.SSLError("handshake failed")
    monkeypatch.setattr(ftplib.FTP, "ntransfercmd", lambda self, cmd, rest=None: (raw, 7))

    with pytest.raises(ssl.SSLError, match="handshake failed"):
        client.ntransfercmd("STOR x")

    raw.close.assert_called_once()


def test_ntransfercmd_pin_mismatch_reraises_even_if_the_socket_will_not_close(monkeypatch):
    client = _data_channel_client(types.SimpleNamespace(cert_fingerprint="ab" * 32, insecure_tls=False))
    wrapped = Mock()
    wrapped.getpeercert.return_value = _DER
    wrapped.close.side_effect = OSError("close failed")
    client.sock.context.wrap_socket.return_value = wrapped
    monkeypatch.setattr(ftplib.FTP, "ntransfercmd", lambda self, cmd, rest=None: (Mock(), 7))

    with pytest.raises(ssl.SSLError, match="fingerprint mismatch"):
        client.ntransfercmd("STOR x")

    wrapped.close.assert_called_once()


# ---------------------------------------------------------------------------
# netsafety.py
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_netsafety_state():
    netsafety._dns_cache.clear()
    netsafety._last_request_at.clear()
    yield
    netsafety._dns_cache.clear()
    netsafety._last_request_at.clear()


def test_allow_private_accepts_ipv6_loopback_and_unique_local():
    for text in ("::1", "fd00::1"):
        assert netsafety._is_safe_ip(ipaddress.ip_address(text), allow_private=True) is True
        assert netsafety._is_safe_ip(ipaddress.ip_address(text), allow_private=False) is False


def test_a_refused_address_is_reported_once_even_if_dns_returns_it_twice(monkeypatch):
    entry = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    monkeypatch.setattr(netsafety.socket, "getaddrinfo", lambda *a, **k: [entry, entry])

    with pytest.raises(netsafety.BlockedAddressError) as excinfo:
        netsafety._get_safe_connection("loop.example", 443, 5, None)

    assert str(excinfo.value).count("127.0.0.1") == 1


class _ClosingRaises:
    def close(self):
        raise OSError("close failed")


def test_redirect_limit_error_is_not_masked_by_a_response_that_will_not_close():
    handler = netsafety.SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    req._bambu_redirect_hops = netsafety.MAX_DOWNLOAD_REDIRECT_HOPS

    with pytest.raises(urllib.error.URLError, match="Too many redirects"):
        handler.redirect_request(req, _ClosingRaises(), 302, "Found", {}, "https://example.com/next")


def test_cross_host_redirect_drops_credentials_and_adds_no_user_agent_that_was_never_set():
    handler = netsafety.SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://a.example/start", headers={"Authorization": "Bearer secret"})

    new_req = handler.redirect_request(req, None, 302, "Found", {}, "https://b.example/next")

    assert new_req is not None
    assert not new_req.has_header("Authorization")
    assert not new_req.has_header("User-agent")


class _RecordingHttpsHandler(netsafety.SafeHTTPSHandler):
    def do_open(self, http_class, req, **kwargs):
        self.seen = kwargs
        return None


def test_https_handler_passes_context_and_hostname_check_only_when_it_has_them():
    handler = _RecordingHttpsHandler()
    for attr in ("_context", "_check_hostname"):
        handler.__dict__.pop(attr, None)
    req = urllib.request.Request("https://example.com/")

    handler.https_open(req)
    assert handler.seen == {}

    ctx = ssl.create_default_context()
    handler._context = ctx
    handler.https_open(req)
    assert handler.seen == {"context": ctx}

    handler._check_hostname = True
    handler.https_open(req)
    assert handler.seen == {"context": ctx, "check_hostname": True}


def _fill_throttle_table(age_seconds):
    import time

    stamp = time.monotonic() - age_seconds
    netsafety._last_request_at.update({f"host{i}.example": stamp for i in range(1000)})


def test_throttle_table_drops_stale_hosts_when_it_reaches_the_cap():
    _fill_throttle_table(age_seconds=120)

    netsafety._throttle_host("fresh.example", sleep=lambda s: None)

    assert list(netsafety._last_request_at) == ["fresh.example"]


def test_throttle_table_is_cleared_when_every_host_is_recent():
    _fill_throttle_table(age_seconds=1)

    netsafety._throttle_host("fresh.example", sleep=lambda s: None)

    assert list(netsafety._last_request_at) == ["fresh.example"]
