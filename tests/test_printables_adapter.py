"""Tests for the Printables adapter — the sandbox around an undocumented API.

These drive the **public** surface (``bambu_cli.printables``) with an injected
opener. Nothing here patches a module global, and nothing imports
``printables.client``: if a test needs to reach past the adapter to be written,
the adapter is not doing its job.

Two things are under test:

1. **Behavior** — URL detection, file preference (STL > STEP > 3MF), and the
   failure taxonomy (unavailable / contract-changed / model-unavailable).
2. **Containment** — the guarantee in ``adapter.py``: no Printables failure
   escapes as an exception. The malformed-payload sweep at the bottom is the
   real point of the package; it is what stops a schema change from taking down
   ``plate job``.

Ground rules (docs/test-backlog.md): never touch the network.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from bambu_cli.printables import (  # noqa: E402
    PrintablesAdapter,
    is_printables_url,
    resolve_printables,
    resolve_printables_url,
)

MODEL_URL = "https://www.printables.com/model/12345-test-model"

# --- fakes -------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, *_args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

class _FakeOpener:
    """Yields the queued responses in order; raises if one is an Exception."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if not self._queue:
            raise AssertionError("adapter made more API calls than the test queued")
        nxt = self._queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return _FakeResponse(nxt)

def _adapter(*responses):
    opener = _FakeOpener(*responses)
    return PrintablesAdapter(opener_factory=lambda: opener), opener

def _model(stls=None, gcodes=None, name="Test Model"):
    return {"data": {"print": {"name": name, "stls": stls or [], "gcodes": gcodes or []}}}

def _link(url):
    return {"data": {"getDownloadLink": {"ok": True, "output": {"link": url}}}}

# --- URL detection -----------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://www.printables.com/model/12345-test-model",
        "https://printables.com/model/1",
        "https://www.printables.com/model/999/files",
    ],
)
def test_recognises_model_urls(url):
    assert is_printables_url(url) is True

@pytest.mark.parametrize(
    "url",
    [
        "https://www.thingiverse.com/thing:12345",
        "https://www.printables.com/social/12345",
        # Lookalike hosts must not be treated as Printables (exact-host match).
        "https://printables.com.evil.example/model/1",
        "https://evil.printables.com.attacker.net/model/1",
        "",
        None,
        12345,
    ],
)
def test_rejects_non_model_urls(url):
    assert is_printables_url(url) is False

def test_non_printables_url_resolves_to_a_typed_refusal_without_network():
    adapter, opener = _adapter()  # no responses queued: any call would assert
    result = adapter.resolve("https://www.thingiverse.com/thing:12345")
    assert result.ok is False
    assert result.reason == "not_a_printables_url"
    assert opener.requests == []

# --- happy paths -------------------------------------------------------------

@patch("bambu_cli.logging_utils._BACKEND")
def test_resolves_stl_to_a_download_url(_log):
    adapter, opener = _adapter(
        _model(stls=[{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}]),
        _link("https://download.example.com/part1.stl"),
    )
    result = adapter.resolve(MODEL_URL)
    assert result.ok is True
    assert result.url == "https://download.example.com/part1.stl"
    assert result.filename == "part1.stl"
    assert len(opener.requests) == 2

@patch("bambu_cli.logging_utils._BACKEND")
def test_picks_the_largest_of_several_stls(_log):
    adapter, _ = _adapter(
        _model(
            stls=[
                {"id": "1", "name": "part1.stl", "fileSize": 1024},
                {"id": "2", "name": "part2.stl", "fileSize": 2048},
            ]
        ),
        _link("https://download.example.com/part2.stl"),
    )
    assert adapter.resolve(MODEL_URL).filename == "part2.stl"

@patch("bambu_cli.logging_utils._BACKEND")
def test_falls_back_to_step_when_no_stl(_log):
    adapter, _ = _adapter(
        _model(stls=[{"name": "part1.step", "fileSize": 1024, "id": "file_123"}]),
        _link("https://download.example.com/part1.step"),
    )
    result = adapter.resolve(MODEL_URL)
    assert result.ok is True
    assert result.filename == "part1.step"

