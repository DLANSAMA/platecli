"""Single source of truth for TLS certificate-fingerprint pin verification.

MQTT, FTPS (control + data channels), and the direct camera grab all pin the
printer's self-signed certificate by its SHA-256 fingerprint. Rolling three
hand-written copies of security-critical compare logic is where a fail-open bug
hides, so every call site funnels through :func:`verify_cert_fingerprint` here.

Design guarantees (all fail *closed* — a failure never verifies as OK):

* No pin configured where a pin is required, a mismatched pin, or an
  unobtainable peer cert all raise; none of them return normally.
* The compare is constant-time (``hmac.compare_digest``) on normalized hex.
* Inputs are normalized by :func:`normalize_fingerprint`: lowercased with ``:``
  and ASCII spaces stripped. Accepted pin formats therefore include
  ``AA:BB:CC...`` (colon-separated, any case), ``aa bb cc ...`` (space-separated),
  and the bare 64-hex-char digest.

Domain callers that need a ``BambuError`` pass an ``exc_factory`` so the raised
type matches their own error contract (the camera path relies on a distinct
exception to steer its fallback policy); the default raised type is
``ssl.SSLError``, matching the historical MQTT/FTPS behaviour.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import ssl
from collections.abc import Callable

# A normalized SHA-256 fingerprint: exactly 64 lowercase hex chars, no separators.
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def normalize_fingerprint(fp: str | None) -> str | None:
    """Normalize a SHA-256 fingerprint to lowercase, separator-free hex.

    Strips ``:`` and ASCII spaces and lowercases, so ``AA:BB``, ``aa bb`` and
    ``aabb`` compare equal. Returns ``None`` for ``None``/empty input.
    """
    if not fp:
        return None
    return fp.lower().replace(":", "").replace(" ", "")


def verify_cert_fingerprint(
    peer_der: bytes | None,
    expected_fingerprint: str | None,
    *,
    exc_factory: Callable[[str], BaseException] = ssl.SSLError,
) -> str:
    """Verify a peer certificate's SHA-256 against a pinned fingerprint.

    Fails closed: raises (never returns) when no pin is configured, when the pin
    is malformed (not 64 hex chars after normalization), when the peer
    certificate is unavailable, or when the fingerprints differ. On a match
    returns the normalized actual fingerprint (useful for callers that also want
    the value, e.g. to export the verified cert as PEM).

    Args:
        peer_der: The peer certificate in DER form (``getpeercert(binary_form=True)``).
            ``None``/empty means no cert was presented — a fail-closed error.
        expected_fingerprint: The pinned SHA-256 in any accepted format (see module
            docstring). ``None``/empty means no pin is configured — a fail-closed
            error, because this function is only called on paths where a pin is
            required.
        exc_factory: Builds the exception raised on any failure. Defaults to
            ``ssl.SSLError``; domain callers pass a ``BambuError`` subclass.

    Raises:
        Whatever ``exc_factory`` produces, on a missing pin, a malformed pin, a
        missing peer cert, or a mismatch. Never a raw ``TypeError``/``ValueError``
        that would slip past a caller's transport-specific error handling.
    """
    expected = normalize_fingerprint(expected_fingerprint)
    if not expected:
        raise exc_factory("No certificate fingerprint pinned to verify against")
    # Validate the pin is exactly 64 lowercase hex chars *before* comparing.
    # normalize_fingerprint only strips ':' and ASCII spaces, so a stray
    # non-ASCII char (a copy-pasted NBSP, a Cyrillic homoglyph, ...) or a
    # wrong-length value would otherwise survive. hmac.compare_digest raises
    # TypeError on a non-ASCII str, and that TypeError is *not* the caller's
    # error type — in the camera path it would escape into the broad
    # except-Exception fallback and silently downgrade to the unpinned Docker
    # streamer. Fail closed here with the caller's own exception instead.
    if not _HEX64_RE.match(expected):
        raise exc_factory("Malformed certificate fingerprint pin (expected 64 hex chars for a SHA-256)")
    if not peer_der:
        raise exc_factory("No peer certificate to verify fingerprint against")
    # ``actual`` comes from hexdigest(): always 64 lowercase ASCII hex chars,
    # so it needs no validation before the constant-time compare.
    actual = hashlib.sha256(peer_der).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise exc_factory(f"Certificate fingerprint mismatch: expected {expected}, got {actual}")
    return actual
