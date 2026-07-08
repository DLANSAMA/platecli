"""Filename and extension helpers for downloads and printer-side files."""

import email.message
import email.utils
import os
import re
from urllib.parse import unquote, urlparse

from bambu_cli.cli import _namespace_get, _redact_url_credentials
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DOWNLOADABLE_EXTENSIONS,
    EXIT_FILE_ERROR,
    MAX_DOWNLOAD_FILENAME_LENGTH,
    PRINT_READY_EXTENSIONS,
    WINDOWS_RESERVED_FILENAMES,
)
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger


def _name_for_message(value):  # pragma: no cover -- naming helper
    """Return a local/remote name for messages without URL credentials."""
    return _redact_url_credentials(value)


def _file_extension(path):  # pragma: no cover -- naming helper
    return os.path.splitext(path)[1].lower()


def _portable_basename(path):  # pragma: no cover -- naming helper
    """Return a basename while treating both POSIX and Windows separators as separators."""
    return os.path.basename(str(path or "").replace("\\", "/"))


def _download_source_extension(url, fallback_name=None):  # pragma: no cover -- naming helper
    """Infer the model/print extension from a URL path or resolved filename."""
    for value in (fallback_name, unquote(urlparse(url).path)):
        ext = _file_extension(value or "")
        if ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
            return ext
    return ".stl"


def _download_filename_with_extension(filename, url, fallback_name=None):  # pragma: no cover -- naming helper
    source_ext = _download_source_extension(url, fallback_name=fallback_name)
    stem, ext = os.path.splitext(filename)
    if ext.lower() in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        if ext.lower() != source_ext:
            return f"{stem}{source_ext}"
        return filename
    return filename + source_ext


def _download_target_filename(args, url, resolved_name=None):  # pragma: no cover -- naming helper
    """Choose a safe local filename for a direct model/print download."""
    if _namespace_get(args, "name"):
        filename = _sanitize_download_filename(_namespace_get(args, "name"))
    elif resolved_name:
        filename = _sanitize_download_filename(resolved_name)
    else:
        path = urlparse(url).path
        filename = _sanitize_download_filename(_portable_basename(unquote(path)) or "model.stl")
    return _download_filename_with_extension(filename, url, fallback_name=resolved_name)


def _sanitize_download_filename(filename):  # pragma: no cover -- naming helper
    filename = _portable_basename(filename)
    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", filename).strip(" .")
    if filename in (".", "..") or not filename:
        return "model.stl"
    stem, ext = os.path.splitext(filename)
    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        filename = f"_{filename}"
        stem, ext = os.path.splitext(filename)
    if len(filename) > MAX_DOWNLOAD_FILENAME_LENGTH:
        stem_limit = max(1, MAX_DOWNLOAD_FILENAME_LENGTH - len(ext))
        filename = f"{stem[:stem_limit]}{ext}"
    return filename


def _filename_from_content_disposition(value):  # pragma: no cover -- naming helper
    if not value:
        return None
    message = email.message.Message()
    message["content-disposition"] = value
    filename = None
    # RFC 5987/RFC 2231 filename* values carry the better decoded filename.
    # email.message normalizes them as duplicate "filename" tuple params; when
    # both filename and filename* exist, prefer the tuple value.
    for key, param_value in reversed(message.get_params(header="content-disposition") or []):
        if key.lower() == "filename" and isinstance(param_value, tuple):
            filename = email.utils.collapse_rfc2231_value(param_value)
            break
    if filename is None:
        filename = message.get_filename()
    return _sanitize_download_filename(filename) if filename else None


def _safe_remote_name(filename):  # pragma: no cover -- naming helper
    """Reject names that are unsafe for printer-side files.

    FTP commands are CRLF-delimited, so a NUL/CR/LF in a filename bound into a
    ``STOR``/``DELE`` line could smuggle a second command. ``os.path.basename``
    strips path separators but not these, so we reject them explicitly. Also
    reject Windows/FAT-hostile characters and reserved names because printer SD
    storage and cross-platform agent workflows should use portable filenames.
    Returns the name unchanged if safe, else ``None``.
    """
    if not filename or filename in (".", ".."):
        return None
    if filename != _portable_basename(filename):
        return None
    if any(c in filename for c in ("\r", "\n", "\0")):
        return None
    if any(c in filename for c in '<>:"/\\|?*'):
        return None
    if filename != filename.strip(" ."):
        return None
    if len(filename) > MAX_DOWNLOAD_FILENAME_LENGTH:
        return None
    stem, _ = os.path.splitext(filename)
    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        return None
    return filename


def _is_print_ready_name(filename):  # pragma: no cover -- naming helper
    return _file_extension(filename) in PRINT_READY_EXTENSIONS


def _reject_non_print_ready(filename, action):  # pragma: no cover -- naming helper
    if not _is_print_ready_name(filename):
        logger.error(_print_ready_error_message(filename, action))
        abort("", exit_code=EXIT_FILE_ERROR)


def _print_ready_error_message(filename, action):  # pragma: no cover -- naming helper
    supported = ", ".join(PRINT_READY_EXTENSIONS)
    return f"Cannot {action} '{filename}': expected a printer-ready file ({supported}). Use `job` or `slice` for model files."
