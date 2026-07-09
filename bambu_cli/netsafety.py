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


@functools.lru_cache(maxsize=1)
def _default_user_agent():
    """Construct a User-Agent string that reflects the actual host OS."""
    system = platform.system()
    machine = platform.machine() or "x86_64"
    if system == "Darwin":
        os_label = "Macintosh; Intel Mac OS X 10_15_7"
    elif system == "Windows":
        os_label = "Windows NT 10.0; Win64; x64"
    else:
        os_label = f"X11; Linux {machine}"
    return f"Mozilla/5.0 ({os_label}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


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
