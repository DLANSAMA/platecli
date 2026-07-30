"""Filename and extension helpers for downloads and printer-side files."""

import email.message
import email.utils
import os
import re
from urllib.parse import unquote, urlparse

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DOWNLOADABLE_EXTENSIONS,
    EXIT_FILE_ERROR,
    MAX_DOWNLOAD_FILENAME_LENGTH,
    PRINT_READY_EXTENSIONS,
    WINDOWS_RESERVED_FILENAMES,
)
from bambu_cli.errors import abort
from bambu_cli.jsonio import redact_url_credentials as _redact_url_credentials
from bambu_cli.logging_utils import logger


def _name_for_message(value):
    """Return a local/remote name for messages without URL credentials."""
    return _redact_url_credentials(value)


def _file_extension(path):
    return os.path.splitext(path)[1].lower()


def _portable_basename(path):
    """Return a basename while treating both POSIX and Windows separators as separators."""
    return os.path.basename(str(path or "").replace("\\", "/"))


def _download_source_extension(url, fallback_name=None):
    """Infer the model/print extension from a URL path or resolved filename."""
    for value in (fallback_name, unquote(urlparse(url).path)):
        ext = _file_extension(value or "")
        if ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
            return ext
    return ".stl"


def _download_filename_with_extension(filename, url, fallback_name=None):
    source_ext = _download_source_extension(url, fallback_name=fallback_name)
    stem, ext = os.path.splitext(filename)
    if ext.lower() in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        if ext.lower() != source_ext:
            return f"{stem}{source_ext}"
        return filename
    return filename + source_ext


def _download_target_filename(args, url, resolved_name=None):
    """Choose a safe local filename for a direct model/print download."""
    if _namespace_get(args, "name"):
        filename = _sanitize_download_filename(_namespace_get(args, "name"))
    elif resolved_name:
        filename = _sanitize_download_filename(resolved_name)
    else:
        path = urlparse(url).path
        filename = _sanitize_download_filename(_portable_basename(unquote(path)) or "model.stl")
    return _download_filename_with_extension(filename, url, fallback_name=resolved_name)


def _reserved_device_stem(filename):
    """True if *filename* begins with a Windows reserved device name.

    Windows reserves the segment before the **first** dot, so ``aux.gcode.3mf`` is
    every bit as reserved as ``aux.stl``. ``os.path.splitext`` strips only the last
    extension, which let every ``<device>.gcode.3mf`` — this project's primary
    print-ready extension — through both the repairer and the safety check.
    """
    return filename.split(".", 1)[0].upper() in WINDOWS_RESERVED_FILENAMES


def _utf8_truncate(value, budget):
    """Trim *value* to at most *budget* UTF-8 bytes without splitting a codepoint."""
    encoded = value.encode("utf-8")
    if len(encoded) <= budget:
        return value
    return encoded[:budget].decode("utf-8", errors="ignore")


def _within_name_budget(filename):
    """Filename length is capped in **bytes**, not characters.

    160 CJK characters encode to 472 UTF-8 bytes, which exceeds ext4's 255-byte
    per-name limit, so a character-based cap happily produced names the local
    filesystem then refused with ``ENAMETOOLONG``.
    """
    return len(filename.encode("utf-8")) <= MAX_DOWNLOAD_FILENAME_LENGTH


def _sanitize_download_filename(filename):
    """Repair an arbitrary (often URL-derived) name into a portable, safe one.

    Guarantees the result is accepted by ``_safe_remote_name``, so a file that
    downloads can always be uploaded. That did not hold before: truncation ran
    after the reserved-name check and ignored the extension length, so it could
    emit a name over the cap, ending in a space, or shortened back into ``AUX`` —
    each of which the printer-side check then rejected.
    """
    filename = _portable_basename(filename)
    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", filename).strip(" .")
    if filename in (".", "..") or not filename:
        return "model.stl"
    if _reserved_device_stem(filename):
        filename = f"_{filename}"
    if not _within_name_budget(filename):
        stem, ext = os.path.splitext(filename)
        # Cap the share of the budget an "extension" may claim. Without this, a
        # pathological name like "x." + "a" * 200 left stem_limit at 1 and returned
        # the whole 202-byte string untruncated, over the cap.
        ext_budget = min(len(ext.encode("utf-8")), MAX_DOWNLOAD_FILENAME_LENGTH // 2)
        stem = _utf8_truncate(stem, max(1, MAX_DOWNLOAD_FILENAME_LENGTH - ext_budget))
        filename = f"{stem}{_utf8_truncate(ext, ext_budget)}"
    # Strip AFTER truncating, not before: truncation can land on a space or dot,
    # which _safe_remote_name rejects.
    filename = filename.strip(" .")
    # Enforce the invariant rather than trusting the ordering above. Falling back
    # loses the original name, so `test_repair_never_degrades_a_usable_name` asserts
    # this path is not reached for any input that can be repaired.
    return filename if _safe_remote_name(filename) else "model.stl"


def _filename_from_content_disposition(value):
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


def _has_command_injection_chars(value):
    """True if *value* contains CR, LF, or NUL.

    FTP and MQTT command lines are delimited by these characters; embedding them
    in a filename or G-code payload can smuggle a second command. Shared by
    ``_safe_remote_name`` and ``cmd_gcode`` validation.
    """
    return any(c in (value or "") for c in ("\r", "\n", "\0"))


def _safe_remote_name(filename):
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
    if _has_command_injection_chars(filename):
        return None
    if any(c in filename for c in '<>:"/\\|?*'):
        return None
    if filename != filename.strip(" ."):
        return None
    # Byte budget and first-dot device check, matching _sanitize_download_filename.
    # These two rules must stay identical in both functions: the repairer validates
    # its own output against this check, so any divergence means a file that
    # downloads cannot be uploaded.
    if not _within_name_budget(filename):
        return None
    if _reserved_device_stem(filename):
        return None
    return filename


def _is_print_ready_name(filename):
    return _file_extension(filename) in PRINT_READY_EXTENSIONS


def _reject_non_print_ready(filename, action):
    if not _is_print_ready_name(filename):
        logger.error(_print_ready_error_message(filename, action))
        abort("", exit_code=EXIT_FILE_ERROR)


def _print_ready_error_message(filename, action):
    supported = ", ".join(PRINT_READY_EXTENSIONS)
    return f"Cannot {action} '{filename}': expected a printer-ready file ({supported}). Use `job` or `slice` for model files."
