"""Unit tests for generic-HTML model-link extraction.

Covers bambu_cli.download.html_links (`_ModelLinkParser`,
`_resolve_html_model_link`, `_is_html_content_type`) — the fallback path
that scrapes a direct model/print link out of an arbitrary HTML page when a
URL is not a recognized Printables model page. Pure parsing logic, no
network. Ground rules (docs/test-backlog.md): never touch the network.
"""

import sys
from unittest.mock import MagicMock, patch

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)

from bambu_cli.download import html_links  # noqa: E402
from bambu_cli.download.html_links import (  # noqa: E402
    _is_html_content_type,
    _resolve_html_model_link,
)

BASE = "https://example.com/models/42"


def _resolve(html, base=BASE):
    return _resolve_html_model_link(html.encode("utf-8"), base)


# ---------------------------------------------------------------------------
# _is_html_content_type
# ---------------------------------------------------------------------------
def test_is_html_content_type_variants():
    assert _is_html_content_type("text/html")
    assert _is_html_content_type("text/html; charset=utf-8")
    assert _is_html_content_type("  TEXT/HTML  ")
    assert _is_html_content_type("application/xhtml+xml")
    assert not _is_html_content_type("model/stl")
    assert not _is_html_content_type(None)
    assert not _is_html_content_type("")


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------
def test_extracts_single_absolute_stl_link():
    url, name = _resolve('<a href="https://cdn.example.com/part.stl">dl</a>')
    assert url == "https://cdn.example.com/part.stl"
    assert name == "part.stl"


def test_relative_link_resolved_against_base():
    url, name = _resolve('<a href="../files/widget.3mf">x</a>')
    assert url == "https://example.com/files/widget.3mf"
    assert name == "widget.3mf"


def test_root_relative_link_resolved_against_base():
    url, name = _resolve('<a href="/d/thing.stl">x</a>')
    assert url == "https://example.com/d/thing.stl"
    assert name == "thing.stl"


# ---------------------------------------------------------------------------
# Extension-priority selection
# ---------------------------------------------------------------------------
def test_prefers_stl_over_zip_by_priority():
    html = '<a href="/a/bundle.zip">zip</a><a href="/a/mesh.stl">stl</a>'
    url, name = _resolve(html)
    assert name == "mesh.stl"


def test_prefers_3mf_over_gcode_and_zip():
    html = '<a href="/x/print.gcode">g</a><a href="/x/model.3mf">m</a><a href="/x/archive.zip">z</a>'
    _, name = _resolve(html)
    assert name == "model.3mf"


def test_first_seen_breaks_priority_tie():
    html = '<a href="/one/first.stl">1</a><a href="/two/second.stl">2</a>'
    url, name = _resolve(html)
    assert url == "https://example.com/one/first.stl"
    assert name == "first.stl"


# ---------------------------------------------------------------------------
# Filename-hint fallback (path lacks a usable extension)
# ---------------------------------------------------------------------------
def test_filename_hint_used_when_path_has_no_extension():
    url, name = _resolve('<a href="/download?id=5" download="model.3mf">get</a>')
    assert url == "https://example.com/download?id=5"
    assert name == "model.3mf"


def test_hint_ignored_when_path_extension_already_valid():
    url, name = _resolve('<a href="/real.stl" download="decoy.gcode">x</a>')
    assert name == "real.stl"


def test_data_attributes_are_scanned():
    url, name = _resolve('<div data-download-url="https://cdn.example.com/z.obj"></div>')
    assert url == "https://cdn.example.com/z.obj"
    assert name == "z.obj"


def test_self_closing_tag_link_extracted():
    url, name = _resolve('<img src="/imgs/scan.stl" />')
    assert url == "https://example.com/imgs/scan.stl"
    assert name == "scan.stl"


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------
def test_javascript_mailto_data_hash_schemes_rejected():
    html = (
        '<a href="#section">a</a>'
        '<a href="javascript:void(0)">b</a>'
        '<a href="mailto:contact">c</a>'
        '<a href="data:text/plain;base64,AAAA">d</a>'
    )
    assert _resolve(html) == (None, None)


def test_non_http_scheme_rejected():
    assert _resolve('<a href="ftp://example.com/f.stl">x</a>') == (None, None)


def test_unsupported_extension_rejected():
    assert _resolve('<a href="/readme.txt">x</a>') == (None, None)


def test_empty_page_returns_none():
    assert _resolve_html_model_link(b"", BASE) == (None, None)
    assert _resolve_html_model_link(None, BASE) == (None, None)


def test_no_candidates_returns_none():
    assert _resolve("<html><body><p>nothing here</p></body></html>") == (None, None)


# ---------------------------------------------------------------------------
# Dedup + scan-limit truncation
# ---------------------------------------------------------------------------
def test_identical_links_deduped_still_resolve():
    html = '<a href="/dup/part.stl">1</a><a href="/dup/part.stl">2</a>'
    url, name = _resolve(html)
    assert url == "https://example.com/dup/part.stl"
    assert name == "part.stl"


def test_scan_limit_truncates_tail_links():
    # A valid link that sits entirely past the scan window must be ignored.
    with patch.object(html_links, "HTML_LINK_SCAN_LIMIT", 64):
        padding = "<!-- " + "x" * 200 + " -->"
        html = padding + '<a href="/late/hidden.stl">x</a>'
        assert _resolve(html) == (None, None)


def test_link_within_scan_limit_is_kept():
    with patch.object(html_links, "HTML_LINK_SCAN_LIMIT", 4096):
        _, name = _resolve('<a href="/early/kept.stl">x</a>')
        assert name == "kept.stl"


def test_malformed_bytes_do_not_raise():
    # Invalid UTF-8 is decoded with errors="replace"; must not raise.
    url, name = _resolve_html_model_link(b"\xff\xfe<a href='/m/ok.stl'>x</a>", BASE)
    assert name == "ok.stl"
