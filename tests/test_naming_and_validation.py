"""Pure naming + validation behavior (no network)."""

from __future__ import annotations

import sys
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.download import naming as N  # noqa: E402
from bambu_cli.download import validation as V  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402


def test_has_command_injection_chars():
    assert N._has_command_injection_chars("G28") is False
    assert N._has_command_injection_chars("G28\nM104") is True
    assert N._has_command_injection_chars("a\rb") is True
    assert N._has_command_injection_chars("a\x00b") is True
    assert N._has_command_injection_chars("") is False
    assert N._has_command_injection_chars(None) is False


def test_safe_remote_name_rejects_controls_and_paths():
    assert N._safe_remote_name("model.3mf") == "model.3mf"
    assert N._safe_remote_name("a/b.3mf") is None
    assert N._safe_remote_name("evil\n.3mf") is None
    assert N._safe_remote_name("") is None
    assert N._safe_remote_name("..") is None


def test_sanitize_download_filename_reserved_and_controls():
    assert "\n" not in N._sanitize_download_filename("x\ny.stl")
    name = N._sanitize_download_filename("CON.stl")
    assert name.upper().startswith("_") or name != "CON.stl"


def test_is_print_ready_name():
    assert N._is_print_ready_name("a.3mf") is True
    assert N._is_print_ready_name("a.gcode") is True
    assert N._is_print_ready_name("a.stl") is False


def test_looks_like_and_normalize_url():
    assert V._looks_like_url("https://example.com/x.stl") is True
    assert V._looks_like_url("/local/path.stl") is False
    assert V._normalize_url_input("example.com/x.stl").startswith("http")


def test_validate_http_url_rejects_file_scheme():
    with pytest.raises((BambuError, SystemExit)):
        V._validate_http_url_or_exit("file:///etc/passwd")


def test_max_download_mb_error_and_validate():
    args = Namespace(max_download_mb=0)
    assert V._max_download_mb_error(args)
    with pytest.raises((BambuError, SystemExit)):
        V._validate_max_download_mb_or_exit(args)


def test_ams_helpers():
    from bambu_cli import ams

    assert ams._to_int("3") == 3
    assert ams._to_int("x", 7) == 7
    assert ams._to_float("1.5") == 1.5
    assert ams._normalize_color("#AABBCCDD") == "AABBCC"
    assert ams._normalize_color(None) is None
    assert ams.parse_ams({}) is None


def test_print_ready_error_message_and_reject():
    msg = N._print_ready_error_message("model.stl", "print")
    assert "model.stl" in msg
    assert "print" in msg
    assert ".3mf" in msg or "gcode" in msg.lower()
    with pytest.raises((BambuError, SystemExit)):
        N._reject_non_print_ready("model.stl", "print")


def test_looks_like_url_requires_scheme_or_domain_shape():
    assert V._looks_like_url("not a url") is False
    assert V._is_http_url("https://example.com/a.stl") is True
    assert V._is_http_url("ftp://example.com/a.stl") is False


def test_reject_oversized_download_when_content_length_set():
    args = Namespace(max_download_mb=1, json=False)
    with pytest.raises((BambuError, SystemExit)):
        V._reject_oversized_download(
            args,
            "https://example.com/big.stl",
            None,
            "https://example.com/big.stl",
            "./big.stl",
            0,
            1024 * 1024,
            content_length=5 * 1024 * 1024,
        )
