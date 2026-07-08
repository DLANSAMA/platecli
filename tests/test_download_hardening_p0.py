"""P0 hardening regression tests for the bambu_cli.download package.

Covers gaps documented in docs/test-backlog.md P3: redirect revalidation
(SSRF + extension), explicit redirect hop cap, mid-stream size enforcement,
short-read detection, empty-file rejection, and Content-Disposition
filename hardening (RFC 2231 decoding + extension re-check).

Ground rules (docs/test-backlog.md): patch functions in the module that
calls them (bambu_cli.download.*), patch runtime state on bambu_cli.bambu,
never touch the network.
"""
import os
import socket
import sys
import types
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli import download  # noqa: E402
from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_NETWORK_ERROR  # noqa: E402
from bambu_cli.errors import BambuError


def _args(tmp_path, url, **overrides):
    base = dict(
        url=url, output=str(tmp_path), name=None, max_download_mb=1,
        json=False, progress=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _mock_opener(mock_resp):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = mock_resp
    return opener


def _base_resp(url, body=b"x" * 100, content_type="model/stl", content_disposition=None):
    resp = MagicMock()
    resp.geturl.return_value = url

    def getheader(name, default=None):
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Content-Disposition": content_disposition,
        }
        return headers.get(name, default)

    resp.getheader.side_effect = getheader
    chunks = [body]

    def read(n=-1):
        if not chunks:
            return b""
        chunk = chunks.pop(0)
        return chunk

    resp.read.side_effect = read
    return resp


# ---------------------------------------------------------------------------
# Redirect hop cap
# ---------------------------------------------------------------------------

def test_redirect_hop_cap_enforced():
    """More than MAX_DOWNLOAD_REDIRECT_HOPS hops must raise a clear URLError."""
    req = types.SimpleNamespace(full_url="https://example.com/start", _bambu_redirect_hops=download.MAX_DOWNLOAD_REDIRECT_HOPS)
    handler = download.SafeHTTPRedirectHandler()
    with pytest.raises(urllib.error.URLError) as excinfo:
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/next")
    assert "Too many redirects" in str(excinfo.value)


def test_safe_opener_uses_capped_redirect_handler():
    import urllib.request

    opener = download.build_safe_opener()
    handler_types = [type(h) for h in opener.handlers]
    assert download.SafeHTTPRedirectHandler in handler_types
    # A plain HTTPRedirectHandler (no hop cap) must not also be registered.
    assert urllib.request.HTTPRedirectHandler not in handler_types


# ---------------------------------------------------------------------------
# Redirect revalidation: SSRF + extension
# ---------------------------------------------------------------------------

def test_redirect_to_private_ip_blocked(tmp_path):
    """A redirected connection resolving to a private IP must be refused,
    same as the initial hop (per-hop SSRF check via _get_safe_connection)."""
    addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
    download._dns_cache.clear()
    with patch.object(download.socket, "getaddrinfo", return_value=addr_info):
        with pytest.raises(urllib.error.URLError):
            download._get_safe_connection("internal.example.com", 443, 5, None)
    download._dns_cache.clear()


def test_redirected_url_with_unsupported_extension_rejected(tmp_path):
    """If the response's final (post-redirect) URL has a disallowed
    extension, the download must be rejected even though the original URL
    looked fine."""
    original_url = "https://example.com/model.stl"
    final_url = "https://example.com/payload.pdf"
    resp = _base_resp(final_url)
    opener = _mock_opener(resp)
    args = _args(tmp_path, original_url)

    with patch.object(download, "build_safe_opener", return_value=opener):
        with pytest.raises((SystemExit, BambuError)) as excinfo:
            download._cmd_download(args)

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    leftovers = [p for p in tmp_path.iterdir() if p.stat().st_size > 0]
    assert not leftovers, f"partial download not cleaned up: {leftovers}"


# ---------------------------------------------------------------------------
# Mid-stream size enforcement / short reads / empty files
# ---------------------------------------------------------------------------

