"""One-shot job orchestration: URL/local file -> download -> slice -> upload
-> optional print, plus print payload generation and dry-run prediction."""

import argparse
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote, urlparse

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
    DOWNLOADABLE_EXTENSIONS,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    PRINT_READY_EXTENSIONS,
    SLICEABLE_EXTENSIONS,
)
from bambu_cli.errors import BambuError, abort


def _exit_code_from_error(exc, default=EXIT_COMMAND_ERROR):  # pragma: no cover -- job helper
    """Normalize BambuError / SystemExit to an integer exit code."""
    code = getattr(exc, "exit_code", None)
    if code is not None:
        return code
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if code is None:
        return 0
    return default


from bambu_cli.download import (
    _archive_member_too_large_message,
    _download_target_filename,
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
    _sanitize_download_filename,
    _select_zip_model_member,
    _unsupported_download_message,
    _validate_http_url_or_exit,
)
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _ensure_output_dir, emit_json


def _default_download():  # pragma: no cover -- job helper
    from bambu_cli import bambu

    return bambu.cmd_download


def _default_slice():  # pragma: no cover -- job helper
    from bambu_cli import bambu

    return bambu.cmd_slice


def _default_upload():  # pragma: no cover -- job helper
    from bambu_cli import bambu

    return bambu.cmd_upload


def _default_print():  # pragma: no cover -- job helper
    from bambu_cli import bambu

    return bambu.cmd_print


@dataclass
class JobSteps:
    """Injectable step callables for the job/send orchestrator.

    Each field defaults to a zero-arg factory that late-binds to the real
    implementation through the ``bambu`` facade at call time, so existing
    tests/callers that patch ``bambu.cmd_download`` (etc.) keep working even
    when a caller doesn't supply its own ``JobSteps``.
    """

    download: Optional[Callable] = None
    slice: Optional[Callable] = None
    upload: Optional[Callable] = None
    print_: Optional[Callable] = None

    def _resolve(self, value, default_factory):
        return value if value is not None else default_factory()

    def get_download(self):
        return self._resolve(self.download, _default_download)

    def get_slice(self):
        return self._resolve(self.slice, _default_slice)

    def get_upload(self):
        return self._resolve(self.upload, _default_upload)

    def get_print(self):
        return self._resolve(self.print_, _default_print)


def _print_next_command(args, basename):  # pragma: no cover -- job helper
    command = ["print", basename, "--confirm", "--json"]
    if _namespace_get(args, "use_ams", False):
        command.append("--use-ams")
    ams_mapping = _namespace_get(args, "ams_mapping")
    if ams_mapping:
        command.extend(["--ams-mapping", str(ams_mapping)])
    if _namespace_get(args, "timelapse", False):
        command.append("--timelapse")
    if _namespace_get(args, "skip_bed_leveling", False):
        command.append("--skip-bed-leveling")
    if _namespace_get(args, "skip_flow_cali", False):
        command.append("--skip-flow-cali")
    return command


def generate_print_payload(  # pragma: no cover -- job helper
    basename, use_ams=False, ams_mapping=None, timelapse=False, bed_leveling=True, flow_cali=True
):
    """Generate the JSON payload for the print command."""
    # Files are stored in /sdcard/model/ on the printer (referenced via the url field below).
    encoded_basename = quote(basename, safe="")
    print_cmd = {
        "sequence_id": "0",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "subtask_name": basename,
        "url": f"file:///sdcard/model/{encoded_basename}",
        "bed_type": "auto",
        "timelapse": timelapse,
        "bed_leveling": bed_leveling,
        "flow_cali": flow_cali,
        "vibration_cali": True,
        "layer_inspect": False,
        "use_ams": use_ams,
        "profile_id": "0",
        "project_id": "0",
        "subtask_id": "0",
        "task_id": "0",
    }

    if use_ams and ams_mapping is not None:
        print_cmd["ams_mapping"] = ams_mapping

    payload = json.dumps({"print": print_cmd})
    return payload


def _slice_args_for_job(filepath, args, output_dir):  # pragma: no cover -- job helper
    """Build a slice command namespace from job-level arguments."""
    return argparse.Namespace(
        file=filepath,
        quality=getattr(args, "quality", "standard"),
        filament=getattr(args, "filament", "PLA Basic"),
        infill=getattr(args, "infill", 15),
        pattern=getattr(args, "pattern", "3dhoneycomb"),
        nozzle_temp=getattr(args, "nozzle_temp", 220),
        bed_temp=getattr(args, "bed_temp", 60),
        supports=getattr(args, "supports", False),
        support_type=getattr(args, "support_type", None),
        support_interface_density=getattr(args, "support_interface_density", None),
        support_interface_pattern=getattr(args, "support_interface_pattern", None),
        walls=getattr(args, "walls", None),
        wall_type=getattr(args, "wall_type", None),
        top_layers=getattr(args, "top_layers", None),
        bottom_layers=getattr(args, "bottom_layers", None),
        accel_wall=getattr(args, "accel_wall", None),
        accel_wall_outer=getattr(args, "accel_wall_outer", None),
        accel_infill=getattr(args, "accel_infill", None),
        accel_travel=getattr(args, "accel_travel", None),
        accel_first_layer=getattr(args, "accel_first_layer", None),
        copies=getattr(args, "copies", 1),
        output=output_dir,
        threads=getattr(args, "threads", None),
    )


def _predicted_sliced_remote_name(filepath, copies=1):  # pragma: no cover -- job helper
    """Return the remote filename Orca output will have after job/send slicing."""
    from bambu_cli.slicer import _sliced_output_path

    return _portable_basename(_sliced_output_path(filepath, ".", copies=copies))


