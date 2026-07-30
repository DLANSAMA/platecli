"""JSON-mode detection and output-redaction helpers.

Extracted from ``bambu_cli.cli`` (roadmap B.4) so domain modules can decide
whether machine-readable output was requested, and scrub credentials from URLs
before they reach a log line or a JSON envelope, without importing from the CLI
entrypoint. These helpers never terminate the process.

Note: ``bambu_cli.utils`` carries its own credential-redaction pass applied
uniformly across every emitted JSON payload; the ``redact_url_credentials``
here is the eager, single-value variant callers use when building the strings
that go into those payloads and log messages.
"""

from urllib.parse import urlparse, urlunparse

__all__ = [
    "json_mode_requested",
    "looks_like_schemeless_credential_url",
    "redact_url_credentials",
]


def json_mode_requested(args):
    return bool(getattr(args, "json", False))


def looks_like_schemeless_credential_url(value):
    """Detect userinfo-bearing URLs where the user omitted https://."""
    text = str(value or "")
    if "\\" in text or any(char.isspace() for char in text):
        return False
    if "@" not in text or text.startswith(("/", ".", "~", "$")):
        return False
    try:
        parsed = urlparse(f"https://{text}")
        host = parsed.hostname or ""
        return bool(parsed.netloc and (parsed.username is not None or parsed.password is not None) and "." in host)
    except Exception:
        return False


def redact_url_credentials(value):
    """Return URL text with any userinfo removed before logging or JSON output."""
    text = str(value or "")
    if "@" not in text:
        return value
    parsed = urlparse(text)
    if "://" not in text and looks_like_schemeless_credential_url(text):
        redacted = redact_url_credentials(f"https://{text}")
        prefix = "https://"
        return redacted[len(prefix) :] if isinstance(redacted, str) and redacted.startswith(prefix) else redacted
    # Scheme-relative URLs (//user:pass@host/…) parse with an empty scheme but a
    # populated netloc; strip userinfo while preserving the leading "//".
    if (
        not parsed.scheme
        and text.startswith("//")
        and parsed.netloc
        and (parsed.username is not None or parsed.password is not None)
    ):
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            host = f"{host}:{port}"
        return urlunparse(("", host, parsed.path, parsed.params, parsed.query, parsed.fragment))
    if not parsed.scheme or not parsed.netloc or (parsed.username is None and parsed.password is None):
        return value
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))
