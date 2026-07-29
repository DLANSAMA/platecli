"""Unit tests for the shared TLS pin verifier (roadmap B.5).

``bambu_cli.tlspin.verify_cert_fingerprint`` is the single source of truth that
MQTT, FTPS, and the direct camera grab all call. These tests exercise it
directly; the per-transport suites (``test_tls_pinning.py``, ``test_camera_cmd``)
prove each call site still fails closed through its own path.
"""

from __future__ import annotations

import hashlib
import ssl

import pytest

from bambu_cli.tlspin import normalize_fingerprint, verify_cert_fingerprint

pytestmark = pytest.mark.security

_DER = b"\x30\x82fake-der-bytes-for-pin-tests"
_FP = hashlib.sha256(_DER).hexdigest()  # 64 lowercase hex chars


class _DomainError(Exception):
    """Stand-in for a BambuError subclass passed as exc_factory."""


# --- match ---


def test_match_returns_normalized_actual():
    assert verify_cert_fingerprint(_DER, _FP) == _FP


def test_match_uppercase_pin():
    assert verify_cert_fingerprint(_DER, _FP.upper()) == _FP


def test_match_colon_separated_pin():
    colonized = ":".join(_FP[i : i + 2] for i in range(0, len(_FP), 2))
    assert verify_cert_fingerprint(_DER, colonized) == _FP


def test_match_space_separated_pin():
    spaced = " ".join(_FP[i : i + 2] for i in range(0, len(_FP), 2))
    assert verify_cert_fingerprint(_DER, spaced) == _FP


def test_match_mixed_case_and_colons():
    mixed = ":".join(_FP[i : i + 2] for i in range(0, len(_FP), 2)).upper()
    assert verify_cert_fingerprint(_DER, mixed) == _FP


# --- mismatch (fail closed) ---


def test_mismatch_raises_sslerror_by_default():
    with pytest.raises(ssl.SSLError, match="fingerprint mismatch"):
        verify_cert_fingerprint(_DER, "ab" * 32)


def test_mismatch_uses_exc_factory():
    with pytest.raises(_DomainError, match="fingerprint mismatch"):
        verify_cert_fingerprint(_DER, "ab" * 32, exc_factory=_DomainError)


def test_almost_right_pin_still_fails():
    # Flip the last hex digit: must not verify.
    near = _FP[:-1] + ("0" if _FP[-1] != "0" else "1")
    with pytest.raises(ssl.SSLError):
        verify_cert_fingerprint(_DER, near)


# --- missing pin (fail closed) ---


@pytest.mark.parametrize("pin", [None, "", ":", "   "])
def test_no_pin_configured_fails_closed(pin):
    with pytest.raises(ssl.SSLError, match="No certificate fingerprint pinned"):
        verify_cert_fingerprint(_DER, pin)


def test_no_pin_uses_exc_factory():
    with pytest.raises(_DomainError):
        verify_cert_fingerprint(_DER, None, exc_factory=_DomainError)


# --- unobtainable peer cert (fail closed) ---


@pytest.mark.parametrize("der", [None, b""])
def test_no_peer_cert_fails_closed(der):
    with pytest.raises(ssl.SSLError, match="No peer certificate"):
        verify_cert_fingerprint(der, _FP)


def test_no_peer_cert_uses_exc_factory():
    with pytest.raises(_DomainError, match="No peer certificate"):
        verify_cert_fingerprint(None, _FP, exc_factory=_DomainError)


# --- normalize helper ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("AABB", "aabb"),
        ("aa:bb:cc", "aabbcc"),
        ("AA BB CC", "aabbcc"),
        ("Aa:Bb Cc", "aabbcc"),
    ],
)
def test_normalize_fingerprint(raw, expected):
    assert normalize_fingerprint(raw) == expected
