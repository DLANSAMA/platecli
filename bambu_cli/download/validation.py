"""URL normalization/validation and download safety-limit checks."""

import os
import re
from urllib.parse import unquote, urlparse

from bambu_cli.cli import (
    _expand_path,
    _looks_like_schemeless_credential_url,
    _namespace_get,
    _redact_url_credentials,
)
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    DOWNLOADABLE_EXTENSIONS,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    KNOWN_UNSUPPORTED_CONTENT_TYPES,
    KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS,
)
from bambu_cli.download.naming import _file_extension, _portable_basename
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json_error


def _looks_like_url(value):  # pragma: no cover -- validation helper
    parsed = urlparse(value)
    return bool(parsed.scheme and "://" in value)


def _normalize_url_input(value):  # pragma: no cover -- validation helper
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


def _is_http_url(value):  # pragma: no cover -- validation helper
    parsed = urlparse(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _validate_http_url_or_exit(value):  # pragma: no cover -- url validate
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https"):
        logger.error(f"Invalid URL scheme: {parsed.scheme or 'none'}")
        abort("", exit_code=EXIT_COMMAND_ERROR)
    if not parsed.netloc:
        logger.error("Invalid URL: missing host")
        abort("", exit_code=EXIT_COMMAND_ERROR)
    if parsed.username is not None or parsed.password is not None:
        logger.error("Invalid URL: embedded credentials are not supported")
        abort("", exit_code=EXIT_COMMAND_ERROR)


def _validate_download_url_or_exit(args, source_url, normalized_source, url, failed_step, label):  # pragma: no cover -- validation helper
    """Validate a download URL and emit structured, redacted JSON on failure."""
    try:
        _validate_http_url_or_exit(url)
    except BambuError as exc:
        emit_json_error(
            args,
            "download",
            getattr(exc, "exit_code", getattr(exc, "code", 5)),
            f"{label}: {_redact_url_credentials(url)}",
            failed_step=failed_step,
            source=_redact_url_credentials(source_url),
            normalized_source=_redact_url_credentials(normalized_source),
            download_url=_redact_url_credentials(url),
        )
        raise


def _known_unsupported_download_extension(value):  # pragma: no cover -- validation helper
    """Return a clearly unsupported source extension, or None when ambiguous."""
    ext = _file_extension(_portable_basename(unquote(str(value or ""))))
    if ext and ext not in DOWNLOADABLE_EXTENSIONS and ext in KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS:
        return ext
    return None


def _unsupported_download_message(ext):  # pragma: no cover -- validation helper
    supported = ", ".join(DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
    return (
        f"Unsupported download file type '{ext}'. Supported types: {supported}. "
        "Use a direct model/print file, a ZIP containing a model/print file, a Printables model page, or a page with a direct model-file link."
    )


def _reject_unsupported_download_extension(args, source_url, normalized_source, url, value, failed_step="validate"):  # pragma: no cover -- validation helper
    ext = _known_unsupported_download_extension(value)
    if not ext:
        return
    message = _unsupported_download_message(ext)
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step=failed_step,
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        extension=ext,
    )
    abort("", exit_code=EXIT_FILE_ERROR)


def _known_unsupported_content_type(content_type):  # pragma: no cover -- validation helper
    """Return a clearly unsupported response content type, or None when ambiguous."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type:
        return None
    if media_type.startswith("image/"):
        return media_type
    if media_type in KNOWN_UNSUPPORTED_CONTENT_TYPES:
        return media_type
    return None


def _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type):  # pragma: no cover -- validation helper
    media_type = _known_unsupported_content_type(content_type)
    if not media_type:
        return
    message = f"Download URL returned unsupported content type '{media_type}', not a model file."
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step="download",
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        content_type=media_type,
    )
    abort("", exit_code=EXIT_FILE_ERROR)


def _max_download_mb_error(args):  # pragma: no cover -- validation helper
    max_download_mb = _namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)
    try:
        max_download_mb = int(max_download_mb)
    except (TypeError, ValueError):
        max_download_mb = 0
    if max_download_mb <= 0:
        return "--max-download-mb must be a positive integer"
    return None


def _validate_max_download_mb_or_exit(args, command="download"):  # pragma: no cover -- validation helper
    message = _max_download_mb_error(args)
    if message:
        logger.error(message)
        emit_json_error(args, command, EXIT_COMMAND_ERROR, message, failed_step="validate")
        abort("", exit_code=EXIT_COMMAND_ERROR)
    max_download_mb = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB))
    return max_download_mb * 1024 * 1024


def _reject_oversized_download(  # pragma: no cover -- validation helper
    args, source_url, normalized_source, url, outpath, received_bytes, max_bytes, content_length=None
):
    limit_mb = max_bytes // (1024 * 1024)
    if content_length is not None:
        message = f"Download is too large: {content_length} bytes exceeds the {limit_mb} MB safety limit."
    else:
        message = f"Download exceeded the {limit_mb} MB safety limit."
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step="download",
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        path=outpath,
        received_bytes=received_bytes,
        content_length=content_length,
        max_download_bytes=max_bytes,
    )
    abort("", exit_code=EXIT_FILE_ERROR)
