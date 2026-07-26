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
    assert N._safe_remote_name(".") is None
    assert N._safe_remote_name(" model.3mf") is None  # leading space
    assert N._safe_remote_name("model.3mf ") is None
    assert N._safe_remote_name("CON.3mf") is None
    assert N._safe_remote_name("a" * 200 + ".3mf") is None


def test_sanitize_download_filename_reserved_and_controls():
    assert "\n" not in N._sanitize_download_filename("x\ny.stl")
    name = N._sanitize_download_filename("CON.stl")
    assert name.upper().startswith("_") or name != "CON.stl"


# Names that broke one of the two functions, or plausibly could. Kept as one corpus
# so the round-trip property below covers every case the individual tests assert.
_HOSTILE_NAMES = [
    # Windows device names before the FIRST dot. splitext() only strips the last
    # extension, so every <device>.gcode.3mf -- the project's print-ready format --
    # slipped through both functions.
    "aux.gcode.3mf",
    "CON.gcode.3mf",
    "com1.gcode.3mf",
    "lpt9.3mf",
    "nul.tar.gz",
    "aux",
    "AUX.",
    "aux.GCODE.3MF",
    "prn.stl",
    # A pathological "extension" consumed the whole budget, leaving the result over
    # the cap entirely untruncated.
    "x." + "a" * 200,
    # Truncation used to run last and could leave a trailing space, or shorten a
    # stem back into a reserved device name.
    "a" * 159 + " " + "b" * 50,
    "a" * 158 + "." + "b" * 50,
    "AUX" + "Z" * 10 + "." + "c" * 156,
    # Length was capped in characters; 160 CJK chars is 480 UTF-8 bytes and exceeds
    # ext4's 255-byte limit (ENAMETOOLONG on write). The bare form is the one that
    # discriminates: at exactly 160 characters a char-based cap sees nothing wrong.
    "日" * 160,
    "日" * 160 + ".3mf",
    "🖨" * 100 + ".stl",
    # Traversal, separators, control characters, CRLF command smuggling.
    "../../etc/passwd",
    "..%2f..%2fx.stl",
    "%2e%2e%2fetc%2fpasswd.stl",
    "%252e%252e%252f.stl",
    "%00.stl",
    "evil%0d%0aSTOR x.stl",
    "model\r\n.stl",
    "model\x00.stl",
    "C:\\Windows\\model.stl",
    "/abs/path/model.stl",
    # Degenerate.
    ".",
    "..",
    "",
    "   ",
    "...",
    # Legal names that must survive intact.
    "USB-C Cover.stl",
    "part #3.stl",
    "50%off.stl",
    "Ünïcodé Mödel.stl",
]


@pytest.mark.parametrize("raw", _HOSTILE_NAMES)
def test_sanitized_names_are_always_accepted_by_the_remote_check(raw):
    """The repairer must never emit a name the printer-side check refuses.

    This is the load-bearing invariant: `_sanitize_download_filename` runs on the
    download path and `_safe_remote_name` guards upload, so any disagreement means
    a file that downloads cannot then be uploaded -- `job` would fail after a
    successful download. Three such disagreements existed (oversized extension,
    trailing space after truncation, truncation re-creating a reserved stem).
    """
    fixed = N._sanitize_download_filename(raw)
    assert N._safe_remote_name(fixed) is not None, f"{raw!r} repaired to {fixed!r}, which _safe_remote_name rejects"


@pytest.mark.parametrize("raw", _HOSTILE_NAMES)
def test_sanitize_is_idempotent(raw):
    """Re-sanitizing must be a no-op, or the same model downloaded twice could land
    under two different names."""
    once = N._sanitize_download_filename(raw)
    assert N._sanitize_download_filename(once) == once


