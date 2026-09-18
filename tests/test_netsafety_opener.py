"""End-to-end tests for the safe opener, driven through a real local server.

Why a real server instead of the usual mocks: the opener chain was missing
``urllib.request.HTTPErrorProcessor`` for a long time, which silently disabled
redirect following and HTTPError raising. Every existing redirect test called
``SafeHTTPRedirectHandler.redirect_request(...)`` directly, or asserted the
handler was *registered* -- both pass against a chain that can never invoke the
handler. Only opening a URL catches it, so these tests do exactly that.

The server binds 127.0.0.1, so every test here runs inside
``settings_ctx(allow_private_ips=True)``; the SSRF guard is covered separately
in tests/test_netsafety.py.
"""

import http.server
import threading
import urllib.error
import urllib.request

import pytest

from bambu_cli.netsafety import MAX_DOWNLOAD_REDIRECT_HOPS, build_safe_opener, polite_open
from tests.bambu_test_base import settings_ctx

BODY = b"solid cube\nendsolid cube\n"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Routes: /final, /redirect-once, /hop/<n> (chains down to /final), /missing, /boom."""

    # Deliberately HTTP/1.0 (the default): keep-alive would leave the server-side
    # connection lingering in its handler thread past the end of the test, which
    # surfaces as a ResourceWarning -- and CI runs with -W error::ResourceWarning.
    # Every response below carries an explicit Content-Length, so nothing here
    # depends on 1.1 framing.

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's required spelling
        path = self.path
        if path == "/final":
            self._send(200, BODY)
        elif path == "/redirect-once":
            self._send(302, headers=[("Location", "/final")])
        elif path.startswith("/hop/"):
            remaining = int(path.rsplit("/", 1)[1])
            target = "/final" if remaining <= 1 else f"/hop/{remaining - 1}"
            self._send(302, headers=[("Location", target)])
        elif path == "/missing":
            self._send(404, b"<html>not found</html>", [("Content-Type", "text/html")])
        else:
            self._send(500, b"kaboom")

    def log_message(self, *args):
        """Silence the default stderr access log."""


@pytest.fixture
def server():
    # Single-threaded on purpose. ThreadingHTTPServer hands each connection to a
    # thread whose socket is then torn down by GC rather than explicitly, which
    # trips ResourceWarning -- and CI runs with -W error::ResourceWarning. With
    # HTTP/1.0 every connection closes after one response, so serving
    # sequentially is sufficient for these tests.
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _open(base, path):
    opener = build_safe_opener()
    return opener.open(urllib.request.Request(f"{base}{path}"), timeout=10)


def test_opener_registers_an_http_error_processor():
    """Without this handler the whole error/redirect chain is inert."""
    opener = build_safe_opener()
    for scheme in ("http", "https"):
        processors = [type(h).__name__ for h in opener.process_response.get(scheme, [])]
        assert "HTTPErrorProcessor" in processors, f"{scheme} has no error processor: {processors}"


def test_redirect_is_actually_followed(server):
    with settings_ctx(allow_private_ips=True), _open(server, "/redirect-once") as resp:
        assert resp.status == 200
        assert resp.read() == BODY
        assert resp.url.endswith("/final")


def test_redirect_chain_within_the_cap_succeeds(server):
    with settings_ctx(allow_private_ips=True), _open(server, f"/hop/{MAX_DOWNLOAD_REDIRECT_HOPS - 1}") as resp:
        assert resp.status == 200
        assert resp.read() == BODY


def test_redirect_chain_over_the_cap_is_refused(server):
    with settings_ctx(allow_private_ips=True), pytest.raises(urllib.error.URLError) as excinfo:
        _open(server, f"/hop/{MAX_DOWNLOAD_REDIRECT_HOPS + 3}")
    assert "Too many redirects" in str(excinfo.value)


def test_404_raises_httperror_rather_than_returning_the_error_page(server):
    with settings_ctx(allow_private_ips=True), pytest.raises(urllib.error.HTTPError) as excinfo:
        _open(server, "/missing")
    assert excinfo.value.code == 404
    excinfo.value.close()


def test_500_raises_httperror(server):
    with settings_ctx(allow_private_ips=True), pytest.raises(urllib.error.HTTPError) as excinfo:
        _open(server, "/boom")
    assert excinfo.value.code == 500
    excinfo.value.close()


def test_polite_open_surfaces_httperror_so_its_retry_policy_can_see_it(server):
    """polite_open only retries 429/503 because it catches HTTPError -- which
    requires the error processor to have raised one in the first place."""
    opener = build_safe_opener()
    req = urllib.request.Request(f"{server}/missing")
    with settings_ctx(allow_private_ips=True), pytest.raises(urllib.error.HTTPError) as excinfo:
        polite_open(opener, req, timeout=10, sleep=lambda _: None)
    assert excinfo.value.code == 404
    excinfo.value.close()


# --- the user-visible consequence ------------------------------------------
# A download behind a redirect is the common case on real file hosts (signed
# CDN URLs, release assets). With the opener chain inert it failed with
# "Downloaded file is empty; refusing to use it.", and a 404 was misreported as
# "HTML page did not contain a direct model file link". Both are driven here
# through cmd_download against the live server, not a mocked opener.


def test_cmd_download_follows_a_redirect_to_the_real_model(server, tmp_path):
    import argparse

    from bambu_cli.commands import cmd_download

    outdir = tmp_path / "out"
    outdir.mkdir()
    args = argparse.Namespace(
        url=f"{server}/redirect-once",
        output=str(outdir),
        name=None,
        max_download_mb=100,
        allow_private_ips=True,
        json=False,
    )
    with settings_ctx(allow_private_ips=True):
        cmd_download(args)

    written = list(outdir.iterdir())
    assert len(written) == 1, f"expected exactly one downloaded file, got {written}"
    assert written[0].read_bytes() == BODY


def test_cmd_download_reports_a_404_as_a_network_error_with_the_status(server, tmp_path):
    import argparse

    from bambu_cli.errors import BambuError

    from bambu_cli.commands import cmd_download

    outdir = tmp_path / "out"
    outdir.mkdir()
    args = argparse.Namespace(
        url=f"{server}/missing",
        output=str(outdir),
        name=None,
        max_download_mb=100,
        allow_private_ips=True,
        json=False,
    )
    with settings_ctx(allow_private_ips=True), pytest.raises((BambuError, SystemExit)):
        cmd_download(args)

    assert list(outdir.iterdir()) == [], "a 404 body must never be left on disk as a model"


# --- SSRF re-validation per hop ---------------------------------------------
# "Each hop is independently re-validated" was the redirect handler's stated
# guarantee, but while redirects were never followed it was untestable and
# untested. _get_safe_connection's refusal of private/reserved IPs is covered in
# tests/test_netsafety.py; what is covered here is that every hop actually
# reaches that gate, rather than only the first one.


def test_every_redirect_hop_goes_through_the_ssrf_gate(server, monkeypatch):
    from bambu_cli import netsafety

    seen = []
    real = netsafety._get_safe_connection

    def _spy(host, port, timeout, source_address):
        seen.append((host, port))
        return real(host, port, timeout, source_address)

    monkeypatch.setattr(netsafety, "_get_safe_connection", _spy)

    with settings_ctx(allow_private_ips=True), _open(server, f"/hop/{MAX_DOWNLOAD_REDIRECT_HOPS - 1}") as resp:
        assert resp.read() == BODY

    # One validated connect per hop, plus the final 200 -- not just the first.
    assert len(seen) == MAX_DOWNLOAD_REDIRECT_HOPS, f"only {len(seen)} hop(s) validated: {seen}"


def test_a_single_redirect_validates_both_hops(server, monkeypatch):
    from bambu_cli import netsafety

    seen = []
    real = netsafety._get_safe_connection

    def _spy(host, port, timeout, source_address):
        seen.append((host, port))
        return real(host, port, timeout, source_address)

    monkeypatch.setattr(netsafety, "_get_safe_connection", _spy)
    with settings_ctx(allow_private_ips=True), _open(server, "/redirect-once") as resp:
        assert resp.read() == BODY
    assert len(seen) == 2, f"expected the 302 and the 200 to be validated separately: {seen}"


def test_hop_cap_closes_the_intermediate_response_before_raising():
    """HTTPRedirectHandler.http_error_302 calls redirect_request first and only
    reaches its own fp.close() afterwards, so bailing out on the hop cap has to
    close the 3xx response itself or leak its socket to the garbage collector."""
    from unittest.mock import MagicMock

    from bambu_cli.netsafety import SafeHTTPRedirectHandler

    handler = SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    req._bambu_redirect_hops = MAX_DOWNLOAD_REDIRECT_HOPS
    fp = MagicMock()

    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, fp, 302, "Found", {}, "https://example.com/next")

    fp.close.assert_called_once()


def test_hop_cap_tolerates_a_missing_intermediate_response():
    """Several existing tests call redirect_request with fp=None."""
    from bambu_cli.netsafety import SafeHTTPRedirectHandler

    handler = SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    req._bambu_redirect_hops = MAX_DOWNLOAD_REDIRECT_HOPS

    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/next")
