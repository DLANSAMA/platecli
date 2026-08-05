"""The `download` command: HTTP fetch loop, redirects, HTML resolution, limits."""

import os
import tempfile
import urllib.error
import urllib.request
from typing import cast
from urllib.parse import urlparse

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import (
    DOWNLOAD_TIMEOUT,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    EXIT_NETWORK_ERROR,
    HTML_LINK_SCAN_LIMIT,
)
from bambu_cli.download.extract import _extract_zip_model, _is_archive_download
from bambu_cli.download.html_links import _is_html_content_type, _resolve_html_model_link
from bambu_cli.download.naming import (
    _download_filename_with_extension,
    _download_target_filename,
    _filename_from_content_disposition,
    _sanitize_download_filename,
)
from bambu_cli.download.validation import (
    _known_unsupported_download_extension,
    _normalize_url_input,
    _reject_oversized_download,
    _reject_unsupported_content_type,
    _reject_unsupported_download_extension,
    _validate_download_url_or_exit,
    _validate_max_download_mb_or_exit,
)
from bambu_cli.errors import BambuError, abort
from bambu_cli.fsutil import _download_partial_path, _noncolliding_path, _portable_basename, _remove_partial_file
from bambu_cli.jsonio import redact_url_credentials as _redact_url_credentials
from bambu_cli.logging_utils import logger, safe_log_error
from bambu_cli.netsafety import build_safe_opener, polite_open, user_agent_for_url
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.printables import _is_printables_model_url, resolve_printables_url
from bambu_cli.utils import _ensure_output_dir, _record_download_success, emit_json_error


def _response_header(resp, name):
    value = resp.getheader(name)
    return value if isinstance(value, str) else None