def test_mid_stream_oversize_deletes_partial_file(tmp_path):
    """Even without a Content-Length header, exceeding max_download_mb mid
    stream must abort and remove the partial file."""
    url = "https://example.com/model.stl"
    resp = MagicMock()
    resp.geturl.return_value = url
    resp.getheader.return_value = None
    resp.read.side_effect = lambda n: b"x" * n  # endless stream
    opener = _mock_opener(resp)
    args = _args(tmp_path, url, max_download_mb=1)

    with patch.object(download, "build_safe_opener", return_value=opener):
        with pytest.raises((SystemExit, BambuError)) as excinfo:
            download._cmd_download(args)

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    leftovers = [p for p in tmp_path.iterdir() if p.stat().st_size > 0]
    assert not leftovers, f"partial download not cleaned up: {leftovers}"


def test_short_read_detected_and_partial_removed(tmp_path):
    """Content-Length promised more bytes than the body actually delivered."""
    url = "https://example.com/model.stl"
    resp = MagicMock()
    resp.geturl.return_value = url

    def getheader(name, default=None):
        if name == "Content-Length":
            return "1000"
        if name == "Content-Type":
            return "model/stl"
        return default

    resp.getheader.side_effect = getheader
    body_chunks = [b"only-part-of-the-data"]

    def read(n=-1):
        if body_chunks:
            return body_chunks.pop(0)
        return b""

    resp.read.side_effect = read
    opener = _mock_opener(resp)
    args = _args(tmp_path, url, max_download_mb=100)

    with patch.object(download, "build_safe_opener", return_value=opener):
        with pytest.raises((SystemExit, BambuError)) as excinfo:
            download._cmd_download(args)

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_NETWORK_ERROR
    leftovers = [p for p in tmp_path.iterdir() if p.stat().st_size > 0]
    assert not leftovers, f"partial download not cleaned up: {leftovers}"


def test_empty_download_rejected(tmp_path):
    url = "https://example.com/model.stl"
    resp = _base_resp(url, body=b"", content_type="model/stl")
    # Content-Length of 0 with no bytes at all.
    resp.getheader.side_effect = lambda name, default=None: {
        "Content-Type": "model/stl",
        "Content-Length": "0",
    }.get(name, default)
    resp.read.side_effect = lambda n=-1: b""
    opener = _mock_opener(resp)
    args = _args(tmp_path, url, max_download_mb=100)

    with patch.object(download, "build_safe_opener", return_value=opener):
        with pytest.raises((SystemExit, BambuError)) as excinfo:
            download._cmd_download(args)

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    leftovers = [p for p in tmp_path.iterdir() if p.stat().st_size > 0]
    assert not leftovers, f"partial download not cleaned up: {leftovers}"


# ---------------------------------------------------------------------------
# Content-Disposition filename hardening
# ---------------------------------------------------------------------------

def test_rfc2231_filename_star_decoded_and_sanitized():
    """filename* (RFC 2231/5987) must be decoded and then sanitized just like
    a plain filename (path separators / traversal stripped)."""
    value = "attachment; filename*=UTF-8''..%2F%2Fetc%2Fpasswd%2Fmodel.stl"
    result = download._filename_from_content_disposition(value)
    assert result is not None
    assert "/" not in result
    assert ".." not in result
    assert result.endswith(".stl")


def test_content_disposition_disallowed_extension_not_smuggled(tmp_path):
    """A Content-Disposition header must not be able to smuggle a disallowed
    extension onto the saved file: the allowlist is re-applied to the final
    effective filename, forcing a supported extension regardless of what the
    header claims."""
    from bambu_cli.constants import DOWNLOADABLE_EXTENSIONS

    url = "https://example.com/download?id=1"  # no extension in URL itself
    resp = _base_resp(
        url, content_type="application/octet-stream",
        content_disposition='attachment; filename="payload.exe"',
    )
    opener = _mock_opener(resp)
    args = _args(tmp_path, url, max_download_mb=100)

    with patch.object(download, "build_safe_opener", return_value=opener):
        outpath = download._cmd_download(args)

    assert not outpath.endswith(".exe")
    assert os.path.splitext(outpath)[1].lower() in DOWNLOADABLE_EXTENSIONS
