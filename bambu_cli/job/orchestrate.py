"""Job/send command entry and pipeline orchestration."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import zipfile
from urllib.parse import urlparse

from bambu_cli import utils
from bambu_cli.cli import (
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
    _redact_url_credentials,
)
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    PRINT_READY_EXTENSIONS,
    SLICEABLE_EXTENSIONS,
)
from bambu_cli.context import RuntimeContext
from bambu_cli.download import (
    _archive_member_too_large_message,
    _extract_zip_model,
    _file_extension,
    _is_http_url,
    _is_printables_model_url,
    _known_unsupported_download_extension,
    _looks_like_url,
    _max_download_mb_error,
    _name_for_message,
    _normalize_url_input,
    _portable_basename,
    _safe_remote_name,
    _select_zip_model_member,
    _unsupported_download_message,
    _validate_http_url_or_exit,
)
from bambu_cli.errors import BambuError
from bambu_cli.job.payload import _parse_print_options, _print_next_command
from bambu_cli.job.predict import (
    _predicted_sliced_remote_name,
    _predicted_url_download_extension,
    _predicted_url_remote_name,
    _slice_args_for_job,
)
from bambu_cli.job.steps import JobSteps
from bambu_cli.job.support import (
    _emit_job_failure,
    _exit_code_from_error,
    _job_fail,
    _last_error_for,
    _prepare_job_output_dir,
    _validate_predicted_remote_name_or_fail,
)
from bambu_cli.logging_utils import logger
from bambu_cli.slicer import _directory_input_message, _is_directory_input, _validate_slice_options
from bambu_cli.utils import emit_json


def _cmd_job(args):
    """Public entry point shim: builds a RuntimeContext/JobSteps and delegates."""

    return _run_job(RuntimeContext.for_request(args), args, JobSteps())


def _run_job(ctx, args, steps=None):
    """Agent-friendly one-shot workflow: URL/local file -> slice if needed -> upload -> optional print."""
    if steps is None:
        steps = JobSteps()

    source_arg = args.source
    source = _normalize_url_input(source_arg)
    reported_source = _redact_url_credentials(source_arg)
    reported_normalized_source = _redact_url_credentials(source) if source != source_arg else None
    command_name = _namespace_get(args, "cmd", "job") or "job"
    summary = {
        "command": command_name,
        "source": reported_source,
        "normalized_source": reported_normalized_source,
        "downloaded_path": None,
        "extracted_path": None,
        "archive_entry": None,
        "printable_path": None,
        "remote_name": None,
        "printed": False,
        "uploaded": False,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "upload_only": bool(getattr(args, "upload_only", False)),
        "workdir": None,
        "next_command": None,
        "would_download": False,
        "would_extract": False,
        "would_slice": False,
        "would_upload": False,
        "would_print": False,
    }

    if source_arg.startswith("-"):
        _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Invalid source: {source_arg}")

    slice_option_error = _validate_slice_options(args)
    if slice_option_error:
        _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, slice_option_error)

    if getattr(args, "confirm", False) and not getattr(args, "upload_only", False):
        _, print_option_error = _parse_print_options(args)
        if print_option_error:
            _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, print_option_error)

    if _looks_like_url(source) and not _is_http_url(source):
        try:
            _validate_http_url_or_exit(source)
        except BambuError as exc:
            _emit_job_failure(
                args,
                summary,
                "validate",
                _exit_code_from_error(exc),
                f"Invalid URL source: {_redact_url_credentials(source)}",
            )
            raise

    if _is_http_url(source):
        try:
            _validate_http_url_or_exit(source)
        except BambuError as exc:
            _emit_job_failure(
                args,
                summary,
                "validate",
                _exit_code_from_error(exc),
                f"Invalid URL source: {_redact_url_credentials(source)}",
            )
            raise
        max_download_mb_error = _max_download_mb_error(args)
        if max_download_mb_error:
            _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
        predicted_remote_name = _predicted_url_remote_name(source, args)
        _validate_predicted_remote_name_or_fail(
            args,
            summary,
            predicted_remote_name,
            "Predicted printer filename is unsafe",
        )

    is_temp_workdir = False
    workdir = None

    try:
        if getattr(args, "dry_run", False) and _is_http_url(source):
            if not _is_printables_model_url(source):
                unsupported_ext = _known_unsupported_download_extension(urlparse(source).path)
                if unsupported_ext:
                    summary["extension"] = unsupported_ext
                    _job_fail(
                        args, summary, "validate", EXIT_FILE_ERROR, _unsupported_download_message(unsupported_ext)
                    )
            workdir = _prepare_job_output_dir(args, summary)
            if workdir:
                summary["workdir"] = workdir
            logger.info("🔍 Dry Run: URL source detected; skipping download, slicing, upload, and print.")
            logger.info("   Re-run without --dry-run to download and prepare the model.")
            summary["status"] = "dry_run_url_skipped"
            summary["would_download"] = True
            source_ext = _predicted_url_download_extension(source, args)
            if source_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
                summary["would_extract"] = True
            elif source_ext in SLICEABLE_EXTENSIONS:
                summary["would_slice"] = True
            summary["remote_name"] = predicted_remote_name
            summary["would_upload"] = True
            summary["would_print"] = bool(getattr(args, "confirm", False)) and not bool(
                getattr(args, "upload_only", False)
            )
            if getattr(args, "json", False):
                emit_json(summary)
            return None

        if _is_http_url(source):
            workdir = _prepare_job_output_dir(args, summary)
            if not workdir:
                workdir = tempfile.mkdtemp(prefix="bambu-job-")
                is_temp_workdir = True
            summary["workdir"] = workdir
            logger.info("🚦 Job source is a URL; downloading first.")
            summary["would_download"] = True
            try:
                utils._LAST_ERROR_PAYLOAD = None
                utils._LAST_DOWNLOAD_PAYLOAD = None
                download_path = steps.get_download()(
                    argparse.Namespace(
                        url=source,
                        output=workdir,
                        name=getattr(args, "name", None),
                        max_download_mb=getattr(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB),
                        json=False,
                        progress=not getattr(args, "json", False),
                    )
                )
            except BambuError as exc:
                detail = _last_error_for("download", ctx)
                _emit_job_failure(
                    args,
                    summary,
                    "download",
                    _exit_code_from_error(exc),
                    error=detail.get("error") if detail else None,
                    detail=detail,
                )
                raise
            source_path = download_path
            summary["downloaded_path"] = download_path
            if isinstance(utils._LAST_DOWNLOAD_PAYLOAD, dict) and utils._LAST_DOWNLOAD_PAYLOAD.get("archive_entry"):
                summary["would_extract"] = True
                summary["extracted_path"] = utils._LAST_DOWNLOAD_PAYLOAD.get("path")
                summary["archive_entry"] = utils._LAST_DOWNLOAD_PAYLOAD.get("archive_entry")
            workdir = workdir or os.path.dirname(os.path.abspath(source_path))
        else:
            source_path = _expand_path(source)
            if getattr(args, "name", None):
                logger.warning("⚠️  --name is only used for URL downloads; ignoring it for a local file.")
            if not os.path.exists(source_path):
                _job_fail(
                    args, summary, "validate", EXIT_FILE_ERROR, f"File not found: {_path_for_message(source_path)}"
                )
            if _is_directory_input(source_path):
                _job_fail(args, summary, "validate", EXIT_FILE_ERROR, _directory_input_message(source_path))

        ext = _file_extension(source_path)
        if ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
            max_download_mb_error = _max_download_mb_error(args)
            if max_download_mb_error:
                _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
        if ext in SLICEABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
            workdir = _prepare_job_output_dir(args, summary)
            if not workdir and not getattr(args, "dry_run", False):
                workdir = tempfile.mkdtemp(prefix="bambu-job-")
                is_temp_workdir = True
            if workdir:
                summary["workdir"] = workdir
        if ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
            summary["would_extract"] = True
            logger.info("🚦 Job source is a ZIP archive; extracting a model file before continuing.")
            try:
                with zipfile.ZipFile(source_path) as archive:
                    info, member_filename = _select_zip_model_member(archive)
            except zipfile.BadZipFile:
                _job_fail(args, summary, "extract", EXIT_FILE_ERROR, "ZIP archive is invalid or corrupt.")
            if info is None:
                _job_fail(
                    args,
                    summary,
                    "extract",
                    EXIT_FILE_ERROR,
                    "ZIP archive did not contain a supported model or printer-ready file.",
                )
            member_ext = _file_extension(member_filename)
            if member_ext in SLICEABLE_EXTENSIONS:
                predicted_remote_name = _predicted_sliced_remote_name(member_filename, getattr(args, "copies", 1))
            elif member_ext in PRINT_READY_EXTENSIONS:
                predicted_remote_name = member_filename
            else:
                predicted_remote_name = None
            _validate_predicted_remote_name_or_fail(
                args,
                summary,
                predicted_remote_name,
                "ZIP member would produce unsafe printer filename",
            )
            if getattr(args, "dry_run", False):
                max_bytes = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)) * 1024 * 1024
                if info.file_size > max_bytes:
                    _job_fail(
                        args,
                        summary,
                        "extract",
                        EXIT_FILE_ERROR,
                        _archive_member_too_large_message(member_filename, info.file_size, max_bytes),
                    )
                logger.info(
                    "🔍 Dry Run: ZIP archive contains a supported file; skipping extraction, slicing, upload, and print."
                )
                summary["status"] = "dry_run_local_skipped"
                summary["archive_entry"] = member_filename
                summary["would_slice"] = member_ext in SLICEABLE_EXTENSIONS
                summary["remote_name"] = predicted_remote_name
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, "confirm", False)) and not bool(
                    getattr(args, "upload_only", False)
                )
                if getattr(args, "json", False):
                    emit_json(summary)
                return source_path
            try:
                extracted_path, extracted_filename, archive_entry, _ = _extract_zip_model(
                    source_path,
                    workdir,
                    argparse.Namespace(
                        name=None, max_download_mb=getattr(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)
                    ),
                )
            except ValueError as exc:
                _job_fail(args, summary, "extract", EXIT_FILE_ERROR, str(exc))
            source_path = extracted_path
            ext = _file_extension(source_path)
            summary["extracted_path"] = extracted_path
            summary["archive_entry"] = archive_entry

        if ext in SLICEABLE_EXTENSIONS:
            summary["would_slice"] = True
            predicted_remote_name = _predicted_sliced_remote_name(source_path, getattr(args, "copies", 1))
            if _safe_remote_name(predicted_remote_name) is None:
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Sliced output would have unsafe printer filename: {_name_for_message(predicted_remote_name)!r}",
                )
            logger.info("🚦 Job source is a model file; slicing before upload.")
            if getattr(args, "dry_run", False):
                logger.info("🔍 Dry Run: local model is valid; skipping slicing, upload, and print.")
                summary["status"] = "dry_run_local_skipped"
                summary["printable_path"] = source_path
                summary["remote_name"] = predicted_remote_name
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, "confirm", False)) and not bool(
                    getattr(args, "upload_only", False)
                )
                if getattr(args, "json", False):
                    emit_json(summary)
                return source_path
            try:
                utils._LAST_ERROR_PAYLOAD = None
                printable_path = steps.get_slice()(_slice_args_for_job(source_path, args, workdir))
            except BambuError as exc:
                summary["printable_path"] = source_path
                detail = _last_error_for("slice", ctx)
                _emit_job_failure(
                    args,
                    summary,
                    "slice",
                    _exit_code_from_error(exc),
                    error=detail.get("error") if detail else None,
                    detail=detail,
                )
                raise
        elif ext in PRINT_READY_EXTENSIONS:
            remote_candidate = _portable_basename(source_path)
            if _safe_remote_name(remote_candidate) is None:
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Refusing to upload file with unsafe name: {_name_for_message(remote_candidate)!r}",
                )
            summary["remote_name"] = remote_candidate
            if not _is_http_url(source) and getattr(args, "output", None):
                logger.warning(
                    "⚠️  --output is only used when job/send downloads, extracts, or slices; ignoring it for a printer-ready local file."
                )
            logger.info("🚦 Job source is already printer-ready; upload will use it directly.")
            if getattr(args, "dry_run", False):
                try:
                    ready_size = os.path.getsize(source_path)
                except OSError as exc:
                    _job_fail(
                        args,
                        summary,
                        "validate",
                        EXIT_FILE_ERROR,
                        f"Could not read file size for {_path_for_message(source_path)}: {_exception_for_message(exc)}",
                    )
                if ready_size <= 0:
                    _job_fail(
                        args,
                        summary,
                        "validate",
                        EXIT_FILE_ERROR,
                        f"Refusing to dry-run an empty printer-ready file: {_path_for_message(source_path)}",
                    )
                logger.info("🔍 Dry Run: printer-ready file is valid; skipping upload and print.")
                summary["status"] = "dry_run_local_skipped"
                summary["printable_path"] = source_path
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, "confirm", False)) and not bool(
                    getattr(args, "upload_only", False)
                )
                if getattr(args, "json", False):
                    emit_json(summary)
                return source_path
            printable_path = source_path
        else:
            supported = ", ".join(SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
            _job_fail(
                args,
                summary,
                "validate",
                EXIT_FILE_ERROR,
                f"Unsupported source file type '{ext or 'none'}'. Supported types: {supported}",
            )

        summary["printable_path"] = printable_path
        summary["would_upload"] = True

        try:
            utils._LAST_ERROR_PAYLOAD = None
            remote_name = steps.get_upload()(
                argparse.Namespace(
                    file=printable_path, dry_run=False, json=False, progress=not getattr(args, "json", False)
                )
            )
        except BambuError as exc:
            detail = _last_error_for("upload", ctx)
            _emit_job_failure(
                args,
                summary,
                "upload",
                _exit_code_from_error(exc),
                error=detail.get("error") if detail else None,
                detail=detail,
            )
            raise
        summary["remote_name"] = remote_name
        summary["uploaded"] = True

        if getattr(args, "upload_only", False):
            logger.info(f"✅ Job uploaded {remote_name}; print not started because --upload-only was set.")
            if getattr(args, "json", False):
                summary["status"] = "uploaded"
                summary["next_command"] = _print_next_command(args, remote_name)
                emit_json(summary)
            return printable_path

        if not getattr(args, "confirm", False):
            logger.warning(f"⚠️  Job uploaded {remote_name}, but print was not started. Re-run with --confirm to print.")
            if getattr(args, "json", False):
                summary["status"] = "uploaded_not_printed"
                summary["next_command"] = _print_next_command(args, remote_name)
                emit_json(summary)
            return printable_path

        summary["would_print"] = True
        try:
            utils._LAST_ERROR_PAYLOAD = None
            steps.get_print()(
                argparse.Namespace(
                    file=remote_name,
                    confirm=True,
                    dry_run=False,
                    use_ams=getattr(args, "use_ams", False),
                    ams_mapping=getattr(args, "ams_mapping", None),
                    timelapse=getattr(args, "timelapse", False),
                    skip_bed_leveling=getattr(args, "skip_bed_leveling", False),
                    skip_flow_cali=getattr(args, "skip_flow_cali", False),
                    json=False,
                )
            )
        except BambuError as exc:
            detail = _last_error_for("print", ctx)
            summary["next_command"] = ["status", "--json"]
            summary["recovery_hint"] = (
                "Upload succeeded but print start was not confirmed. Check printer status before retrying."
            )
            _emit_job_failure(
                args,
                summary,
                "print",
                _exit_code_from_error(exc),
                error=detail.get("error") if detail else None,
                detail=detail,
            )
            raise
        summary["printed"] = True
        summary["status"] = "printed"
        if getattr(args, "json", False):
            emit_json(summary)
        return printable_path
    finally:
        if is_temp_workdir and workdir and os.path.exists(workdir) and os.environ.get("BAMBU_KEEP_WORKDIR") != "1":
            shutil.rmtree(workdir, ignore_errors=True)