def _response_url(resp):
    """Return the final response URL after redirects when urllib exposes it."""
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):
        return None
    try:
        value = geturl()
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _cmd_download(
    args,
    *,
    opener_factory=None,
    resolve_printables=None,
    noncolliding_path=None,
):
    """Download a model or printer-ready file from a URL. Auto-resolves Printables page URLs.

    Collaborators (opener factory, Printables resolver, collision path helper)
    are injectable so tests pass fakes instead of patching module globals.
    Defaults are the real production implementations.
    """
    from bambu_cli import utils

    _openers = opener_factory if opener_factory is not None else build_safe_opener
    _resolve = resolve_printables if resolve_printables is not None else resolve_printables_url
    _noncolliding = noncolliding_path if noncolliding_path is not None else _noncolliding_path

    utils._LAST_DOWNLOAD_PAYLOAD = None
    source_url = args.url
    url = _normalize_url_input(source_url)
    normalized_source = url if url != source_url else None
    source_report = _redact_url_credentials(source_url)
    normalized_source_report = _redact_url_credentials(normalized_source)
    max_download_bytes = _validate_max_download_mb_or_exit(args)
    _validate_download_url_or_exit(args, source_url, normalized_source, url, "validate", "Invalid URL source")
    is_printables_model = _is_printables_model_url(url)
    if not is_printables_model:
        _reject_unsupported_download_extension(args, source_url, normalized_source, url, urlparse(url).path)

    outdir = _expand_path(args.output) if args.output else tempfile.gettempdir()
    if outdir.startswith("-"):
        message = f"Invalid output directory: {_path_for_message(outdir)}"
        emit_json_error(
            args,
            "download",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="validate",
            source=source_report,
            normalized_source=normalized_source_report,
            output=outdir,
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_COMMAND_ERROR)
    try:
        _ensure_output_dir(outdir)
    except BambuError as exc:
        emit_json_error(
            args,
            "download",
            getattr(exc, "exit_code", None) or EXIT_FILE_ERROR,
            f"Could not prepare output directory: {_path_for_message(outdir)}",
            failed_step="validate",
            source=source_report,
            normalized_source=normalized_source_report,
            output=outdir,
        )
        raise
    headers = {
        "Accept": "*/*",
    }

    resolved_url, stl_name = _resolve(url)

    # If the URL was a Printables page, it may have been resolved successfully.
    # If it was a Printables page and failed, we should return to match original behavior.
    if is_printables_model:
        if not resolved_url:
            emit_json_error(
                args,
                "download",
                EXIT_COMMAND_ERROR,
                "Failed to resolve Printables model URL.",
                failed_step="resolve",
                source=source_report,
                normalized_source=normalized_source_report,
            )
            abort("", exit_code=EXIT_COMMAND_ERROR)  # Failed to resolve, error message already printed
        url = resolved_url
        _reject_unsupported_download_extension(args, source_url, normalized_source, url, stl_name)
        _reject_unsupported_download_extension(args, source_url, normalized_source, url, urlparse(url).path)

    # Security: Validate URL scheme to prevent SSRF (e.g. file://)
    _validate_download_url_or_exit(
        args, source_url, normalized_source, url, "validate", "Invalid resolved download URL"
    )

    partial_path = None
    replace_on_success = False
    outpath = None
    safe_opener = _openers()
    # `_noncolliding` reserves the final output name by CREATING a 0-byte file
    # (ftps._noncolliding_path uses O_CREAT|O_EXCL). Every reservation is tracked
    # here so an abandoned placeholder (retarget, HTML re-resolve, or any failure
    # path) is unlinked instead of leaking a 0-byte model file with the requested
    # name that a later run/agent could mistake for a real download.
    reserved_paths: set[str] = set()

    def _reserve(path):
        reserved = _noncolliding(path)
        reserved_paths.add(reserved)
        return reserved

    def _release_reserved(path):
        """Stop tracking a reserved placeholder without deleting it (kept as real output)."""
        reserved_paths.discard(path)

    def _cleanup_reserved():
        """Unlink every still-tracked placeholder, ignoring errors.

        On success a kept output is removed from tracking first via
        ``_release_reserved`` before this runs, so it is never unlinked here.
        """
        for path in list(reserved_paths):
            try:
                os.unlink(path)
            except OSError:
                pass
            reserved_paths.discard(path)

    try:
        for _html_resolution_attempt in range(3):
            # A previous loop pass may have reserved a placeholder before deciding
            # to re-resolve an HTML page; drop it so it never leaks.
            _cleanup_reserved()
            archive_download = _is_archive_download(url, stl_name)
            if archive_download:
                archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
                    prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False
                )
                outpath = archive_temp.name
                archive_temp.close()
                filename = _portable_basename(outpath)
                partial_path = outpath
                replace_on_success = False
            else:
                filename = _download_target_filename(args, url, stl_name)
                outpath = os.path.join(outdir, filename)
                outpath = _reserve(outpath)
                filename = _portable_basename(outpath)
            req = urllib.request.Request(url, headers={**headers, "User-Agent": user_agent_for_url(url)})
            with polite_open(safe_opener, req, timeout=DOWNLOAD_TIMEOUT) as resp:
                final_url = _response_url(resp)
                if final_url and final_url != url:
                    try:
                        _validate_download_url_or_exit(
                            args,
                            source_url,
                            normalized_source,
                            final_url,
                            "download",
                            "Invalid redirected download URL",
                        )
                        if _known_unsupported_download_extension(urlparse(final_url).path):
                            _remove_partial_file(partial_path)
                            partial_path = None
                        _reject_unsupported_download_extension(
                            args,
                            source_url,
                            normalized_source,
                            final_url,
                            urlparse(final_url).path,
                            failed_step="download",
                        )
                    except BambuError:
                        _remove_partial_file(partial_path)
                        partial_path = None
                        raise
                    url = final_url
                    if not stl_name and not _namespace_get(args, "name") and not archive_download:
                        _cleanup_reserved()  # old-name placeholder is abandoned by the rename
                        filename = _download_target_filename(args, url, stl_name)
                        outpath = os.path.join(outdir, filename)
                        outpath = _reserve(outpath)
                        filename = _portable_basename(outpath)
                content_type = _response_header(resp, "Content-Type")
                archive_download = archive_download or _is_archive_download(url, stl_name, content_type)
                if archive_download and not filename.startswith(".bambu-download-"):
                    if partial_path and partial_path != outpath:
                        _remove_partial_file(partial_path)
                    # The resolved-name placeholder (if any) is abandoned for an archive temp.
                    _cleanup_reserved()
                    archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
                        prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False
                    )
                    outpath = archive_temp.name
                    archive_temp.close()
                    filename = _portable_basename(outpath)
                    partial_path = outpath
                    replace_on_success = False
                if _is_html_content_type(content_type):
                    if partial_path == outpath and outpath and filename.startswith(".bambu-download-"):
                        _remove_partial_file(partial_path)
                        partial_path = None
                    page_bytes = resp.read(HTML_LINK_SCAN_LIMIT + 1)
                    resolved_html_url, resolved_html_name = _resolve_html_model_link(page_bytes, url)
                    if resolved_html_url and resolved_html_url != url:
                        logger.info(f"🔗 Found model file link on page: {resolved_html_name or resolved_html_url}")
                        url = resolved_html_url
                        stl_name = resolved_html_name or stl_name
                        _validate_download_url_or_exit(
                            args,
                            source_url,
                            normalized_source,
                            url,
                            "resolve",
                            "Invalid resolved HTML model URL",
                        )
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, url, stl_name, failed_step="resolve"
                        )
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, url, urlparse(url).path, failed_step="resolve"
                        )
                        continue
                    message = "HTML page did not contain a direct model file link."
                    emit_json_error(
                        args,
                        "download",
                        EXIT_FILE_ERROR,
                        message,
                        failed_step="resolve",
                        source=source_report,
                        normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url),
                    )
                    safe_log_error(message)
                    logger.info(
                        "   Use a Printables model page, a direct .stl/.step/.stp/.obj/.3mf/.gcode/.zip download URL, or a page with a direct model-file link."
                    )
                    abort("", exit_code=EXIT_FILE_ERROR)
                if not archive_download:
                    _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type)

                header_filename = _filename_from_content_disposition(_response_header(resp, "Content-Disposition"))
                if header_filename and _is_archive_download(url, header_filename, content_type):
                    archive_download = True
                # An archive upgrade (URL/content-type OR a Content-Disposition
                # filename) always needs an archive temp to stream into. Creating
                # it must NOT be gated on `--name`/stl_name: when stl_name is set
                # and no --name is given that gate is False, so without this branch
                # partial_path stays None and the transfer body would open(None).
                if archive_download and not filename.startswith(".bambu-download-"):
                    if partial_path != outpath and partial_path:
                        _remove_partial_file(partial_path)
                    # Drop the resolved-name placeholder reserved for this outpath.
                    _cleanup_reserved()
                    archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
                        prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False
                    )
                    outpath = archive_temp.name
                    archive_temp.close()
                    filename = _portable_basename(outpath)
                    partial_path = outpath
                    replace_on_success = False
                # Non-archive Content-Disposition rename (the archive case is fully
                # handled by the archive-temp block above).
                if not archive_download and header_filename and (_namespace_get(args, "name") or not stl_name):
                    _reject_unsupported_download_extension(
                        args, source_url, normalized_source, url, header_filename, failed_step="download"
                    )
                    if _namespace_get(args, "name"):
                        filename = _download_filename_with_extension(
                            _sanitize_download_filename(_namespace_get(args, "name")),
                            url,
                            fallback_name=header_filename,
                        )
                    else:
                        filename = _download_filename_with_extension(
                            header_filename, url, fallback_name=header_filename
                        )
                    _cleanup_reserved()  # old-name placeholder abandoned by the CD rename
                    outpath = os.path.join(outdir, filename)
                    outpath = _reserve(outpath)
                    filename = _portable_basename(outpath)

                logger.info(f"⬇️  Downloading {filename}...")
                # Safety net: an archive upgrade path may leave partial_path unset;
                # a partial path must always exist before the transfer body opens it.
                if not archive_download or partial_path is None:
                    partial_path, replace_on_success = _download_partial_path(outpath)
                content_length = _response_header(resp, "Content-Length")
                try:
                    total_size = int(content_length) if content_length else None
                except ValueError:
                    total_size = None
                if total_size is not None and total_size > max_download_bytes:
                    _remove_partial_file(partial_path)
                    _reject_oversized_download(
                        args,
                        source_url,
                        normalized_source,
                        url,
                        outpath,
                        0,
                        max_download_bytes,
                        content_length=total_size,
                    )

                chunk_size = 65536  # 64KB chunks
                downloaded = 0
                last_percent_reported = -10
                download_exceeded_limit = False

                progress = None
                task_id = None
                try:
                    if not getattr(args, "json", False) and getattr(args, "progress", True):
                        from rich.progress import (
                            BarColumn,
                            DownloadColumn,
                            Progress,
                            TextColumn,
                            TimeRemainingColumn,
                            TransferSpeedColumn,
                        )

                        progress = Progress(
                            TextColumn("[bold blue]{task.description}", justify="right"),
                            BarColumn(bar_width=None),
                            "[progress.percentage]{task.percentage:>3.1f}%",
                            "•",
                            DownloadColumn(),
                            "•",
                            TransferSpeedColumn(),
                            "•",
                            TimeRemainingColumn(),
                            transient=True,
                        )
                        progress.start()
                        task_id = progress.add_task("Downloading", total=total_size)
                except ImportError:
                    pass

                # partial_path is always set before the transfer body (archive or
                # _download_partial_path); cast keeps mypy happy without a runtime branch.
                download_path = cast(str, partial_path)
                try:
                    with open(download_path, "wb") as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if downloaded > max_download_bytes:
                                download_exceeded_limit = True
                                break

                            if progress and task_id is not None:
                                progress.update(task_id, completed=downloaded)
                            elif total_size and total_size > 0:
                                percent = int((downloaded / total_size) * 100)
                                if percent - last_percent_reported >= 10:
                                    logger.info(
                                        f"   Download progress: {percent}% ({downloaded // 1024}KB / {total_size // 1024}KB)"
                                    )
                                    last_percent_reported = percent
                finally:
                    if progress:
                        progress.stop()

                if download_exceeded_limit:
                    _remove_partial_file(download_path)
                    _reject_oversized_download(
                        args,
                        source_url,
                        normalized_source,
                        url,
                        outpath,
                        downloaded,
                        max_download_bytes,
                    )

                if total_size is not None and downloaded < total_size:
                    _remove_partial_file(download_path)
                    message = f"Download ended early: received {downloaded} of {total_size} bytes."
                    emit_json_error(
                        args,
                        "download",
                        EXIT_NETWORK_ERROR,
                        message,
                        failed_step="download",
                        source=source_report,
                        normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url),
                        path=outpath,
                        received_bytes=downloaded,
                        expected_bytes=total_size,
                    )
                    safe_log_error(message)
                    abort("", exit_code=EXIT_NETWORK_ERROR)

            finished_path = cast(str, partial_path)
            size = os.path.getsize(finished_path)
            if size <= 0:
                _remove_partial_file(finished_path)
                _cleanup_reserved()  # drop the 0-byte reserved placeholder
                message = "Downloaded file is empty; refusing to use it."
                emit_json_error(
                    args,
                    "download",
                    EXIT_FILE_ERROR,
                    message,
                    failed_step="download",
                    source=source_report,
                    normalized_source=normalized_source_report,
                    download_url=_redact_url_credentials(url),
                    path=outpath,
                    bytes=size,
                )
                safe_log_error(message)
                abort("", exit_code=EXIT_FILE_ERROR)
            if replace_on_success:
                os.replace(finished_path, outpath)
                partial_path = None
                # outpath now holds the real bytes; keep it (stop tracking as a placeholder).
                _release_reserved(outpath)
            if archive_download:
                archive_path = outpath
                try:
                    extracted_path, extracted_filename, archive_entry, size = _extract_zip_model(
                        archive_path, outdir, args, noncolliding_path=_noncolliding
                    )
                except OSError as exc:
                    _remove_partial_file(archive_path)
                    message = f"Failed to extract archive: {exc}"
                    emit_json_error(
                        args,
                        "download",
                        EXIT_FILE_ERROR,
                        message,
                        failed_step="extract",
                        source=source_report,
                        normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url),
                        path=archive_path,
                    )
                    safe_log_error(message)
                    abort("", exit_code=EXIT_FILE_ERROR)
                except ValueError as exc:
                    _remove_partial_file(archive_path)
                    partial_path = None
                    message = str(exc)
                    emit_json_error(
                        args,
                        "download",
                        EXIT_FILE_ERROR,
                        message,
                        failed_step="extract",
                        source=source_report,
                        normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url),
                        path=archive_path,
                    )
                    safe_log_error(message)
                    abort("", exit_code=EXIT_FILE_ERROR)
                _remove_partial_file(archive_path)
                partial_path = None
                logger.info(f"✅ Downloaded: {_path_for_message(extracted_path)} ({size // 1024}KB)")
                _record_download_success(
                    args,
                    {
                        "status": "downloaded",
                        "command": "download",
                        "source": source_report,
                        "normalized_source": normalized_source_report,
                        "download_url": _redact_url_credentials(url),
                        "path": extracted_path,
                        "filename": extracted_filename,
                        "archive_entry": archive_entry,
                        "bytes": size,
                    },
                )
                return extracted_path
            logger.info(f"✅ Downloaded: {_path_for_message(outpath)} ({size // 1024}KB)")
            _record_download_success(
                args,
                {
                    "status": "downloaded",
                    "command": "download",
                    "source": source_report,
                    "normalized_source": normalized_source_report,
                    "download_url": _redact_url_credentials(url),
                    "path": outpath,
                    "filename": filename,
                    "bytes": size,
                },
            )
            return outpath

        message = "Could not resolve HTML page to a direct model file."
        emit_json_error(
            args,
            "download",
            EXIT_FILE_ERROR,
            message,
            failed_step="resolve",
            source=source_report,
            normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url),
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_FILE_ERROR)
    except urllib.error.HTTPError as e:
        _remove_partial_file(partial_path)
        _cleanup_reserved()
        message = f"Download failed: HTTP Error {e.code} ({e.reason})"
        emit_json_error(
            args,
            "download",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="download",
            source=source_report,
            normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url),
            http_status=e.code,
            path=outpath,
        )
        safe_log_error(message)
        if e.code == 404:
            logger.info("   The requested file or model does not exist. Check that the URL is correct.")
        elif e.code == 403:
            logger.info("   Access is forbidden. Printables or the host may be blocking automated requests.")
        try:
            e.close()
        except Exception:
            pass
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except urllib.error.URLError as e:
        _remove_partial_file(partial_path)
        _cleanup_reserved()
        err_msg = str(e.reason) if hasattr(e, "reason") else str(e)
        if "Security Error" in err_msg:
            message = f"SSRF Security Violation Blocked: {err_msg}"
            emit_json_error(
                args,
                "download",
                EXIT_COMMAND_ERROR,
                message,
                failed_step="validate",
                source=source_report,
                normalized_source=normalized_source_report,
                download_url=_redact_url_credentials(url),
                path=outpath,
            )
            safe_log_error(message)
            abort("", exit_code=EXIT_COMMAND_ERROR)
        message = f"Network error during download: {e}"
        emit_json_error(
            args,
            "download",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="download",
            source=source_report,
            normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url),
            path=outpath,
        )
        safe_log_error(message)
        logger.info("   Please check your internet connection or verify the domain name resolves correctly.")
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except OSError as e:
        _remove_partial_file(partial_path)
        _cleanup_reserved()
        message = f"Local file error during download: {_exception_for_message(e)}"
        emit_json_error(
            args,
            "download",
            EXIT_FILE_ERROR,
            message,
            failed_step="download",
            source=source_report,
            normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url),
            path=outpath,
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_FILE_ERROR)
    except BambuError:
        # Internal abort() paths (oversized, early-end, extract failure, etc.)
        # raise BambuError; drop any 0-byte reserved placeholder they left behind.
        _cleanup_reserved()
        raise
    except Exception as e:
        _remove_partial_file(partial_path)
        _cleanup_reserved()
        message = f"Download failed: {e}"
        emit_json_error(
            args,
            "download",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="download",
            source=source_report,
            normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url),
            path=outpath,
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_NETWORK_ERROR)
