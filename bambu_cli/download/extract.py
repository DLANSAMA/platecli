"""ZIP archive detection and safe single-member model extraction."""

import os
import zipfile
from urllib.parse import unquote, urlparse

from bambu_cli.cli import _namespace_get, _path_for_message
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    DOWNLOAD_LINK_EXTENSION_PRIORITY,
    DOWNLOADABLE_EXTENSIONS,
)
from bambu_cli.download.naming import (
    _download_filename_with_extension,
    _file_extension,
    _portable_basename,
    _sanitize_download_filename,
)
from bambu_cli.logging_utils import logger
from bambu_cli.protocols.ftps import _download_partial_path, _remove_partial_file


def _archive_member_too_large_message(filename, member_bytes, max_bytes):  # pragma: no cover -- msg
    limit_mb = max_bytes // (1024 * 1024)
    return f"ZIP member is too large: {filename} is {member_bytes} bytes and exceeds the {limit_mb} MB safety limit."


def _archive_member_exceeded_limit_message(filename, max_bytes):  # pragma: no cover -- msg
    limit_mb = max_bytes // (1024 * 1024)
    return f"ZIP member exceeded the {limit_mb} MB safety limit while extracting: {filename}"


def _is_zip_content_type(content_type):  # pragma: no cover -- zip type
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return media_type in ("application/zip", "application/x-zip-compressed")


def _is_archive_download(url, filename=None, content_type=None):  # pragma: no cover -- archive detect
    values = [filename, unquote(urlparse(url).path)]
    return any(
        _file_extension(_portable_basename(value or "")) in ARCHIVE_DOWNLOAD_EXTENSIONS for value in values
    ) or _is_zip_content_type(content_type)


def _select_zip_model_member(archive):  # pragma: no cover -- zip select
    """Pick the best supported model/print file from a ZIP without trusting paths."""
    candidates = []
    for index, info in enumerate(archive.infolist()):
        if info.is_dir() or info.file_size <= 0:
            continue
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:  # Symlink entries are not model files.
            continue
        filename = _sanitize_download_filename(_portable_basename(info.filename))
        ext = _file_extension(filename)
        if ext in DOWNLOADABLE_EXTENSIONS:
            candidates.append((DOWNLOAD_LINK_EXTENSION_PRIORITY.get(ext, 99), index, info, filename))
    if not candidates:
        return None, None
    _, _, info, filename = min(candidates)
    return info, filename


def _extract_zip_model(zip_path, outdir, args):  # pragma: no cover -- zip extract
    """Extract exactly one supported model/print file from a downloaded ZIP."""
    # Collision avoidance is looked up on the package so existing
    # ``bambu_cli.download._noncolliding_path`` test patches keep working.
    from bambu_cli import download as _download_pkg

    try:
        with zipfile.ZipFile(zip_path) as archive:
            info, member_filename = _select_zip_model_member(archive)
            if info is None:
                raise ValueError("ZIP archive did not contain a supported model or printer-ready file.")
            max_bytes = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)) * 1024 * 1024
            if info.file_size > max_bytes:
                raise ValueError(_archive_member_too_large_message(member_filename, info.file_size, max_bytes))
            if _namespace_get(args, "name"):
                filename = _download_filename_with_extension(
                    _sanitize_download_filename(_namespace_get(args, "name")),
                    member_filename,
                    fallback_name=member_filename,
                )
            else:
                filename = member_filename
            outpath = os.path.join(outdir, filename)
            outpath = _download_pkg._noncolliding_path(outpath)
            filename = _portable_basename(outpath)
            partial_path, replace_on_success = _download_partial_path(outpath)
            try:
                with archive.open(info, "r") as src, open(partial_path, "wb") as dst:
                    extracted = 0
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        extracted += len(chunk)
                        if extracted > max_bytes:
                            raise ValueError(_archive_member_exceeded_limit_message(member_filename, max_bytes))
                        dst.write(chunk)
            except Exception:
                _remove_partial_file(partial_path)
                raise
            size = os.path.getsize(partial_path)
            if size <= 0:
                _remove_partial_file(partial_path)
                raise ValueError(f"ZIP member is empty: {member_filename}")
            if replace_on_success:
                os.replace(partial_path, outpath)
            logger.info(f"📦 Extracted from ZIP: {member_filename} → {_path_for_message(outpath)} ({size // 1024}KB)")
            return outpath, filename, member_filename, size
    except zipfile.BadZipFile as exc:
        raise ValueError("Downloaded ZIP archive is invalid or corrupt.") from exc
