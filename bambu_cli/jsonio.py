"""JSON-mode detection and output-redaction helpers.

Extracted from ``bambu_cli.cli`` (roadmap B.4) so domain modules can decide
whether machine-readable output was requested, and scrub credentials from URLs
before they reach a log line or a JSON envelope, without importing from the CLI
entrypoint. These helpers never terminate the process.

This is the single redactor: command code calls it when building log lines
and payload fields, and ``bambu_cli.utils.emit_json`` runs the same function
over every emitted string.
"""

import re
from urllib.parse import urlparse, urlunparse

__all__ = [
    "json_mode_requested",
    "looks_like_schemeless_credential_url",
    "looks_like_split_userinfo",
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


def _strip_through_last_at(text):
    """Conservative scrub for URL text the parser cannot read.

    Drops everything between the scheme separator and the last ``@``, which is
    where a browser-style reader would put the userinfo. Over-redacting an
    already-invalid URL loses nothing; echoing a password does.
    """
    sep = text.find("://")
    start = sep + 3 if sep >= 0 else (2 if text.startswith("//") else 0)
    at = text.rfind("@")
    if at < start:
        return text
    return text[:start] + text[at + 1 :]


def _port_is_invalid(parsed):
    try:
        _ = parsed.port
    except ValueError:
        return True
    return False


_AUTHORITY_END = re.compile(r"[/?#]")


def _after_netloc(text, parsed):
    start = text.find("//")
    at = text.find(parsed.netloc, start + 2 if start >= 0 else 0)
    return text[at + len(parsed.netloc) :] if at >= 0 else ""


def looks_like_split_userinfo(text, parsed=None):
    """True for ``user:12/pw@host``: a password holding ``/``, ``?`` or ``#``.

    The parser ends the authority at the first of those, so when the text
    before it is a valid port (digits, or nothing) the URL parses cleanly as
    host ``user`` and port ``12``, with ``pw@host`` in the path, query or
    fragment. A segment that starts with ``@`` (``/@scope/pkg``) is a path.
    """
    if parsed is None:
        try:
            parsed = urlparse(text)
        except ValueError:
            return False
    if not parsed.netloc or "@" in parsed.netloc or ":" not in parsed.netloc:
        return False
    rest = _after_netloc(text, parsed)
    if not rest or rest[0] not in "/?#":
        return False
    segment = _AUTHORITY_END.split(rest[1:], maxsplit=1)[0]
    return "@" in segment and not segment.startswith("@")


def redact_url_credentials(value):
    """Return URL text with any userinfo removed before logging or JSON output."""
    text = str(value or "")
    if "@" not in text:
        return value
    try:
        parsed = urlparse(text)
    except ValueError:
        # e.g. an unbalanced IPv6 bracket after the userinfo: user:pw@[x/a.stl
        return _strip_through_last_at(text)
    after_netloc = _after_netloc(text, parsed) if parsed.netloc else ""
    if parsed.netloc and _port_is_invalid(parsed) and "@" in after_netloc:
        # A password holding "/", "?" or "#" (user:pa/ss@host) makes the parser
        # read "user" as the host and the password as the port, so there is no
        # userinfo to strip and the secret would be echoed as-is.
        return _strip_through_last_at(text)
    if looks_like_split_userinfo(text, parsed):
        # The same with a password that starts with digits: user:1234/pw@host.
        return _strip_through_last_at(text)
    if len(parsed.scheme) > 1 and not parsed.netloc and text[len(parsed.scheme) + 1 :].startswith(("/", "\\")):
        # https:///user:pw@host, https:/user:pw@host, https:\\user:pw@host: no
        # authority for the parser, but a lenient reader still finds userinfo.
        # (A one-letter "scheme" is a Windows drive: C:\\Users\\a@b\\x.stl.)
        return _strip_through_last_at(text)
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