@pytest.mark.parametrize("raw", _HOSTILE_NAMES)
def test_sanitized_names_carry_no_dangerous_characters(raw):
    """Separators and the FTP command delimiters must never survive repair."""
    fixed = N._sanitize_download_filename(raw)
    for char in ("/", "\\", "\r", "\n", "\0"):
        assert char not in fixed, f"{raw!r} repaired to {fixed!r}, which still contains {char!r}"
    assert fixed == fixed.strip(" ."), f"{fixed!r} has a leading/trailing space or dot"
    assert fixed not in (".", "..", "")


def test_reserved_device_names_are_caught_before_the_first_dot():
    """Regression: `aux.gcode.3mf` passed both functions because splitext() left the
    stem as `aux.gcode`. Windows reserves the segment before the first dot."""
    assert N._reserved_device_stem("aux.gcode.3mf") is True
    assert N._reserved_device_stem("AUX.stl") is True
    assert N._reserved_device_stem("com1.gcode.3mf") is True
    assert N._reserved_device_stem("auxiliary.stl") is False
    assert N._reserved_device_stem("my-aux.stl") is False
    # Both functions must agree, or the round-trip breaks.
    assert N._sanitize_download_filename("aux.gcode.3mf") == "_aux.gcode.3mf"
    assert N._safe_remote_name("aux.gcode.3mf") is None


# Inputs that correctly yield "model.stl": the degenerate ones, plus two whose real
# basename simply *is* model.stl. Anything else must keep something of the original.
_EXPECTED_FALLBACKS = {".", "..", "", "   ", "...", "C:\\Windows\\model.stl", "/abs/path/model.stl"}


@pytest.mark.parametrize("raw", [n for n in _HOSTILE_NAMES if n not in _EXPECTED_FALLBACKS])
def test_repair_never_degrades_a_usable_name(raw):
    """`_sanitize_download_filename` ends with a validate-or-fall-back-to-model.stl
    guard. That guarantees safety but would silently discard the user's filename if
    the repair steps above it regressed, and the round-trip test alone cannot see
    that (model.stl is perfectly safe). So assert the fallback is never reached for
    an input that can be repaired.
    """
    assert N._sanitize_download_filename(raw) != "model.stl"


def test_name_budget_is_bytes_not_characters():
    """160 CJK characters is 480 UTF-8 bytes, which ext4 refuses (ENAMETOOLONG).

    Uses the bare 160-character form deliberately: with a trailing ``.3mf`` the name
    is 164 characters and a character-based cap would truncate it too, so that input
    cannot tell the two rules apart.
    """
    from bambu_cli.constants import MAX_DOWNLOAD_FILENAME_LENGTH as MAX

    raw = "日" * 160
    assert len(raw) <= MAX, "input must be within the CHARACTER cap for this test to discriminate"
    assert len(raw.encode("utf-8")) > MAX, "input must exceed the BYTE cap"

    fixed = N._sanitize_download_filename(raw)
    assert len(fixed.encode("utf-8")) <= MAX
    # Truncation must not split a codepoint into mojibake.
    assert fixed == fixed.encode("utf-8").decode("utf-8")
    # The rejecter has to use the same rule, or the round-trip breaks.
    assert N._safe_remote_name(raw) is None


def test_ordinary_names_are_left_alone():
    """Repair must not churn names that were already fine -- users would see files
    renamed for no reason."""
    for name in ("USB-C Cover.stl", "part #3.stl", "50%off.stl", "benchy.gcode.3mf"):
        assert N._sanitize_download_filename(name) == name


def test_content_disposition_percent_is_not_double_decoded():
    """A literal `%20` in a plain `filename=` param is not an escape sequence, so it
    must survive. Adding unquote() to the shared sanitizer would have decoded it."""
    got = N._filename_from_content_disposition('attachment; filename="save%20file.stl"')
    assert got == "save%20file.stl"


def test_content_disposition_rfc5987_still_decodes():
    """The RFC 5987 `filename*` path does its own decoding and must keep working."""
    got = N._filename_from_content_disposition("attachment; filename*=UTF-8''%E6%97%A5%E6%9C%AC.3mf")
    assert got == "日本.3mf"


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
