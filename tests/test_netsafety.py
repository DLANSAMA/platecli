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

    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "--allow-private-ips", "status", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.commands.cmd_status", capture)
    main()
    assert seen.get("allow") is True

def test_main_default_denies_private_ips(monkeypatch, tmp_path):
    import bambu_cli.bambu as bambu
    from bambu_cli.cli import main
    from bambu_cli.context import current_settings

    seen = {}

    def capture(_args):
        seen["allow"] = current_settings().allow_private_ips

    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "status", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.commands.cmd_status", capture)
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

    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "--allow-private-ips", "status", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no-config" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.commands.cmd_status", capture)
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


@pytest.mark.parametrize("ip", ["224.0.0.1", "239.255.255.250", "ff02::1", "ff05::2"])
def test_multicast_ip_refused_even_with_allow_private(ip):
    with (
        settings_ctx(allow_private_ips=True),
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo(ip)),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError, match="No safe/reachable"),
    ):
        _get_safe_connection("multicast.example.com", 80, 5, None)
    conn.assert_not_called()


@pytest.mark.parametrize("ip", ["::127.0.0.1", "::169.254.169.254", "::10.0.0.1", "::192.168.1.1"])
def test_ipv4_compatible_ipv6_refused_when_private(ip):
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo(ip)),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError, match="No safe/reachable"),
    ):
        _get_safe_connection("compat.example.com", 80, 5, None)
    conn.assert_not_called()


def test_6to4_embedding_private_ipv4_refused():
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo("2002:7f00:1::")),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError, match="No safe/reachable"),
    ):
        _get_safe_connection("sixtofour.example.com", 80, 5, None)
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

# --- polite client (per-host throttle + Retry-After) -------------------------

def _http_error(code, retry_after=None):
    import email.message

    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.printables.com/graphql/", code, "rate limited", hdrs, None)

def _fake_req(url="https://api.printables.com/graphql/"):
    req = MagicMock()
    req.full_url = url
    return req

def test_polite_open_retries_on_429_and_honors_retry_after(monkeypatch):
    monkeypatch.setattr(netsafety, "MIN_HOST_REQUEST_INTERVAL", 1.0)
    sentinel = object()
    slept = []

    opener = MagicMock()
    opener.open.side_effect = [_http_error(429, "2"), sentinel]
    assert netsafety.polite_open(opener, _fake_req(), timeout=5, sleep=slept.append) is sentinel
    assert 2.0 in slept

    # Absurd Retry-After clamps to MAX_RETRY_AFTER_WAIT.
    netsafety._last_request_at.clear()
    slept.clear()
    opener = MagicMock()
    opener.open.side_effect = [_http_error(429, "999"), sentinel]
    assert netsafety.polite_open(opener, _fake_req(), timeout=5, sleep=slept.append) is sentinel
    assert 30.0 in slept

    # An HTTP-date Retry-After falls back to the polite interval, no raise.
    assert netsafety._retry_after_seconds(_http_error(429, "Wed, 21 Oct 2026 07:28:00 GMT")) == 1.0
    # hdrs={} (a plain dict, as some tests construct) also falls back.
    assert netsafety._retry_after_seconds(urllib.error.HTTPError("u", 429, "m", {}, None)) == 1.0

    # 503 also retries.
    netsafety._last_request_at.clear()
    opener = MagicMock()
    opener.open.side_effect = [_http_error(503, "1"), sentinel]
    assert netsafety.polite_open(opener, _fake_req(), timeout=5, sleep=slept.append) is sentinel

    # A permanent 429 re-raises after exactly 1 + MAX_RATE_LIMIT_RETRIES calls.
    netsafety._last_request_at.clear()
    opener = MagicMock()
    opener.open.side_effect = _http_error(429, "1")
    with pytest.raises(urllib.error.HTTPError):
        netsafety.polite_open(opener, _fake_req(), timeout=5, sleep=slept.append)
    assert opener.open.call_count == 3

    # A non-rate-limit status re-raises immediately.
    netsafety._last_request_at.clear()
    opener = MagicMock()
    opener.open.side_effect = _http_error(404)
    with pytest.raises(urllib.error.HTTPError):
        netsafety.polite_open(opener, _fake_req(), timeout=5, sleep=slept.append)
    assert opener.open.call_count == 1

