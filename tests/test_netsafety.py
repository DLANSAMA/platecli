"""Unit tests for the SSRF-safe connection layer (bambu_cli.netsafety).

Exercises `_get_safe_connection` (per-hop IP validation, DNS cache, TOCTOU-safe
connect-to-resolved-IP) and `build_safe_opener` handler composition. All DNS
and socket calls are mocked; the network is never touched.

Ground rules (docs/test-backlog.md): patch runtime state via the RuntimeContext
(settings_ctx), never touch the network.
"""

import socket
import sys
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)

from bambu_cli import netsafety  # noqa: E402
from bambu_cli.netsafety import (  # noqa: E402
    MAX_DOWNLOAD_REDIRECT_HOPS,
    SafeHTTPHandler,
    SafeHTTPRedirectHandler,
    SafeHTTPSHandler,
    _get_safe_connection,
    build_safe_opener,
)
from tests.bambu_test_base import settings_ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_dns_cache():
    netsafety._dns_cache.clear()
    yield
    netsafety._dns_cache.clear()


def _addrinfo(ip, port=443):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


# ---------------------------------------------------------------------------
# Public IPs connect; the connection targets the *resolved IP*, not the host
# (TOCTOU / DNS-rebinding defense).
# ---------------------------------------------------------------------------
def test_public_ip_connects_to_resolved_ip_not_hostname():
    sentinel = object()
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("8.8.8.8")),
        patch.object(netsafety.socket, "create_connection", return_value=sentinel) as conn,
    ):
        result = _get_safe_connection("host.example.com", 443, 5, None)
    assert result is sentinel
    conn.assert_called_once_with(("8.8.8.8", 443), 5, None)


# ---------------------------------------------------------------------------
# Private / non-global IPs are refused unless explicitly allowed.
# ---------------------------------------------------------------------------
def test_private_ip_refused_and_never_connects():
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("192.168.0.10")),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError, match="No safe/reachable"),
    ):
        _get_safe_connection("internal.example.com", 443, 5, None)
    conn.assert_not_called()


def test_allow_private_ips_permits_private_connection():
    sentinel = object()
    with (
        settings_ctx(allow_private_ips=True),
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("10.0.0.5")),
        patch.object(netsafety.socket, "create_connection", return_value=sentinel) as conn,
    ):
        result = _get_safe_connection("internal", 443, 5, None)
    assert result is sentinel
    conn.assert_called_once_with(("10.0.0.5", 443), 5, None)


# ---------------------------------------------------------------------------
# CLI wiring: --allow-private-ips must reach RuntimeContext via main()
# (settings_ctx alone is not enough — the flag was previously dead).
# ---------------------------------------------------------------------------
def test_main_allow_private_ips_flag_enables_settings(monkeypatch, tmp_path):
    import bambu_cli.bambu as bambu
    from bambu_cli.cli import main
    from bambu_cli.context import current_settings

    seen = {}

    def capture(_args):
        seen["allow"] = current_settings().allow_private_ips

    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--sim", "--allow-private-ips", "status", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(bambu, "cmd_status", capture)
    main()
    assert seen.get("allow") is True


def test_main_default_denies_private_ips(monkeypatch, tmp_path):
    import bambu_cli.bambu as bambu
    from bambu_cli.cli import main
    from bambu_cli.context import current_settings

    seen = {}

    def capture(_args):
        seen["allow"] = current_settings().allow_private_ips

    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--sim", "status", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(bambu, "cmd_status", capture)
    main()
    assert seen.get("allow") is False


def test_main_allow_private_ips_reaches_get_safe_connection(monkeypatch, tmp_path):
    """End-to-end: flag → Settings → netsafety permits a private resolved IP."""
    import bambu_cli.bambu as bambu
    from bambu_cli.cli import main

    sentinel = object()
    outcomes = {}

    def capture(_args):
        with (
            patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("192.168.1.50")),
            patch.object(netsafety.socket, "create_connection", return_value=sentinel) as conn,
        ):
            outcomes["result"] = _get_safe_connection("lan.example", 443, 5, None)
            outcomes["connected"] = conn.called

    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--sim", "--allow-private-ips", "status", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(bambu, "cmd_status", capture)
    main()
    assert outcomes.get("result") is sentinel
    assert outcomes.get("connected") is True


def test_ipv4_mapped_ipv6_private_address_refused():
    # ::ffff:192.168.0.1 must be unwrapped and evaluated as the private v4 addr.
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("::ffff:192.168.0.1")),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError, match="No safe/reachable"),
    ):
        _get_safe_connection("rebind.example.com", 443, 5, None)
    conn.assert_not_called()


# ---------------------------------------------------------------------------
# Resolution / candidate-iteration edge cases
# ---------------------------------------------------------------------------
def test_dns_failure_becomes_urlerror():
    with (
        patch.object(netsafety.socket, "getaddrinfo", side_effect=socket.gaierror("nope")),
        pytest.raises(urllib.error.URLError, match="DNS resolution failed"),
    ):
        _get_safe_connection("nx.example.com", 443, 5, None)


