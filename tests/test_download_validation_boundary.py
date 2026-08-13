"""Boundary tests for the download rejection path.

``download/validation.py`` is in the mutation scope (`[tool.mutmut].only_mutate`),
but half its surface — the ``_reject_*`` functions that actually stop a bad
download — had no direct tests. They were only reached incidentally through
whole-command tests, which is why mutants inside them survived: nothing asserted
what they *do*.

These tests drive each rejection through its real inputs and assert only what a
caller can observe:

* whether it aborts at all (some inputs are deliberately ambiguous and must pass)
* the exit code
* the ``failed_step`` and the machine-readable fields in the JSON envelope
* that credentials in the URL are redacted before they reach that envelope

Deliberately **not** asserted: the prose of the error message. Pinning message
text couples tests to copy-editing and is the kind of brittleness that makes a
suite expensive without making it safer — mutants that only change wording are
recorded as equivalent in docs/mutation-baseline.md rather than chased.

Ground rules (docs/test-backlog.md): no network, no printer.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from bambu_cli import utils  # noqa: E402
from bambu_cli.constants import EXIT_FILE_ERROR  # noqa: E402
from bambu_cli.download import validation as V  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402

URL = "https://example.com/model.rar"

@pytest.fixture(autouse=True)
def _reset_json_state():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None

def _args(**kw):
    return Namespace(json=True, **kw)

def _payload(capsys):
    return json.loads(capsys.readouterr().out)

# --- unsupported source extension -------------------------------------------

@pytest.mark.parametrize("value", ["archive.rar", "notes.pdf", "/path/to/render.png", "a.tar", "b.7z"])
def test_clearly_unsupported_extension_is_named(value):
    """The extension is reported so a caller can say *which* type was refused."""
    assert V._known_unsupported_download_extension(value) is not None

@pytest.mark.parametrize(
    "value",
    [
        "model.stl",  # supported
        "model.3mf",  # supported
        "",  # nothing to judge
        None,
        "https://example.com/download?id=123",  # extensionless: ambiguous, must not guess
        "model.unknownext",  # unknown but not on the refuse-list
        # Deliberately absent from the refuse-list: it names archive/document/
        # image types, not a security allowlist. Print-readiness is enforced
        # downstream by _reject_non_print_ready.
        "installer.exe",
    ],
)
def test_ambiguous_or_supported_extensions_are_not_rejected(value):
    """Guessing wrong here would refuse a legitimate download."""
    assert V._known_unsupported_download_extension(value) is None

def test_extension_is_read_after_percent_decoding():
    # A percent-encoded name must not smuggle a refused type past the check.
    assert V._known_unsupported_download_extension("https://example.com/notes%2Epdf") is not None

def test_reject_unsupported_extension_aborts_with_file_error(capsys):
    with pytest.raises(BambuError) as excinfo:
        V._reject_unsupported_download_extension(_args(), URL, None, URL, "archive.rar")
    assert getattr(excinfo.value, "exit_code", None) == EXIT_FILE_ERROR

    payload = _payload(capsys)
    assert payload["status"] == "error"
    assert payload["command"] == "download"
    assert payload["failed_step"] == "validate"
    assert payload["extension"] == ".rar"

def test_reject_unsupported_extension_honours_the_caller_step(capsys):
    # The same refusal happens mid-download after a redirect; the step must say so.
    with pytest.raises(BambuError):
        V._reject_unsupported_download_extension(_args(), URL, None, URL, "archive.rar", failed_step="download")
    assert _payload(capsys)["failed_step"] == "download"

def test_reject_unsupported_extension_is_a_no_op_for_supported_types(capsys):
    V._reject_unsupported_download_extension(_args(), URL, None, URL, "model.stl")
    assert capsys.readouterr().out == ""

def test_rejection_redacts_credentials_in_the_url(capsys):
    # Username-only + IP host: exercises the userinfo-stripping path without
    # writing a literal `user:pass@host` or email into the repo, which
    # tests/privacy_smoke.py rejects. Same convention as the sibling tests in
    # test_job.py and test_mqtt_print_and_setup.py.
    creds = "http://user@127.0.0.1/archive.rar"
    with pytest.raises(BambuError):
        V._reject_unsupported_download_extension(_args(), creds, None, creds, "archive.rar")
    emitted = capsys.readouterr().out
    assert "user@" not in emitted, "userinfo leaked into the error envelope"

# --- unsupported content type ------------------------------------------------

@pytest.mark.parametrize(
    "content_type",
    ["image/png", "image/jpeg", "IMAGE/PNG", "image/png; charset=binary", "application/pdf", "text/plain"],
)
def test_clearly_unsupported_content_types_are_named(content_type):
    assert V._known_unsupported_content_type(content_type) is not None

@pytest.mark.parametrize(
    "content_type",
    [
        "",
        None,
        "application/octet-stream",
        "model/stl",
        # text/html must pass: it is the HTML-scrape path, where the page is
        # parsed for a direct model-file link rather than refused.
        "text/html",
    ],
)
def test_ambiguous_content_types_are_allowed_through(content_type):
    """Most servers send octet-stream for model files; refusing it breaks downloads."""
    assert V._known_unsupported_content_type(content_type) is None

def test_content_type_parameters_are_ignored_when_matching():
    assert V._known_unsupported_content_type("image/png; charset=utf-8") == "image/png"

def test_reject_unsupported_content_type_reports_the_download_step(capsys):
    with pytest.raises(BambuError) as excinfo:
        V._reject_unsupported_content_type(_args(), URL, None, URL, "image/png")
    assert getattr(excinfo.value, "exit_code", None) == EXIT_FILE_ERROR

    payload = _payload(capsys)
    # It failed after the request went out, so this is `download`, not `validate`.
    assert payload["failed_step"] == "download"
    assert payload["content_type"] == "image/png"

def test_reject_unsupported_content_type_is_a_no_op_when_ambiguous(capsys):
    V._reject_unsupported_content_type(_args(), URL, None, URL, "application/octet-stream")
    assert capsys.readouterr().out == ""

# --- the error envelope is recorded even without --json ----------------------

def test_failure_detail_is_recorded_for_non_json_callers(capsys):
    """`job` reads the last error payload to build its own envelope.

    Without --json nothing is printed, but the detail must still be captured or
    a pipeline failure loses the reason it failed.
    """
    with pytest.raises(BambuError):
        V._reject_unsupported_download_extension(Namespace(json=False), URL, None, URL, "archive.rar")
    assert capsys.readouterr().out == ""
    assert utils._LAST_ERROR_PAYLOAD is not None
    assert utils._LAST_ERROR_PAYLOAD["failed_step"] == "validate"
    assert utils._LAST_ERROR_PAYLOAD["extension"] == ".rar"