def test_throttle_host_enforces_min_interval_per_host(monkeypatch):
    monkeypatch.setattr(netsafety, "MIN_HOST_REQUEST_INTERVAL", 1.0)
    netsafety._last_request_at.clear()
    slept = []

    netsafety._throttle_host("api.printables.com", sleep=slept.append)
    assert slept == []

    netsafety._throttle_host("api.printables.com", sleep=slept.append)
    assert len(slept) == 1
    assert 0 < slept[0] <= 1.0

    # A different host is throttled independently.
    slept.clear()
    netsafety._throttle_host("files.printables.com", sleep=slept.append)
    assert slept == []

    # No host means no policy and no throttling.
    slept.clear()
    netsafety._throttle_host("", sleep=slept.append)
    assert slept == []

    # With the interval at 0 (the suite-wide conftest default) the throttle is
    # fully disarmed -- this is what keeps the test suite from really sleeping.
    monkeypatch.setattr(netsafety, "MIN_HOST_REQUEST_INTERVAL", 0.0)
    netsafety._last_request_at.clear()
    slept.clear()
    netsafety._throttle_host("api.printables.com", sleep=slept.append)
    netsafety._throttle_host("api.printables.com", sleep=slept.append)
    assert slept == []

def test_polite_open_tolerates_non_string_full_url():
    sentinel = object()
    slept = []
    opener = MagicMock()
    opener.open.return_value = sentinel

    assert netsafety.polite_open(opener, MagicMock(), timeout=5, sleep=slept.append) is sentinel
    assert slept == []
    assert netsafety._host_of(MagicMock().full_url) == ""


def test_browser_like_user_agent_darwin_and_windows():
    netsafety._default_user_agent.cache_clear()
    with patch("bambu_cli.netsafety.platform.system", return_value="Darwin"):
        ua = netsafety._default_user_agent()
        assert "Macintosh; Intel Mac OS X 10_15_7" in ua

    netsafety._default_user_agent.cache_clear()
    with patch("bambu_cli.netsafety.platform.system", return_value="Windows"):
        ua = netsafety._default_user_agent()
        assert "Windows NT 10.0; Win64; x64" in ua

    netsafety._default_user_agent.cache_clear()


def test_safe_https_connection_wrap_socket_failure_closes_sock():
    conn = netsafety.SafeHTTPSConnection("host.example.com", 443)
    mock_sock = MagicMock()
    mock_sock.close.side_effect = Exception("close error")
    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.side_effect = Exception("handshake fail")
    conn._context = mock_ctx
    with patch.object(netsafety, "_get_safe_connection", return_value=mock_sock):
        with pytest.raises(Exception, match="handshake fail"):
            conn.connect()
    mock_sock.close.assert_called_once()



# --- bounded hostname resolution (audit P2) ---------------------------------


def test_try_resolve_ip_reports_failure_instead_of_echoing_the_hostname():
    """_resolve_ip cannot express failure (it returns the host unchanged), which
    is what pushed cli.py into a second, unbounded socket.getaddrinfo. The
    _try_resolve_ip variant returns None so callers can branch honestly."""
    from bambu_cli.utils import _resolve_ip, _try_resolve_ip

    assert _try_resolve_ip("no-such-host.invalid", timeout=5.0) is None
    assert _resolve_ip("no-such-host.invalid", timeout=5.0) == "no-such-host.invalid"


def test_try_resolve_ip_passes_ip_literals_through_without_a_lookup(monkeypatch):
    import socket as socket_mod

    from bambu_cli import utils

    def _explode(*args, **kwargs):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket_mod, "getaddrinfo", _explode)
    assert utils._try_resolve_ip("192.0.2.10") == "192.0.2.10"
    assert utils._try_resolve_ip("::1") == "::1"


def test_try_resolve_ip_returns_none_when_the_lookup_outlives_the_timeout(monkeypatch):
    """The whole point of the timeout: a hung resolver must not block the CLI."""
    import socket as socket_mod
    import threading

    from bambu_cli import utils

    release = threading.Event()

    def _hang(*args, **kwargs):
        release.wait(30)
        return [(0, 0, 0, "", ("192.0.2.1", 0))]

    monkeypatch.setattr(socket_mod, "getaddrinfo", _hang)
    utils._RESOLVE_IP_CACHE.pop("slow.example", None)
    utils._RESOLVE_IP_INFLIGHT.pop("slow.example", None)
    try:
        assert utils._try_resolve_ip("slow.example", timeout=0.2) is None
    finally:
        release.set()