def test_unparseable_ip_skipped_then_valid_ip_used():
    sentinel = object()
    addrs = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.4.4", 443)),
    ]
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=addrs),
        patch.object(netsafety.socket, "create_connection", return_value=sentinel) as conn,
    ):
        result = _get_safe_connection("mixed.example.com", 443, 5, None)
    assert result is sentinel
    conn.assert_called_once_with(("8.8.4.4", 443), 5, None)


def test_all_ips_fail_connection_invalidates_cache():
    # A valid public IP that refuses TCP must raise and drop the cache entry so
    # the next attempt re-resolves rather than serving a dead cached address.
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("8.8.8.8")) as ga,
        patch.object(netsafety.socket, "create_connection", side_effect=OSError("refused")),
    ):
        with pytest.raises(urllib.error.URLError, match="No safe/reachable"):
            _get_safe_connection("dead.example.com", 443, 5, None)
        assert ("dead.example.com", 443) not in netsafety._dns_cache
        # Second call must resolve again (cache was invalidated).
        with pytest.raises(urllib.error.URLError):
            _get_safe_connection("dead.example.com", 443, 5, None)
    assert ga.call_count == 2


# ---------------------------------------------------------------------------
# DNS cache behavior
# ---------------------------------------------------------------------------
def test_dns_cache_hit_skips_second_resolution():
    sentinel = object()
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("8.8.8.8")) as ga,
        patch.object(netsafety.socket, "create_connection", return_value=sentinel),
    ):
        _get_safe_connection("cached.example.com", 443, 5, None)
        _get_safe_connection("cached.example.com", 443, 5, None)
    assert ga.call_count == 1


def test_dns_cache_expiry_triggers_reresolution():
    from bambu_cli.constants import DNS_CACHE_TTL

    sentinel = object()
    # _get_safe_connection reads time.time() once per call: first call stores the
    # entry at t=1000; second call is past the TTL, so the cache entry expires.
    times = iter([1000.0, 1000.0 + DNS_CACHE_TTL + 1])
    with (
        patch.object(netsafety.time, "time", side_effect=lambda: next(times)),
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("8.8.8.8")) as ga,
        patch.object(netsafety.socket, "create_connection", return_value=sentinel),
    ):
        _get_safe_connection("ttl.example.com", 443, 5, None)
        _get_safe_connection("ttl.example.com", 443, 5, None)
    assert ga.call_count == 2


def test_dns_cache_evicted_when_oversized():
    # >1000 entries triggers a full clear before inserting the new one.
    for i in range(1001):
        netsafety._dns_cache[(f"h{i}", 443)] = (_addrinfo("8.8.8.8"), 0.0)
    sentinel = object()
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("8.8.8.8")),
        patch.object(netsafety.socket, "create_connection", return_value=sentinel),
    ):
        _get_safe_connection("fresh.example.com", 443, 5, None)
    # Cache was cleared, leaving only the freshly resolved host.
    assert list(netsafety._dns_cache) == [("fresh.example.com", 443)]


# ---------------------------------------------------------------------------
# build_safe_opener composition
# ---------------------------------------------------------------------------
def test_build_safe_opener_disables_proxies():
    # An explicit empty ProxyHandler registers no proxy routes, so urllib never
    # consults environment proxies (which could reach an internal address on our
    # behalf and bypass IP validation). Assert no handler carries an active proxy.
    opener = build_safe_opener()
    assert not any(getattr(h, "proxies", None) for h in opener.handlers)


def test_build_safe_opener_registers_safe_handlers():
    opener = build_safe_opener()
    types_present = {type(h) for h in opener.handlers}
    assert SafeHTTPHandler in types_present
    assert SafeHTTPSHandler in types_present
    assert SafeHTTPRedirectHandler in types_present


# ---------------------------------------------------------------------------
# Redirect hop cap
# ---------------------------------------------------------------------------
def test_redirect_hop_cap_rejects_over_limit():
    handler = SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    req._bambu_redirect_hops = MAX_DOWNLOAD_REDIRECT_HOPS
    with pytest.raises(urllib.error.URLError, match="Too many redirects"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/next")


def test_safe_https_connect_wraps_socket():
    conn = netsafety.SafeHTTPSConnection("example.com", 443)
    conn.timeout = 5
    conn.source_address = None
    sock = object()
    wrapped = MagicMock()
    ctx = MagicMock()
    ctx.wrap_socket.return_value = wrapped
    conn._context = ctx
    with patch.object(netsafety, "_get_safe_connection", return_value=sock):
        conn.connect()
    assert conn.sock is wrapped
    ctx.wrap_socket.assert_called_once()


def test_safe_http_connect():
    conn = netsafety.SafeHTTPConnection("example.com", 80)
    conn.timeout = 5
    conn.source_address = None
    sock = object()
    with patch.object(netsafety, "_get_safe_connection", return_value=sock):
        conn.connect()
    assert conn.sock is sock


def test_safe_https_connect_closes_on_wrap_failure():
    conn = netsafety.SafeHTTPSConnection("example.com", 443)
    conn.timeout = 5
    conn.source_address = None
    sock = MagicMock()
    ctx = MagicMock()
    ctx.wrap_socket.side_effect = OSError("ssl fail")
    conn._context = ctx
    with patch.object(netsafety, "_get_safe_connection", return_value=sock), pytest.raises(OSError):
        conn.connect()
    sock.close.assert_called()