@patch("bambu_cli.logging_utils._BACKEND")
def test_falls_back_to_3mf_and_warns_it_cannot_be_resliced(mock_log):
    adapter, _ = _adapter(
        _model(gcodes=[{"name": "part1.3mf", "fileSize": 1024, "id": "file_123"}]),
        _link("https://download.example.com/part1.3mf"),
    )
    result = adapter.resolve(MODEL_URL)
    assert result.ok is True
    assert result.filename == "part1.3mf"
    assert any("falling back to 3MF" in c[0][0] for c in mock_log.warning.call_args_list)

@patch("bambu_cli.logging_utils._BACKEND")
def test_stl_is_preferred_over_step_and_3mf(_log):
    adapter, _ = _adapter(
        _model(
            stls=[
                {"id": "s", "name": "big.step", "fileSize": 9999},
                {"id": "m", "name": "big.3mf", "fileSize": 9999},
                {"id": "t", "name": "small.stl", "fileSize": 1},
            ]
        ),
        _link("https://download.example.com/small.stl"),
    )
    assert adapter.resolve(MODEL_URL).filename == "small.stl"

# --- failure taxonomy --------------------------------------------------------

@patch("bambu_cli.logging_utils._BACKEND")
def test_network_error_is_reported_as_unavailable(_log):
    adapter, _ = _adapter(urllib.error.URLError("Network unreachable"))
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_unavailable"
    assert "Network" in result.error
    assert result.remedy

@patch("bambu_cli.logging_utils._BACKEND")
def test_missing_model_is_reported_as_model_unavailable(_log):
    adapter, _ = _adapter({"data": {"print": None}})
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_model_unavailable"
    assert "12345" in result.error

@patch("bambu_cli.logging_utils._BACKEND")
def test_model_without_printable_files_is_model_unavailable(_log):
    adapter, _ = _adapter(_model(stls=[{"id": "1", "name": "readme.txt", "fileSize": 10}]))
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_model_unavailable"
    assert "No STL, STEP, or 3MF" in result.error

@patch("bambu_cli.logging_utils._BACKEND")
def test_refused_download_link_surfaces_the_servers_reason(_log):
    adapter, _ = _adapter(
        _model(stls=[{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}]),
        {"data": {"getDownloadLink": {"ok": False, "errors": [{"field": "link", "messages": ["Download limit reached"]}]}}},
    )
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_model_unavailable"
    assert "Download limit reached" in result.error

@patch("bambu_cli.logging_utils._BACKEND")
def test_non_json_body_is_reported_as_a_contract_change(_log):
    adapter, _ = _adapter(b"<html>we redesigned our API</html>")
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_contract_changed"
    # The remedy must tell the user this is not something retrying will fix.
    assert "manually" in result.remedy or "browser" in result.remedy

@patch("bambu_cli.logging_utils._BACKEND")
def test_file_record_without_id_is_a_contract_change_not_a_crash(_log):
    # id/name are what the download step needs; losing them means the schema moved.
    adapter, _ = _adapter(_model(stls=[{"name": "part1.stl", "fileSize": 1024}]))
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_contract_changed"

@patch("bambu_cli.logging_utils._BACKEND")
def test_ok_link_with_no_url_is_a_contract_change(_log):
    adapter, _ = _adapter(
        _model(stls=[{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}]),
        {"data": {"getDownloadLink": {"ok": True, "output": {}}}},
    )
    result = adapter.resolve(MODEL_URL)
    assert result.ok is False
    assert result.reason == "printables_contract_changed"

# --- containment: the reason this package exists -----------------------------