def test_concurrent_resolves_of_a_hung_host_share_one_worker_thread(monkeypatch):
    """A blackholed DNS server must park one thread, not one per call."""
    import socket as socket_mod
    import threading

    from bambu_cli import utils

    release = threading.Event()
    calls = []

    def _hang(host, *args, **kwargs):
        calls.append(host)
        release.wait(30)
        return [(0, 0, 0, "", ("192.0.2.1", 0))]

    monkeypatch.setattr(socket_mod, "getaddrinfo", _hang)
    utils._RESOLVE_IP_CACHE.pop("hung.example", None)
    utils._RESOLVE_IP_INFLIGHT.pop("hung.example", None)
    try:
        for _ in range(4):
            assert utils._try_resolve_ip("hung.example", timeout=0.1) is None
        assert len(calls) == 1, f"spawned {len(calls)} resolver threads for one host"
    finally:
        release.set()


# --- regressions found by mutation: each of these went green with the guarded
# line removed, so the line was untested ------------------------------------


def test_throttle_queued_callers_sleep_the_full_backlog_not_one_interval(monkeypatch):
    """`previous` is a scheduled timestamp: with three calls queued the third
    must wait ~2 intervals. Clamping the sleep to one interval (the old code)
    let queued callers burst past the throttle."""
    monkeypatch.setattr(netsafety, "MIN_HOST_REQUEST_INTERVAL", 1.0)
    netsafety._last_request_at.clear()
    slept = []
    for _ in range(3):
        netsafety._throttle_host("api.printables.com", sleep=slept.append)
    assert len(slept) == 2
    assert slept[1] > netsafety.MIN_HOST_REQUEST_INTERVAL * 1.5, slept
    assert slept[1] <= netsafety.MAX_RETRY_AFTER_WAIT


def test_throttle_backlog_is_capped_at_max_retry_after_wait(monkeypatch):
    monkeypatch.setattr(netsafety, "MIN_HOST_REQUEST_INTERVAL", 10.0)
    netsafety._last_request_at.clear()
    slept = []
    for _ in range(6):
        netsafety._throttle_host("api.printables.com", sleep=slept.append)
    assert max(slept) <= netsafety.MAX_RETRY_AFTER_WAIT


def test_try_resolve_ip_recovers_when_the_worker_thread_cannot_start(monkeypatch):
    """A failed Thread.start() must not leave a dead in-flight entry behind:
    every later call for that host would attach to it, wait its full timeout
    and report None forever, surfacing as a bogus 'Invalid printer_ip'."""
    import socket as socket_mod
    import threading

    from bambu_cli import utils

    utils._RESOLVE_IP_CACHE.pop("limit.example", None)
    utils._RESOLVE_IP_INFLIGHT.pop("limit.example", None)

    def _no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", _no_threads)
    assert utils._try_resolve_ip("limit.example", timeout=0.2) is None
    assert "limit.example" not in utils._RESOLVE_IP_INFLIGHT
    monkeypatch.undo()

    monkeypatch.setattr(socket_mod, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("192.0.2.9", 0))])
    assert utils._try_resolve_ip("limit.example", timeout=2.0) == "192.0.2.9"
    utils._RESOLVE_IP_CACHE.pop("limit.example", None)


def test_resolve_ip_cache_expires_so_a_moved_printer_is_re_resolved(monkeypatch):
    """The cached IP is handed to paho/ftplib as a literal, so a permanent
    cache would pin a long-lived process (the TUI) to a stale DHCP lease."""
    import socket as socket_mod

    from bambu_cli import utils

    utils._RESOLVE_IP_CACHE.pop("moved.example", None)
    utils._RESOLVE_IP_INFLIGHT.pop("moved.example", None)
    answers = iter(["192.0.2.1", "192.0.2.2"])
    monkeypatch.setattr(socket_mod, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (next(answers), 0))])

    assert utils._try_resolve_ip("moved.example", timeout=2.0) == "192.0.2.1"
    assert utils._try_resolve_ip("moved.example", timeout=2.0) == "192.0.2.1", "fresh entry must be served"

    ip, stamp = utils._RESOLVE_IP_CACHE["moved.example"]
    utils._RESOLVE_IP_CACHE["moved.example"] = (ip, stamp - utils._RESOLVE_IP_CACHE_TTL - 1)
    assert utils._try_resolve_ip("moved.example", timeout=2.0) == "192.0.2.2"
    utils._RESOLVE_IP_CACHE.pop("moved.example", None)
