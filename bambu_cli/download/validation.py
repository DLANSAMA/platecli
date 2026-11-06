"""URL normalization/validation and download safety-limit checks."""

import os
import re
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    DOWNLOADABLE_EXTENSIONS,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    KNOWN_UNSUPPORTED_CONTENT_TYPES,
    KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS,
)
from bambu_cli.download.naming import _file_extension
from bambu_cli.errors import BambuError, abort
from bambu_cli.fsutil import _portable_basename
from bambu_cli.jsonio import looks_like_schemeless_credential_url as _looks_like_schemeless_credential_url
from bambu_cli.jsonio import looks_like_split_userinfo as _looks_like_split_userinfo
from bambu_cli.jsonio import redact_url_credentials as _redact_url_credentials
from bambu_cli.logging_utils import safe_log_error
from bambu_cli.paths import expand_path as _expand_path

_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _safe_urlparse(value):
    """``urlparse`` that returns None instead of raising on a malformed URL.

    ``urlparse`` raises ``ValueError`` for an unbalanced IPv6 bracket such as
    ``http://[::1/x.stl``; callers report that as an invalid URL rather than
    letting it escape as an unexpected error with a traceback.
    """
    try:
        return urlparse(value)
    except ValueError:
        return None


# RFC 3986 reserved and unreserved characters, plus "%" so existing escapes
# survive: quoting with this safe set only touches non-ASCII and unsafe bytes.
_URI_SAFE = "!#$%&'()*+,/:;=?@[]~"


def _iri_to_uri(url):
    """Percent-encode non-ASCII in the path, query and fragment.

    A URL pasted from a browser address bar can hold raw Unicode
    (``/files/модель.stl``); http.client sends the request line as ASCII and
    would fail with an opaque ``'ascii' codec can't encode`` error.
    """
    if url.isascii():
        return url
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe=_URI_SAFE),
            quote(parts.query, safe=_URI_SAFE),
            quote(parts.fragment, safe=_URI_SAFE),
        )
    )


def _looks_like_url(value):
    parsed = _safe_urlparse(value)
    if parsed is None:
        return bool(_SCHEME_PREFIX.match(value))
    return bool(parsed.scheme and "://" in value)


def _normalize_url_input(value):
    """Accept common scheme-less website inputs without mistaking model.stl for a URL."""
    if _looks_like_url(value):
        return value
    if value.startswith(("/", ".", "~", "$")) or os.path.exists(_expand_path(value)):
        return value
    if _looks_like_schemeless_credential_url(value):
        return f"https://{value}"

    # Agents and users often omit https:// for web pages. Require either a
    # www. host or a domain/path form so missing local files like model.stl stay
    # local paths and get the normal "File not found" error.
    if value.startswith("www.") or re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?/", value):
        return f"https://{value}"
    return value


def _is_http_url(value):
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in (value or "")):
        return False
    parsed = _safe_urlparse(value)
    return parsed is not None and parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _validate_http_url_or_exit(value):
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in (value or "")):
        message = "Invalid URL: contains whitespace or control characters"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")
    parsed = _safe_urlparse(value)
    if parsed is None:
        message = "Invalid URL: malformed host"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")
    if parsed.scheme.lower() not in ("http", "https"):
        message = f"Invalid URL scheme: {parsed.scheme or 'none'}"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")
    if not parsed.netloc:
        message = "Invalid URL: missing host"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")
    if parsed.username is not None or parsed.password is not None or _looks_like_split_userinfo(value, parsed):
        # The second form is user:1234/pw@host: it parses as host "user", so the
        # request would carry the password in its path to whatever "user" resolves to.
        message = "Invalid URL: embedded credentials are not supported"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")
    try:
        _ = parsed.port
    except ValueError:
        # Usually a password holding "/", "?" or "#" (user:pa/ss@host): the
        # parser then reads the password as the port. The message must not
        # echo it, and http.client's own "nonnumeric port: '<secret>'" must
        # never be reached.
        message = "Invalid URL: the port is not a number (a password in the URL is not supported)"
        safe_log_error(message)
        abort(message, exit_code=EXIT_COMMAND_ERROR, failed_step="validate")


