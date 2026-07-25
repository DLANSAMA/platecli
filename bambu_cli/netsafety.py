"""SSRF-safe HTTP layer: safe opener construction, per-hop IP validation,
DNS caching, and redirect hop limiting. No dependency on Printables/model
selection logic — this module is purely network-safety plumbing shared by
the download package and printables.py."""

import functools
import http.client
import ipaddress
import platform
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.cli import _redact_url_credentials
from bambu_cli.logging_utils import logger

_dns_cache: dict = {}
_dns_cache_lock = threading.Lock()

# Explicit redirect hop cap. Each hop is independently re-validated (scheme via
# handler registration, SSRF via _get_safe_connection on the real connect), but
# without an explicit low cap a malicious/misconfigured server could otherwise
# chain redirects up to urllib's built-in default of 10.
MAX_DOWNLOAD_REDIRECT_HOPS = 5


def _get_safe_connection(host, port, timeout, source_address):
    """Perform DNS resolution and validate IP is not internal/reserved."""
    from bambu_cli.constants import DNS_CACHE_TTL

    cache_key = (host, port)
    now = time.time()

    addr_info = None
    with _dns_cache_lock:
        if cache_key in _dns_cache:
            cached_info, timestamp = _dns_cache[cache_key]
            if now - timestamp < DNS_CACHE_TTL:
                addr_info = cached_info
            else:
                del _dns_cache[cache_key]

    if addr_info is None:
        try:
            addr_info = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            with _dns_cache_lock:
                if len(_dns_cache) > 1000:
                    _dns_cache.clear()
                _dns_cache[cache_key] = (addr_info, now)
        except socket.gaierror as e:
            raise urllib.error.URLError(f"DNS resolution failed for {host}: {e}") from e

    for res in addr_info:
        ip = res[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip)
            if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
                ip_obj = ip_obj.ipv4_mapped
            from bambu_cli.context import current_settings

            if not current_settings().allow_private_ips and not ip_obj.is_global:
                logger.warning(f"Security Error: Refusing connection to non-public IP ({ip}) for {host}")
                continue
        except ValueError:
            continue

        # Connect directly to the validated IP to prevent TOCTOU/DNS rebinding
        try:
            connect_port = int(port) if port is not None else 0
            return socket.create_connection((str(ip), connect_port), timeout, source_address)
        except OSError:
            continue

    # If all IPs fail, invalidate cache so next attempt resolves DNS again
    with _dns_cache_lock:
        _dns_cache.pop(cache_key, None)

    raise urllib.error.URLError(f"Could not connect to {host}: No safe/reachable IP addresses found")


class SafeHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _get_safe_connection(
            self.host,
            self.port,
            self.timeout,
            self.source_address,  # type: ignore[attr-defined]
        )


class SafeHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _get_safe_connection(
            self.host,
            self.port,
            self.timeout,
            self.source_address,  # type: ignore[attr-defined]
        )
        # Wrap with SSL using the original hostname for SNI.
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise


class SafeHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Enforce an explicit, low redirect hop cap with a clear error.

    Each hop still passes through the Safe* connection classes (per-hop SSRF
    re-validation) and the caller re-checks scheme/extension/content-type on
    the final URL, but without this the stock handler would allow up to its
    own default of 10 hops before failing with a generic HTTPError.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        hop_count = getattr(req, "_bambu_redirect_hops", 0) + 1
        if hop_count > MAX_DOWNLOAD_REDIRECT_HOPS:
            raise urllib.error.URLError(
                f"Too many redirects: exceeded the {MAX_DOWNLOAD_REDIRECT_HOPS}-hop "
                f"limit while fetching {_redact_url_credentials(req.full_url)}"
            )
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req._bambu_redirect_hops = hop_count  # type: ignore[attr-defined]
        return new_req


class SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(SafeHTTPConnection, req)


class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        kwargs = {}
        if hasattr(self, "_context"):
            kwargs["context"] = self._context
        if hasattr(self, "_check_hostname"):
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(SafeHTTPSConnection, req, **kwargs)


PROJECT_URL = "https://github.com/DLANSAMA/platecli"

# Hosts we speak to as a first-party API client: identify honestly, never
# impersonate a browser, never forge browser-only headers. Exact-host matching
# (not suffix matching) so a lookalike such as "printables.com.evil.example"
# can never inherit the first-party policy.
HONEST_UA_HOSTS = frozenset(
    {
        "api.printables.com",
        "files.printables.com",
        "printables.com",
        "www.printables.com",
    }
)


@functools.lru_cache(maxsize=1)
def platecli_user_agent() -> str:
    """Honest, identifying User-Agent: ``platecli/<version> (+<project url>)``.

    The version comes from the single source of truth: ``bambu_cli.constants``
    resolves it lazily from installed package metadata, falling back to
    pyproject.toml. Never hardcode a version literal here.
    """
    from bambu_cli import constants

    return f"platecli/{constants.VERSION} (+{PROJECT_URL})"


