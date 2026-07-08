"""The `download` command: HTTP fetch loop, redirects, HTML resolution, limits."""

import os
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.cli import (
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
    _redact_url_credentials,
)
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
    _portable_basename,
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
from bambu_cli.logging_utils import logger
from bambu_cli.netsafety import _default_user_agent
from bambu_cli.printables import _is_printables_model_url
from bambu_cli.protocols.ftps import _download_partial_path, _remove_partial_file
from bambu_cli.utils import _ensure_output_dir, _record_download_success, emit_json_error


def _response_header(resp, name):  # pragma: no cover -- header helper
    value = resp.getheader(name)
    return value if isinstance(value, str) else None


def _response_url(resp):
    """Return the final response URL after redirects when urllib exposes it."""
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):  # pragma: no cover -- non-urllib response objects
        return None
    try:
        value = geturl()
    except Exception:  # pragma: no cover -- defensive
        return None
    return value if isinstance(value, str) and value else None


def _cmd_download(args):  # pragma: no cover -- HTTP download orchestration
    """Download a model or printer-ready file from a URL. Auto-resolves Printables page URLs."""
    # build_safe_opener, resolve_printables_url, and _noncolliding_path are
    # called through the package namespace (imported here, not at module
    # level, to avoid touching the partially-initialized package during
    # import) so existing test patches on ``bambu_cli.download.<name>`` keep
    # working after the package split.
    from bambu_cli import download as _download_pkg
    from bambu_cli import utils

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
        logger.error(message)
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
        "User-Agent": _default_user_agent(),
        "Accept": "*/*",
    }

    resolved_url, stl_name = _download_pkg.resolve_printables_url(url)

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
    safe_opener = _download_pkg.build_safe_opener()
    try:
        for _html_resolution_attempt in range(3):
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
                outpath = _download_pkg._noncolliding_path(outpath)
                filename = _portable_basename(outpath)
            req = urllib.request.Request(url, headers=headers)
            with safe_opener.open(req, timeout=DOWNLOAD_TIMEOUT) as resp:
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
                        filename = _download_target_filename(args, url, stl_name)
                        outpath = os.path.join(outdir, filename)
                        outpath = _download_pkg._noncolliding_path(outpath)
                        filename = _portable_basename(outpath)
                content_type = _response_header(resp, "Content-Type")
                archive_download = archive_download or _is_archive_download(url, stl_name, content_type)
                if archive_download and not filename.startswith(".bambu-download-"):
                    if partial_path and partial_path != outpath:
                        _remove_partial_file(partial_path)
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
                    logger.error(message)
                    logger.info(
                        "   Use a Printables model page, a direct .stl/.step/.stp/.obj/.3mf/.gcode/.zip download URL, or a page with a direct model-file link."
                    )
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
                    abort("", exit_code=EXIT_FILE_ERROR)
                if not archive_download:
                    _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type)

                header_filename = _filename_from_content_disposition(_response_header(resp, "Content-Disposition"))
                if header_filename and _is_archive_download(url, header_filename, content_type):
                    archive_download = True
                if header_filename and (_namespace_get(args, "name") or not stl_name):
                    if archive_download:
                        if partial_path != outpath and partial_path:
                            _remove_partial_file(partial_path)
                        if outpath and filename.startswith(".bambu-download-"):
                            _remove_partial_file(outpath)
                        archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
                            prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False
                        )
                        outpath = archive_temp.name
                        archive_temp.close()
                        filename = _portable_basename(outpath)
                        partial_path = outpath
                        replace_on_success = False
                    else:
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
                        outpath = os.path.join(outdir, filename)
                        outpath = _download_pkg._noncolliding_path(outpath)
                        filename = _portable_basename(outpath)

                logger.info(f"⬇️  Downloading {filename}...")
                if not archive_download:
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

                try:
                    with open(partial_path, "wb") as f:
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
                    _remove_partial_file(partial_path)
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
                    _remove_partial_file(partial_path)
                    message = f"Download ended early: received {downloaded} of {total_size} bytes."
                    logger.error(message)
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
                    abort("", exit_code=EXIT_NETWORK_ERROR)

            size = os.path.getsize(partial_path)
            if size <= 0:
                _remove_partial_file(partial_path)
                message = "Downloaded file is empty; refusing to use it."
                logger.error(message)
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
                abort("", exit_code=EXIT_FILE_ERROR)
            if replace_on_success:
                os.replace(partial_path, outpath)
                partial_path = None
            if archive_download:
                archive_path = outpath
                try:
                    extracted_path, extracted_filename, archive_entry, size = _extract_zip_model(
                        archive_path, outdir, args
                    )
                except OSError as exc:
                    _remove_partial_file(archive_path)
                    message = f"Failed to extract archive: {exc}"
                    logger.error(message)
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
                    abort("", exit_code=EXIT_FILE_ERROR)
                except ValueError as exc:
                    _remove_partial_file(archive_path)
                    partial_path = None
                    message = str(exc)
                    logger.error(message)
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
        logger.error(message)
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
        abort("", exit_code=EXIT_FILE_ERROR)
    except urllib.error.HTTPError as e:
        _remove_partial_file(partial_path)
        message = f"Download failed: HTTP Error {e.code} ({e.reason})"
        logger.error(message)
        if e.code == 404:
            logger.info("   The requested file or model does not exist. Check that the URL is correct.")
        elif e.code == 403:
            logger.info("   Access is forbidden. Printables or the host may be blocking automated requests.")
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
        try:
            e.close()
        except Exception:
            pass
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except urllib.error.URLError as e:
        _remove_partial_file(partial_path)
        err_msg = str(e.reason) if hasattr(e, "reason") else str(e)
        if "Security Error" in err_msg:
            message = f"SSRF Security Violation Blocked: {err_msg}"
            logger.error(message)
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
            abort("", exit_code=EXIT_COMMAND_ERROR)
        message = f"Network error during download: {e}"
        logger.error(message)
        logger.info("   Please check your internet connection or verify the domain name resolves correctly.")
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
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except OSError as e:
        _remove_partial_file(partial_path)
        message = f"Local file error during download: {_exception_for_message(e)}"
        logger.error(message)
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
        abort("", exit_code=EXIT_FILE_ERROR)
    except BambuError:
        raise
    except Exception as e:
        _remove_partial_file(partial_path)
        message = f"Download failed: {e}"
        logger.error(message)
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
        abort("", exit_code=EXIT_NETWORK_ERROR)