# Every payload here is something Printables could plausibly start returning.
# None of them may raise. This is a regression net for "they changed the API".
HOSTILE_PAYLOADS = [
    pytest.param({"errors": [{"message": "Model not accessible"}], "data": None}, id="graphql-error-envelope"),
    pytest.param({"data": None}, id="data-null-no-errors-key"),
    pytest.param({"errors": [], "data": None}, id="empty-errors-list"),
    pytest.param({"errors": "not-a-list", "data": None}, id="errors-not-a-list"),
    pytest.param({"data": {"print": {"name": "T", "stls": None, "gcodes": None}}}, id="null-file-lists"),
    pytest.param({"data": {"print": {"name": "T", "stls": "nope", "gcodes": 7}}}, id="file-lists-wrong-type"),
    pytest.param({"data": {"print": {"stls": [None, 42, "x"]}}}, id="file-entries-wrong-type"),
    pytest.param({"data": {"print": {"stls": [{"name": None, "fileSize": None, "id": None}]}}}, id="null-file-fields"),
    pytest.param({"data": {"print": []}}, id="print-is-a-list"),
    pytest.param({"data": []}, id="data-is-a-list"),
    pytest.param([], id="top-level-list"),
    pytest.param("just a string", id="top-level-string"),
    pytest.param(None, id="top-level-null"),
    pytest.param({}, id="empty-object"),
    pytest.param(b"", id="empty-body"),
    pytest.param(b"\x00\x01\x02 not json", id="binary-garbage"),
]

@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
@patch("bambu_cli.logging_utils._BACKEND")
def test_malformed_api_response_never_raises(_log, payload):
    adapter, _ = _adapter(payload, payload)
    result = adapter.resolve(MODEL_URL)  # must not raise
    assert result.ok is False
    assert result.reason
    assert result.error
    assert result.as_tuple() == (None, None)

# Factories, not instances: pytest derives parameter ids from the values at
# collection time, and building an HTTPError up here made it probe attributes
# that blow up on Python 3.9 (KeyError: 'file' via tempfile.__getattr__).
# Explicit ids keep the report readable without pytest inspecting the objects.
@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: ValueError("bad value"), id="ValueError"),
        pytest.param(lambda: KeyError("renamed_field"), id="KeyError-renamed-field"),
        pytest.param(lambda: AttributeError("'NoneType' object has no attribute 'get'"), id="AttributeError"),
        pytest.param(lambda: TypeError("unhashable"), id="TypeError"),
        pytest.param(lambda: RuntimeError("something odd"), id="RuntimeError"),
        pytest.param(lambda: MemoryError("body too large"), id="MemoryError"),
        pytest.param(
            lambda: urllib.error.HTTPError("https://api.printables.com/graphql/", 500, "Server Error", {}, None),
            id="HTTPError-500",
        ),
    ],
)
@patch("bambu_cli.logging_utils._BACKEND")
def test_unexpected_exception_is_contained_not_propagated(_log, make_error):
    adapter, _ = _adapter(make_error())
    result = adapter.resolve(MODEL_URL)  # must not raise
    assert result.ok is False
    assert result.error

@patch("bambu_cli.logging_utils._BACKEND")
def test_keyboard_interrupt_is_never_swallowed(_log):
    # Containment must not break Ctrl-C.
    adapter, _ = _adapter(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        adapter.resolve(MODEL_URL)

# --- legacy tuple surface ----------------------------------------------------

@patch("bambu_cli.logging_utils._BACKEND")
def test_resolve_printables_url_keeps_the_tuple_contract(_log):
    adapter, _ = _adapter(
        _model(stls=[{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}]),
        _link("https://download.example.com/part1.stl"),
    )
    assert resolve_printables_url(MODEL_URL, adapter=adapter) == (
        "https://download.example.com/part1.stl",
        "part1.stl",
    )

@patch("bambu_cli.logging_utils._BACKEND")
def test_resolve_printables_url_returns_none_pair_on_failure(_log):
    adapter, _ = _adapter({"data": {"print": None}})
    assert resolve_printables_url(MODEL_URL, adapter=adapter) == (None, None)

@patch("bambu_cli.logging_utils._BACKEND")
def test_resolve_printables_exposes_the_reason_the_tuple_hides(_log):
    # Same failure, two surfaces: the tuple can only say "no", the resolution
    # says *why*. One adapter each — the fake opener queues one call per resolve.
    tuple_adapter, _ = _adapter(b"<html/>")
    rich_adapter, _ = _adapter(b"<html/>")

    assert resolve_printables_url(MODEL_URL, adapter=tuple_adapter) == (None, None)
    assert resolve_printables(MODEL_URL, adapter=rich_adapter).reason == "printables_contract_changed"
