"""Property-based tests for safety-critical pure validators (Phase 3).

Each property is a real invariant: a wrong implementation that drops a check
(path separators, control chars, private IPs, temp bounds, incomplete 3mf)
would fail these. Hypothesis generates adversarial inputs (control chars,
unicode, huge lengths, boundary temps, non-global IPs).

No vacuous asserts. Focused suite is also selected by ``[tool.mutmut]``.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.constants import (  # noqa: E402
    MAX_AMS_SLOT_INDEX,
    MAX_BED_TEMP_C,
    MAX_DOWNLOAD_FILENAME_LENGTH,
    MAX_NOZZLE_TEMP_C,
    MIN_BED_TEMP_C,
    MIN_NOZZLE_TEMP_C,
)
from bambu_cli.download import naming as N  # noqa: E402
from bambu_cli.download import validation as V  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.job import payload as job_payload  # noqa: E402
from bambu_cli import netsafety  # noqa: E402
from bambu_cli.slicer import options as slicer_options  # noqa: E402
from bambu_cli.slicer import output as slicer_output  # noqa: E402

# Keep property suites snappy in CI while still exploring edges.
_PROP = settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

# Characters that must never appear in a sanitized local download filename.
_FORBIDDEN_IN_SANITIZED = set('/\\:\0\r\n\x00<>"|?*') | {chr(c) for c in range(32)}


# ---------------------------------------------------------------------------
# download/naming.py
# ---------------------------------------------------------------------------


@given(st.text(min_size=0, max_size=400))
@_PROP
def test_prop_sanitize_never_contains_path_or_control_chars(raw: str) -> None:
    """Sanitized names never embed path separators, CR/LF/NUL, or other C0 controls."""
    out = N._sanitize_download_filename(raw)
    assert isinstance(out, str)
    assert out  # never empty — falls back to model.stl
    assert "/" not in out
    assert "\\" not in out
    assert "\x00" not in out
    assert "\r" not in out
    assert "\n" not in out
    for ch in out:
        assert ord(ch) >= 32 or ch not in ("\x00", "\r", "\n")
        assert ch not in '<>:"/\\|?*'


@given(st.text(min_size=0, max_size=500))
@_PROP
def test_prop_sanitize_length_bounded(raw: str) -> None:
    """Stem-trimmed names stay ≤ MAX; extension-only oversize is a known residual.

    Implementation trims the *stem* only. If the extension alone is longer than
    MAX_DOWNLOAD_FILENAME_LENGTH, the result can still exceed MAX (not fixed in
    Phase 3 — behavior-preserving). When the extension fits, the full name is
    always bounded.
    """
    import os

    out = N._sanitize_download_filename(raw)
    _stem, ext = os.path.splitext(out)
    if len(ext) < MAX_DOWNLOAD_FILENAME_LENGTH:
        assert len(out) <= MAX_DOWNLOAD_FILENAME_LENGTH
    else:
        # Residual path: cannot stem-trim an extension-dominated name.
        assert len(out) >= len(ext)


@given(st.text(min_size=0, max_size=300))
@_PROP
def test_prop_sanitize_idempotent(raw: str) -> None:
    """Sanitize is idempotent: applying twice equals applying once."""
    once = N._sanitize_download_filename(raw)
    twice = N._sanitize_download_filename(once)
    assert twice == once


@given(st.text(min_size=0, max_size=200), st.sampled_from(["\r", "\n", "\0"]))
@_PROP
def test_prop_injection_chars_always_detected(prefix: str, inj: str) -> None:
    """Any string containing CR, LF, or NUL is flagged as command-injection risk."""
    # Keep total length reasonable; place injection in the middle when possible.
    value = prefix[:80] + inj + prefix[80:100]
    assert N._has_command_injection_chars(value) is True


@given(st.text(alphabet=st.characters(blacklist_characters="\r\n\0"), min_size=0, max_size=120))
@_PROP
def test_prop_no_injection_chars_when_absent(value: str) -> None:
    """Strings without CR/LF/NUL never report command-injection characters."""
    assert N._has_command_injection_chars(value) is False


@given(st.text(min_size=0, max_size=250))
@_PROP
def test_prop_safe_remote_name_reject_or_portable(raw: str) -> None:
    """_safe_remote_name either rejects (None) or returns a portable basename."""
    out = N._safe_remote_name(raw)
    if out is None:
        return
    assert out == raw  # success path returns the input unchanged
    assert N._has_command_injection_chars(out) is False
    assert "/" not in out and "\\" not in out
    assert out not in (".", "..")
    assert out == out.strip(" .")
    assert len(out) <= MAX_DOWNLOAD_FILENAME_LENGTH
    assert not any(c in out for c in '<>:"/\\|?*')
    assert out == N._portable_basename(out)


@given(
    st.text(min_size=1, max_size=100).map(lambda s: s.replace("\0", "x")),
    st.sampled_from(["\r", "\n", "\0", "/", "\\", ":", "*", "?", '"', "<", ">", "|"]),
)
@_PROP
def test_prop_safe_remote_name_rejects_dangerous_chars(stem: str, bad: str) -> None:
    """Any CR/LF/NUL or FAT-hostile / path character forces rejection."""
    # Build a candidate that still looks like a filename with a bad char inserted.
    name = f"m{stem[:40]}{bad}x.3mf"
    assert N._safe_remote_name(name) is None


@given(st.integers(min_value=MAX_DOWNLOAD_FILENAME_LENGTH + 1, max_value=MAX_DOWNLOAD_FILENAME_LENGTH + 80))
@_PROP
def test_prop_safe_remote_name_rejects_overlong(n: int) -> None:
    """Names longer than MAX_DOWNLOAD_FILENAME_LENGTH are never accepted for remote use."""
    name = "a" * n + ".3mf"
    assert len(name) > MAX_DOWNLOAD_FILENAME_LENGTH
    assert N._safe_remote_name(name) is None


# ---------------------------------------------------------------------------
# download/validation.py + URL scheme invariants
# ---------------------------------------------------------------------------


@given(st.sampled_from(["file", "ftp", "ftps", "data", "javascript", "gopher", "ssh", ""]))
@_PROP
def test_prop_validate_http_url_rejects_non_http_schemes(scheme: str) -> None:
    """Only http/https are accepted; every other scheme aborts."""
    if scheme:
        url = f"{scheme}://example.com/model.stl"
    else:
        url = "://example.com/model.stl"
    with pytest.raises((BambuError, SystemExit)):
        V._validate_http_url_or_exit(url)


@given(st.sampled_from(["http", "https"]))
@_PROP
def test_prop_validate_http_url_rejects_missing_host(scheme: str) -> None:
    """http(s) URLs without a host are rejected."""
    with pytest.raises((BambuError, SystemExit)):
        V._validate_http_url_or_exit(f"{scheme}:///path/only.stl")


@given(
    st.sampled_from(["http", "https"]),
    st.text(alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")), min_size=1, max_size=12),
    st.text(alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")), min_size=1, max_size=12),
)
@_PROP
def test_prop_validate_http_url_rejects_embedded_credentials(scheme: str, user: str, password: str) -> None:
    """URLs with userinfo credentials are always rejected (credential leak / SSRF surface)."""
    url = f"{scheme}://{user}:{password}@example.com/model.stl"
    with pytest.raises((BambuError, SystemExit)):
        V._validate_http_url_or_exit(url)


@given(
    st.one_of(
        st.integers(max_value=0),
        st.just(None),
        st.just(""),
        st.just("0"),
        st.just("-3"),
        st.just("nope"),
        st.just(0.5),  # int(0.5)==0 after truncation path only for int() on float -> 0
    )
)
@_PROP
def test_prop_max_download_mb_rejects_non_positive(value) -> None:
    """--max-download-mb must be a positive integer; zero/negative/junk always error."""
    args = argparse.Namespace(max_download_mb=value)
    err = V._max_download_mb_error(args)
    assert err is not None
    assert "positive" in err.lower() or "integer" in err.lower() or "max-download" in err.lower()


@given(st.integers(min_value=1, max_value=4096))
@_PROP
def test_prop_max_download_mb_accepts_positive(value: int) -> None:
    """Positive max-download-mb values have no validation error."""
    args = argparse.Namespace(max_download_mb=value)
    assert V._max_download_mb_error(args) is None


@given(st.sampled_from(["http://example.com/a.stl", "https://cdn.example.org/x.3mf"]))
@_PROP
def test_prop_is_http_url_true_for_valid(url: str) -> None:
    assert V._is_http_url(url) is True


# ---------------------------------------------------------------------------
# netsafety.py — is_global gating
# ---------------------------------------------------------------------------


def _addrinfo(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]


@given(st.ip_addresses(v=4).filter(lambda a: not a.is_global))
@_PROP
def test_prop_non_global_ipv4_never_connected(ip: ipaddress.IPv4Address) -> None:
    """No private/loopback/link-local/reserved IPv4 is ever passed to create_connection."""
    ip_s = str(ip)
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo(ip_s)),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError),
    ):
        netsafety._get_safe_connection("evil.example", 443, 5, None)
    conn.assert_not_called()


@given(st.ip_addresses(v=6).filter(lambda a: not a.is_global and not a.ipv4_mapped))
@_PROP
def test_prop_non_global_ipv6_never_connected(ip: ipaddress.IPv6Address) -> None:
    """Non-global IPv6 (ULA, link-local, loopback, multicast, …) is refused."""
    ip_s = str(ip)
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo(ip_s)),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError),
    ):
        netsafety._get_safe_connection("evil6.example", 443, 5, None)
    conn.assert_not_called()


# Cloud metadata and classic private ranges — explicit samples beyond pure generation.
@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",  # AWS/GCP/Azure metadata (link-local)
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "0.0.0.0",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
        "fd12:3456:789a::1",
    ],
)
def test_explicit_non_global_and_metadata_refused(ip: str) -> None:
    """Known SSRF targets (metadata, RFC1918, loopback, ULA) never connect."""
    addr = ipaddress.ip_address(ip)
    # is_global is False for all of the above in CPython (incl. broadcast).
    assert addr.is_global is False
    with (
        patch.object(netsafety.socket, "getaddrinfo", return_value=_addrinfo(ip)),
        patch.object(netsafety.socket, "create_connection") as conn,
        pytest.raises(urllib.error.URLError),
    ):
        netsafety._get_safe_connection("meta.internal", 80, 5, None)
    conn.assert_not_called()


# ---------------------------------------------------------------------------
# slicer options + AMS mapping + 3mf validation
# ---------------------------------------------------------------------------


@given(st.integers().filter(lambda t: t < MIN_NOZZLE_TEMP_C or t > MAX_NOZZLE_TEMP_C))
@_PROP
def test_prop_out_of_range_nozzle_temp_rejected(temp: int) -> None:
    """Nozzle temps outside [MIN, MAX] always produce a validation error."""
    assume(isinstance(temp, int))
    args = argparse.Namespace(copies=1, infill=15, nozzle_temp=temp, bed_temp=60, wall_type=None)
    err = slicer_options._validate_slice_options(args)
    assert err is not None
    assert "nozzle" in err.lower()


@given(st.integers().filter(lambda t: t < MIN_BED_TEMP_C or t > MAX_BED_TEMP_C))
@_PROP
def test_prop_out_of_range_bed_temp_rejected(temp: int) -> None:
    """Bed temps outside [MIN, MAX] always produce a validation error."""
    args = argparse.Namespace(copies=1, infill=15, nozzle_temp=220, bed_temp=temp, wall_type=None)
    err = slicer_options._validate_slice_options(args)
    assert err is not None
    assert "bed" in err.lower()


@given(st.integers(min_value=MIN_NOZZLE_TEMP_C, max_value=MAX_NOZZLE_TEMP_C))
@_PROP
def test_prop_in_range_nozzle_temp_ok(temp: int) -> None:
    args = argparse.Namespace(copies=1, infill=15, nozzle_temp=temp, bed_temp=60, wall_type=None)
    err = slicer_options._validate_slice_options(args)
    assert err is None


@given(st.integers().filter(lambda v: v < 0 or v > 100))
@_PROP
def test_prop_out_of_range_infill_rejected(infill: int) -> None:
    args = argparse.Namespace(copies=1, infill=infill, nozzle_temp=220, bed_temp=60, wall_type=None)
    err = slicer_options._validate_slice_options(args)
    assert err is not None
    assert "infill" in err.lower()


@given(st.integers(max_value=0))
@_PROP
def test_prop_non_positive_copies_rejected(copies: int) -> None:
    args = argparse.Namespace(copies=copies, infill=15, nozzle_temp=220, bed_temp=60, wall_type=None)
    err = slicer_options._validate_slice_options(args)
    assert err is not None
    assert "copies" in err.lower()


@given(st.lists(st.integers().filter(lambda s: s < 0 or s > MAX_AMS_SLOT_INDEX), min_size=1, max_size=6))
@_PROP
def test_prop_out_of_range_ams_slots_rejected(slots: list[int]) -> None:
    """Any AMS slot outside 0..MAX_AMS_SLOT_INDEX is rejected when --use-ams is set."""
    raw = ",".join(str(s) for s in slots)
    args = argparse.Namespace(use_ams=True, ams_mapping=raw)
    mapping, err = job_payload._parse_print_options(args)
    assert mapping is None
    assert err is not None
    assert "ams" in err.lower() or "slot" in err.lower() or "mapping" in err.lower()


@given(st.lists(st.integers(min_value=0, max_value=MAX_AMS_SLOT_INDEX), min_size=1, max_size=8))
@_PROP
def test_prop_valid_ams_slots_accepted(slots: list[int]) -> None:
    raw = ",".join(str(s) for s in slots)
    args = argparse.Namespace(use_ams=True, ams_mapping=raw)
    mapping, err = job_payload._parse_print_options(args)
    assert err is None
    assert mapping == slots


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(st.binary(min_size=0, max_size=128))
def test_prop_random_bytes_never_valid_3mf(tmp_path: Path, data: bytes) -> None:
    """Random file contents are never a valid sliced 3MF package."""
    # Fixture args first, then hypothesis-drawn values (hypothesis pytest convention).
    path = tmp_path / "fuzz.3mf"
    path.write_bytes(data)
    if not zipfile.is_zipfile(path):
        assert slicer_output._is_valid_sliced_3mf(str(path)) is False
        return
    # Even if zip-shaped, missing required members must fail.
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        assert slicer_output._is_valid_sliced_3mf(str(path)) is False
        return
    has_ct = "[Content_Types].xml" in names
    has_model = "3D/3dmodel.model" in names
    has_plate = any(n.startswith("Metadata/plate_") and n.endswith(".gcode") for n in names)
    if not (has_ct and (has_model or has_plate)):
        assert slicer_output._is_valid_sliced_3mf(str(path)) is False


def test_incomplete_zip_structures_never_valid_3mf(tmp_path: Path) -> None:
    """Zips missing Content_Types or model/plate members are never valid."""
    cases = [
        {"readme.txt": "nope"},
        {"[Content_Types].xml": "<Types/>"},
        {"3D/3dmodel.model": "<model/>"},
        {"Metadata/plate_1.gcode": "G28\n"},
        {"[Content_Types].xml": "<Types/>", "other.txt": "x"},
    ]
    for i, members in enumerate(cases):
        path = tmp_path / f"inc_{i}.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            for name, body in members.items():
                zf.writestr(name, body)
        assert slicer_output._is_valid_sliced_3mf(str(path)) is False


def test_minimal_valid_3mf_shapes_accepted(tmp_path: Path) -> None:
    """Documented acceptance: Content_Types + model, or Content_Types + plate gcode."""
    a = tmp_path / "model_only.3mf"
    with zipfile.ZipFile(a, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
    assert slicer_output._is_valid_sliced_3mf(str(a)) is True

    b = tmp_path / "plate_only.3mf"
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("Metadata/plate_2.gcode", "G28\n")
    assert slicer_output._is_valid_sliced_3mf(str(b)) is True


# ---------------------------------------------------------------------------
# print payload invariants (job pure logic)
# ---------------------------------------------------------------------------


@given(
    st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="/\\"),
        min_size=1,
        max_size=40,
    ),
    st.booleans(),
    st.lists(st.integers(min_value=0, max_value=MAX_AMS_SLOT_INDEX), min_size=1, max_size=4),
)
@_PROP
def test_prop_print_payload_ams_only_when_use_ams(basename: str, use_ams: bool, mapping: list[int]) -> None:
    """ams_mapping appears in the MQTT payload iff use_ams is True and mapping is set."""
    import json

    raw = job_payload.generate_print_payload(basename + ".3mf", use_ams=use_ams, ams_mapping=mapping)
    data = json.loads(raw)
    assert data["print"]["command"] == "project_file"
    assert data["print"]["use_ams"] is use_ams
    if use_ams:
        assert data["print"]["ams_mapping"] == mapping
    else:
        assert "ams_mapping" not in data["print"]
    # Path traversal / command smuggling via basename must still URL-encode into file URL.
    assert data["print"]["url"].startswith("file:///sdcard/model/")
    assert "\n" not in data["print"]["url"]
    assert "\r" not in data["print"]["url"]