@functools.lru_cache(maxsize=1)
def _default_user_agent() -> str:
    """User-Agent for generic, user-supplied file URLs.

    Deliberately still browser-shaped: many CDNs and file hosts (the arbitrary
    URLs a user pastes into ``plate download``) 403 non-browser clients, and a
    failed download is a real UX regression. The honest ``platecli/<version>``
    token is appended so the client is always identifiable and attributable --
    this is compatibility, not impersonation. First-party API hosts get
    :func:`platecli_user_agent` instead; see :func:`user_agent_for_url`.
    """
    system = platform.system()
    machine = platform.machine() or "x86_64"
    if system == "Darwin":
        os_label = "Macintosh; Intel Mac OS X 10_15_7"
    elif system == "Windows":
        os_label = "Windows NT 10.0; Win64; x64"
    else:
        os_label = f"X11; Linux {machine}"
    return (
        f"Mozilla/5.0 ({os_label}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/120.0.0.0 Safari/537.36 {platecli_user_agent()}"
    )


def _host_of(url) -> str:
    """Best-effort lowercase hostname. Never raises.

    Defensive on purpose: callers pass ``req.full_url``, and the test suite
    patches ``urllib.request.Request`` with a MagicMock whose ``full_url`` is
    not a string. ``urlparse`` on such a value raises ``TypeError``. Returning
    "" means "no host policy, no throttling", which is the safe degradation.
    """
    try:
        return (urlparse(url).hostname or "").lower()
    except (TypeError, ValueError, AttributeError):
        return ""


def user_agent_for_url(url) -> str:
    """Pick the UA policy for ``url``: honest for first-party API hosts,
    browser-compatible for arbitrary user-supplied download URLs."""
    if _host_of(url) in HONEST_UA_HOSTS:
        return platecli_user_agent()
    return _default_user_agent()


# --- polite client -----------------------------------------------------------
# Minimum wall-clock gap between two requests to the same host, plus the
# 429/503 backoff policy. platecli issues a handful of requests per invocation,
# so this costs users almost nothing and keeps us a well-behaved third party.
# MIN_HOST_REQUEST_INTERVAL is read at call time (not captured), so the test
# suite can zero it via the autouse fixture in tests/conftest.py.
MIN_HOST_REQUEST_INTERVAL = 1.0
MAX_RETRY_AFTER_WAIT = 30.0
MAX_RATE_LIMIT_RETRIES = 2

_last_request_at: dict = {}
_last_request_lock = threading.Lock()


def _throttle_host(host, sleep=time.sleep) -> None:
    """Block until MIN_HOST_REQUEST_INTERVAL has passed since the last request
    to ``host``. ``sleep`` is injectable so tests never really wait."""
    if not host:
        return
    with _last_request_lock:
        previous = _last_request_at.get(host)
        now = time.monotonic()
        wait = 0.0 if previous is None else MIN_HOST_REQUEST_INTERVAL - (now - previous)
        wait = max(wait, 0.0)
        _last_request_at[host] = now + wait
    if wait > 0:
        sleep(min(wait, MIN_HOST_REQUEST_INTERVAL))


def _retry_after_seconds(err) -> float:
    """Parse Retry-After, clamped. Non-numeric (HTTP-date) values fall back to
    the normal polite interval rather than raising."""
    headers = getattr(err, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return MIN_HOST_REQUEST_INTERVAL
    return max(0.0, min(seconds, MAX_RETRY_AFTER_WAIT))


def polite_open(opener, req, timeout=None, sleep=time.sleep):
    """``opener.open`` with per-host throttling and 429/503 Retry-After respect.

    Returns the live response object (callers keep using it as a context
    manager). Re-raises the final HTTPError if the server keeps rate-limiting.
    """
    host = _host_of(getattr(req, "full_url", ""))
    attempt = 0
    while True:
        _throttle_host(host, sleep=sleep)
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as err:
            if err.code not in (429, 503) or attempt >= MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _retry_after_seconds(err)
            logger.warning(f"Rate limited by {host or 'server'} (HTTP {err.code}); waiting {delay:.0f}s before retry")
            attempt += 1
            sleep(delay)


def build_safe_opener():
    """Build a urllib opener that only uses safe handlers and restricts schemes."""
    opener = urllib.request.OpenerDirector()
    # Disable environment proxies so target IP validation cannot be bypassed by
    # asking a proxy to fetch an internal/private address on our behalf.
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.UnknownHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(SafeHTTPRedirectHandler())
    opener.add_handler(SafeHTTPHandler())
    opener.add_handler(SafeHTTPSHandler())
    return opener
