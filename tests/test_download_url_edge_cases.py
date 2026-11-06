"""Download URL edge cases found in the 2026-11 audit.

Each test failed before its fix:

* a password holding "/", "?" or "#" was read as the port, so redaction found
  no userinfo and the secret was echoed in the JSON error envelope;
* an unbalanced IPv6 bracket raised ValueError out of urlparse and surfaced as
  "Unexpected error" with a traceback;
* a model URL served as text/plain (GitHub raw serves everything that way) was
  refused although ASCII STL, OBJ, STEP and G-code are text;
* a URL with raw Unicode in the path failed with an opaque ASCII codec error;
* Ctrl-C mid-download left the .part file and the 0-byte placeholder behind.

Literal ``user:pass@host`` strings are assembled from pieces because
tests/privacy_smoke.py rejects them in the repo.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.download import validation as V
from bambu_cli.errors import BambuError
from bambu_cli.jsonio import redact_url_credentials

AT = "@"
SECRET = "S3CRETPW"


@pytest.mark.parametrize("sep", ["/", "?", "#"])
def test_password_with_a_delimiter_is_redacted(sep):
    url = f"https://user:{SECRET}{sep}rest{AT}host.example/x.stl"
    redacted = redact_url_credentials(url)
    assert SECRET not in redacted
    assert "rest" not in redacted
    assert redacted == "https://host.example/x.stl"


@pytest.mark.parametrize("sep", ["/", "?", "#"])
def test_password_with_a_delimiter_is_refused_without_echoing_it(sep):
    url = f"https://user:{SECRET}{sep}rest{AT}host.example/x.stl"
    args = types.SimpleNamespace(json=True)
    with pytest.raises(BambuError) as excinfo:
        V._validate_download_url_or_exit(args, url, url, url, "validate", "Invalid URL")
    assert excinfo.value.exit_code == EXIT_COMMAND_ERROR
    emitted = json.dumps(excinfo.value.to_error_payload("download"))
    assert SECRET not in emitted
    assert "embedded credentials are not supported" in emitted


def test_an_at_sign_in_an_ordinary_path_is_left_alone():
    url = "https://host.example/@maker/model.stl"
    assert redact_url_credentials(url) == url


@pytest.mark.parametrize(
    "url",
    ["http://[::1/x.stl", f"https://user:pw{AT}[x/a.stl"],
)
def test_unbalanced_ipv6_bracket_is_an_invalid_url_not_a_crash(url):
    assert V._looks_like_url(url) is True
    assert V._is_http_url(url) is False
    with pytest.raises(BambuError) as excinfo:
        V._validate_http_url_or_exit(url)
    assert excinfo.value.exit_code == EXIT_COMMAND_ERROR
    assert "pw" not in redact_url_credentials(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://raw.githubusercontent.com/u/r/main/model.stl",
        "https://host.example/files/part.OBJ",
        "https://host.example/a/print.gcode?token=1",
        "https://host.example/a/b%20c.step",
    ],
)
def test_text_plain_is_accepted_when_the_url_names_a_model_file(url):
    V._reject_unsupported_content_type(types.SimpleNamespace(json=False), url, None, url, "text/plain; charset=utf-8")


@pytest.mark.parametrize("url", ["https://host.example/download?id=4", "https://host.example/readme.txt"])
def test_text_plain_is_still_refused_without_a_model_extension(url):
    with pytest.raises(BambuError) as excinfo:
        V._reject_unsupported_content_type(types.SimpleNamespace(json=False), url, None, url, "text/plain")
    assert excinfo.value.exit_code == EXIT_FILE_ERROR


def test_images_are_still_refused_even_with_a_model_extension():
    url = "https://host.example/model.stl"
    with pytest.raises(BambuError):
        V._reject_unsupported_content_type(types.SimpleNamespace(json=False), url, None, url, "image/png")


def test_non_ascii_path_and_query_are_percent_encoded():
    assert V._iri_to_uri("http://h.example/files/модель.stl?n=ф#x") == (
        "http://h.example/files/%D0%BC%D0%BE%D0%B4%D0%B5%D0%BB%D1%8C.stl?n=%D1%84#x"
    )


def test_ascii_urls_and_existing_escapes_are_untouched():
    for url in ("http://h.example/a%20b.stl?x=1&y=2#f", "https://h.example/~u/[1].stl"):
        assert V._iri_to_uri(url) == url


def test_ctrl_c_mid_download_leaves_nothing_behind(tmp_path):
    from bambu_cli import download

    url = "https://example.com/model.stl"
    reads = {"n": 0}

    def read(n):
        reads["n"] += 1
        if reads["n"] > 2:
            raise KeyboardInterrupt
        return b"x" * n

    mock_resp = MagicMock()
    mock_resp.read.side_effect = read
    mock_resp.getheader.return_value = None
    mock_resp.geturl.return_value = url
    mock_opener = MagicMock()
    mock_opener.open.return_value.__enter__.return_value = mock_resp
    args = types.SimpleNamespace(
        url=url, output=str(tmp_path), name=None, max_download_mb=10, json=False, progress=False
    )

    with pytest.raises(KeyboardInterrupt):
        download._cmd_download(args, opener_factory=lambda: mock_opener)

    assert reads["n"] > 2, "the interrupt never fired mid-transfer"
    assert list(tmp_path.iterdir()) == [], "Ctrl-C left a partial or placeholder file"


# A password that starts with digits and holds "/", "?" or "#" parses cleanly as
# host:port, so the port check above does not see it.
_SPLIT_USERINFO = [
    "https://bob:1234/" + SECRET + AT + "files.invalid/x.stl",
    "https://bob:123?" + SECRET + AT + "files.invalid/x.stl",
    "https://bob:80#" + SECRET + AT + "files.invalid/x.stl",
    "https://bob:/" + SECRET + AT + "files.invalid/x.stl",
]


@pytest.mark.parametrize("url", _SPLIT_USERINFO)
def test_digit_prefixed_password_with_a_delimiter_is_redacted(url):
    assert SECRET not in redact_url_credentials(url)


@pytest.mark.parametrize("url", _SPLIT_USERINFO)
def test_digit_prefixed_password_with_a_delimiter_is_refused(url):
    with pytest.raises(BambuError) as excinfo:
        V._validate_http_url_or_exit(url)
    assert excinfo.value.exit_code == EXIT_COMMAND_ERROR
    assert SECRET not in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "https:///bob:" + SECRET + AT + "files.invalid/x.stl",
        "https:/bob:" + SECRET + AT + "h/x",
        "https:\\\\bob:" + SECRET + AT + "h/x",
    ],
)
def test_userinfo_without_a_parsed_host_is_redacted(url):
    assert SECRET not in redact_url_credentials(url)


@pytest.mark.parametrize(
    "text",
    [
        "https://host:8443/" + AT + "scope/pkg.stl",
        "https://medium.com/" + AT + "user/model.stl",
        "https://host:8080/download?user=a" + AT + "b.com",
        "C:\\Users\\bob" + AT + "corp\\model.stl",
        "0.20mm Standard " + AT + "BBL X1C",
        "/home/u/a" + AT + "b.stl",
    ],
)
def test_ordinary_text_with_an_at_sign_is_left_alone(text):
    assert redact_url_credentials(text) == text


@pytest.mark.parametrize(
    "url", ["https://host:8443/" + AT + "scope/pkg.stl", "https://host:8080/download?user=a" + AT + "b.com"]
)
def test_urls_with_a_port_and_an_at_sign_in_the_path_still_validate(url):
    V._validate_http_url_or_exit(url)


def test_a_non_numeric_port_without_credentials_is_still_named():
    with pytest.raises(BambuError) as excinfo:
        V._validate_http_url_or_exit("https://host.example:abc/x.stl")
    assert "port is not a number" in str(excinfo.value)