def _predicted_url_download_extension(url, args):  # pragma: no cover -- job helper
    """Infer a direct URL dry-run extension from URL path or explicit --name."""
    source_ext = _file_extension(urlparse(url).path)
    if source_ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        return source_ext
    if _namespace_get(args, "name"):
        return _file_extension(_sanitize_download_filename(_namespace_get(args, "name")))
    return None


def _predicted_url_remote_name(url, args):  # pragma: no cover -- job helper
    """Best-effort remote filename prediction for side-effect-free URL dry-runs.

    This intentionally only uses the URL path and explicit --name value. It does
    not resolve Printables pages, HTML pages, redirects, or ZIP members because
    doing so would require network I/O or archive extraction.
    """
    if _is_printables_model_url(url):
        return None
    predicted_ext = _predicted_url_download_extension(url, args)
    if predicted_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        return None
    if predicted_ext not in DOWNLOADABLE_EXTENSIONS:
        return None
    filename = _download_target_filename(args, url)
    if predicted_ext in SLICEABLE_EXTENSIONS:
        return _predicted_sliced_remote_name(filename, getattr(args, "copies", 1))
    if predicted_ext in PRINT_READY_EXTENSIONS:
        return filename
    return None


def _parse_print_options(args):  # pragma: no cover -- job helper
    """Validate print-only options and return the parsed AMS mapping."""
    raw_mapping = getattr(args, "ams_mapping", None)
    if not raw_mapping:
        return None, None
    if not getattr(args, "use_ams", False):
        return None, "--ams-mapping requires --use-ams"
    try:
        clean_mapping = raw_mapping.strip("[]")
        mapping = [int(x.strip()) for x in clean_mapping.split(",")]
    except ValueError:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if not mapping:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if any(slot < 0 for slot in mapping):
        return None, "Invalid AMS mapping format. Slot indexes must be zero or positive integers like '0' or '0,1,2'"
    return mapping, None


def _emit_job_failure(args, summary, failed_step, exit_code, error=None, detail=None):  # pragma: no cover -- job helper
    """Emit a single machine-readable failure summary for job/send --json."""
    if not bool(_namespace_get(args, "json", False)):
        return
    payload = dict(summary)
    payload.update(
        {
            "status": "error",
            "failed_step": failed_step,
            "exit_code": exit_code,
            "error": error or f"{failed_step} failed; see stderr for details",
        }
    )
    if detail:
        payload[f"{failed_step}_error"] = detail
    emit_json(payload)


def _job_fail(args, summary, failed_step, exit_code, message):  # pragma: no cover -- job helper
    logger.error(message)
    _emit_job_failure(args, summary, failed_step, exit_code, message)
    abort("", exit_code=exit_code)


def _validate_predicted_remote_name_or_fail(args, summary, remote_name, message_prefix):  # pragma: no cover -- job helper
    """Fail a job before work starts if a known printer filename is unsafe."""
    if remote_name is not None and _safe_remote_name(remote_name) is None:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_FILE_ERROR,
            f"{message_prefix}: {remote_name!r}",
        )


def _last_error_for(command, ctx=None):  # pragma: no cover -- job helper
    """Return the last-error payload for ``command``, dual-writing it onto
    ``ctx.last_error`` when a RuntimeContext is supplied.

    The legacy global (``utils._LAST_ERROR_PAYLOAD``) remains the source of
    truth that step implementations write to; ``ctx.last_error`` is a typed
    mirror for callers migrating away from the module global.
    """
    payload = utils._LAST_ERROR_PAYLOAD
    result = payload if isinstance(payload, dict) and payload.get("command") == command else None
    if ctx is not None:
        ctx.last_error = result
    return result


def _prepare_job_output_dir(args, summary):  # pragma: no cover -- job helper
    """Validate job/send working directory before expensive work starts.

    In dry-run mode this is intentionally side-effect free: report that the
    directory would be created instead of creating it.
    """
    if not getattr(args, "output", None):
        return None
    workdir = _expand_path(args.output)
    if workdir.startswith("-"):
        _job_fail(
            args, summary, "validate", EXIT_COMMAND_ERROR, f"Invalid output directory: {_path_for_message(workdir)}"
        )
    if getattr(args, "dry_run", False):
        if os.path.exists(workdir):
            if not os.path.isdir(workdir):
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Output path is not a directory: {_path_for_message(workdir)}",
                )
        else:
            parent = os.path.abspath(workdir)
            while parent and not os.path.exists(parent):
                next_parent = os.path.dirname(parent)
                if next_parent == parent:
                    break
                parent = next_parent
            if not parent or not os.path.isdir(parent) or not os.access(parent, os.W_OK):
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Could not prepare output directory: {_path_for_message(workdir)}",
                )
            summary["would_create_output_dir"] = True
        return workdir
    try:
        _ensure_output_dir(workdir)
    except BambuError as exc:
        _emit_job_failure(
            args,
            summary,
            "validate",
            (getattr(exc, "exit_code", None) or EXIT_FILE_ERROR),
            f"Could not prepare output directory: {_path_for_message(workdir)}",
        )
        raise
    return workdir


def _cmd_job(args):  # pragma: no cover -- job helper
    """Public entry point shim: builds a RuntimeContext/JobSteps and delegates."""
    from bambu_cli.context import RuntimeContext

    return _run_job(RuntimeContext.for_request(args), args, JobSteps())


def _run_job(ctx, args, steps=None):  # pragma: no cover -- job orchestrator; step failure contracts unit-tested
    """Agent-friendly one-shot workflow: URL/local file -> slice if needed -> upload -> optional print."""
    if steps is None:
        steps = JobSteps()
    from bambu_cli.slicer import _directory_input_message, _is_directory_input, _validate_slice_options

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