def _validate_download_url_or_exit(args, source_url, normalized_source, url, failed_step, label):
    """Validate a download URL and attach redacted source fields on failure."""
    try:
        _validate_http_url_or_exit(url)
    except BambuError as exc:
        extra = {
            "source": _redact_url_credentials(source_url),
            "normalized_source": _redact_url_credentials(normalized_source),
            "download_url": _redact_url_credentials(url),
        }
        extra.update(exc.extra or {})
        abort(
            str(exc),
            exit_code=exc.exit_code,
            failed_step=failed_step or exc.failed_step,
            extra=extra,
            command="download",
        )


def _known_unsupported_download_extension(value):
    """Return a clearly unsupported source extension, or None when ambiguous."""
    ext = _file_extension(_portable_basename(unquote(str(value or ""))))
    if ext and ext not in DOWNLOADABLE_EXTENSIONS and ext in KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS:
        return ext
    return None


def _unsupported_download_message(ext):
    supported = ", ".join(DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
    return (
        f"Unsupported download file type '{ext}'. Supported types: {supported}. "
        "Use a direct model/print file, a ZIP containing a model/print file, a Printables model page, or a page with a direct model-file link."
    )


def _reject_unsupported_download_extension(args, source_url, normalized_source, url, value, failed_step="validate"):
    ext = _known_unsupported_download_extension(value)
    if not ext:
        return
    message = _unsupported_download_message(ext)
    abort(
        message,
        exit_code=EXIT_FILE_ERROR,
        failed_step=failed_step,
        extra={
            "source": _redact_url_credentials(source_url),
            "normalized_source": _redact_url_credentials(normalized_source),
            "download_url": _redact_url_credentials(url),
            "extension": ext,
        },
    )


def _known_unsupported_content_type(content_type):
    """Return a clearly unsupported response content type, or None when ambiguous."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type:
        return None
    if media_type.startswith("image/"):
        return media_type
    if media_type in KNOWN_UNSUPPORTED_CONTENT_TYPES:
        return media_type
    return None


def _names_a_model_file(url):
    """True when the URL path ends in a supported model or print extension."""
    parsed = _safe_urlparse(url or "")
    if parsed is None:
        return False
    return _file_extension(_portable_basename(unquote(parsed.path))) in DOWNLOADABLE_EXTENSIONS


def _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type):
    media_type = _known_unsupported_content_type(content_type)
    if not media_type:
        return
    # ASCII STL, OBJ, STEP and G-code are text, and many hosts (GitHub raw among
    # them) serve every file as text/plain. When the URL itself names a model
    # file, text/plain says nothing about whether it is one.
    if media_type == "text/plain" and _names_a_model_file(url):
        return
    message = f"Download URL returned unsupported content type '{media_type}', not a model file."
    abort(
        message,
        exit_code=EXIT_FILE_ERROR,
        failed_step="download",
        extra={
            "source": _redact_url_credentials(source_url),
            "normalized_source": _redact_url_credentials(normalized_source),
            "download_url": _redact_url_credentials(url),
            "content_type": media_type,
        },
    )


def _max_download_mb_error(args):
    max_download_mb = _namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)
    try:
        max_download_mb = int(max_download_mb)
    except (TypeError, ValueError, OverflowError):
        max_download_mb = 0
    if max_download_mb <= 0:
        return "--max-download-mb must be a positive integer"
    return None


def _validate_max_download_mb_or_exit(args, command="download"):
    message = _max_download_mb_error(args)
    if message:
        abort(
            message,
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
        )
    max_download_mb = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB))
    return max_download_mb * 1024 * 1024


def _reject_oversized_download(
    args, source_url, normalized_source, url, outpath, received_bytes, max_bytes, content_length=None
):
    limit_mb = max_bytes // (1024 * 1024)
    if content_length is not None:
        message = f"Download is too large: {content_length} bytes exceeds the {limit_mb} MB safety limit."
    else:
        message = f"Download exceeded the {limit_mb} MB safety limit."
    safe_log_error(message)
    abort(
        message,
        exit_code=EXIT_FILE_ERROR,
        failed_step="download",
        extra={
            "source": _redact_url_credentials(source_url),
            "normalized_source": _redact_url_credentials(normalized_source),
            "download_url": _redact_url_credentials(url),
            "path": outpath,
            "received_bytes": received_bytes,
            "content_length": content_length,
            "max_download_bytes": max_bytes,
        },
    )
