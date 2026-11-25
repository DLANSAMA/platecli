"""Job/send command entry and pipeline orchestration."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from bambu_cli.argutils import namespace_get as _namespace_get
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
    DownloadResult,
    _archive_member_too_large_message,
    _extract_zip_model,
    _file_extension,
    _is_http_url,
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
from bambu_cli.gcode_limits import file_heater_problem, zip_member_heater_problem
from bambu_cli.job.payload import _parse_print_options, _print_next_command
from bambu_cli.job.plan import plan_fingerprint
from bambu_cli.job.predict import (
    _ext_would_slice,
    _path_would_slice,
    _predicted_download_slice_extension,
    _predicted_sliced_remote_name,
    _predicted_url_remote_name,
    _slice_args_for_job,
)
from bambu_cli.job.steps import JobSteps
from bambu_cli.job.support import (
    _choose_plate,
    _emit_job_failure,
    _emit_job_ok,
    _exit_code_from_error,
    _job_fail,
    _last_error_for,
    _prepare_job_output_dir,
    _validate_predicted_remote_name_or_fail,
)
from bambu_cli.jsonio import redact_url_credentials as _redact_url_credentials
from bambu_cli.logging_utils import logger
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.printables import is_printables_url
from bambu_cli.slicer import SliceResult, _directory_input_message, _is_directory_input, _validate_slice_options
from bambu_cli.slicer.orientation import asks_to_turn, presliced_refusal


@dataclass
class _JobState:
    """Typed pipeline state carried between ``_run_job`` stages."""

    ctx: RuntimeContext
    args: argparse.Namespace
    steps: JobSteps
    source_arg: str
    source: str
    summary: dict[str, Any]
    orientation_requested: bool = False
    predicted_url_remote_name: str | None = None
    workdir: str | None = None
    source_path: str = ""
    ext: str = ""
    extracted_path: str | None = None
    printable_path: str | None = None
    # Every auto-created bambu-job-* dir; ALL are cleaned up in finally, no matter how workdir is reassigned.
    temp_dirs: list[str] = field(default_factory=list)


def _cmd_job(args, steps):
    """Public entry point shim: builds a RuntimeContext and delegates.

    ``steps`` is supplied by the caller — ``bambu_cli.commands.cmd_job`` is the
    composition root that knows the real handlers. This module must not import
    them (see scripts/check_layers.py).
    """

    return _run_job(RuntimeContext.for_request(args), args, steps)


def _init_job_state(ctx: RuntimeContext, args: argparse.Namespace, steps: JobSteps | None) -> _JobState:
    source_arg = args.source
    source = _normalize_url_input(source_arg)
    reported_source = _redact_url_credentials(source_arg)
    reported_normalized_source = _redact_url_credentials(source) if source != source_arg else None
    command_name = _namespace_get(args, "cmd", "job") or "job"
    summary: dict[str, Any] = {
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
    return _JobState(
        ctx=ctx,
        args=args,
        steps=steps if steps is not None else JobSteps(),
        source_arg=source_arg,
        source=source,
        summary=summary,
    )


def _validate_url_source(state: _JobState) -> None:
    try:
        _validate_http_url_or_exit(state.source)
    except BambuError as exc:
        _emit_job_failure(
            state.args,
            state.summary,
            "validate",
            _exit_code_from_error(exc),
            f"Invalid URL source: {_redact_url_credentials(state.source)}",
        )
        raise


def _validate_job_inputs(state: _JobState) -> None:
    args = state.args
    summary = state.summary
    if state.source_arg.startswith("-"):
        _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Invalid source: {state.source_arg}")

    slice_option_error = _validate_slice_options(args)
    if slice_option_error:
        _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, slice_option_error)
    try:
        state.orientation_requested = asks_to_turn(args)
    except BambuError as exc:
        _job_fail(args, summary, "validate", exc.exit_code, str(exc))

    # Validate print options on every path, not just `--confirm`. `--dry-run` is
    # documented as no-side-effect validation and is the intended agent pre-check, so
    # it must not report success for flags that will later be rejected. `--upload-only`
    # emits a next_command carrying these same flags, so an invalid combination has to
    # fail here rather than when the caller runs the command we just handed them.
    # `_parse_print_options` returns no error when neither flag was supplied.
    _, print_option_error = _parse_print_options(args)
    if print_option_error:
        _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, print_option_error)

    if _looks_like_url(state.source) or _is_http_url(state.source):
        _validate_url_source(state)
        max_download_mb_error = _max_download_mb_error(args)
        if max_download_mb_error:
            _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
        state.predicted_url_remote_name = _predicted_url_remote_name(state.source, args)
        _validate_predicted_remote_name_or_fail(
            args,
            summary,
            state.predicted_url_remote_name,
            "Predicted printer filename is unsafe",
        )

    summary["plan_fingerprint"] = plan_fingerprint(state.ctx, args, state.source)
    expected_plan = _namespace_get(args, "expect_plan")
    if expected_plan is not None and str(expected_plan).strip().lower() != summary["plan_fingerprint"]:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_COMMAND_ERROR,
            "The plan changed since it was reviewed: nothing was downloaded, sliced, uploaded or printed. "
            f"Expected plan {str(expected_plan).strip().lower()}, this run is plan {summary['plan_fingerprint']}. "
            "Re-run with --dry-run, review it, and pass the new plan_fingerprint to --expect-plan.",
        )


def _ensure_workdir(state: _JobState, *, allow_temp: bool) -> str | None:
    if not state.workdir:
        state.workdir = _prepare_job_output_dir(state.args, state.summary)
        if not state.workdir and allow_temp:
            state.workdir = tempfile.mkdtemp(prefix="bambu-job-")
            state.temp_dirs.append(state.workdir)
    if state.workdir:
        state.summary["workdir"] = state.workdir
    return state.workdir


def _require_single_plate_for_slice(state: _JobState) -> None:
    if _namespace_get(state.args, "plate") not in (None, 1):
        _job_fail(
            state.args,
            state.summary,
            "validate",
            EXIT_COMMAND_ERROR,
            "--plate only applies to a pre-sliced 3MF; a model platecli slices has a single plate (1).",
        )


def _require_nonempty_local_file(state: _JobState, path: str, *, kind: str) -> None:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        _job_fail(
            state.args,
            state.summary,
            "validate",
            EXIT_FILE_ERROR,
            f"Could not read file size for {_path_for_message(path)}: {_exception_for_message(exc)}",
        )
    if size <= 0:
        _job_fail(
            state.args,
            state.summary,
            "validate",
            EXIT_FILE_ERROR,
            f"Refusing to dry-run an empty {kind}: {_path_for_message(path)}",
        )


def _complete_dry_run(
    state: _JobState,
    *,
    status: str,
    log_lines: tuple[str, ...],
    updates: dict[str, Any] | None = None,
) -> None:
    for line in log_lines:
        logger.info(line)
    state.summary["status"] = status
    if updates:
        state.summary.update(updates)
    state.summary["would_upload"] = True
    state.summary["would_print"] = bool(getattr(state.args, "confirm", False)) and not bool(
        getattr(state.args, "upload_only", False)
    )
    if getattr(state.args, "json", False):
        _emit_job_ok(state.summary)


def _dry_run_url_source(state: _JobState) -> None:
    args = state.args
    summary = state.summary
    if not is_printables_url(state.source):
        unsupported_ext = _known_unsupported_download_extension(urlparse(state.source).path)
        if unsupported_ext:
            summary["extension"] = unsupported_ext
            _job_fail(args, summary, "validate", EXIT_FILE_ERROR, _unsupported_download_message(unsupported_ext))
    _ensure_workdir(state, allow_temp=False)
    summary["would_download"] = True
    # Predict the extension the download will actually hand to the pipeline, then
    # decide via the SAME predicate the real run uses (`_ext_would_slice`).
    # `_predicted_download_slice_extension` mirrors the downloader's own fallback
    # to ".stl" for sources whose extension is unknowable offline (Printables
    # pages, ?id= links), so would_slice matches what the real run does.
    source_ext = _predicted_download_slice_extension(state.source, args)
    if source_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        summary["would_extract"] = True
    elif _ext_would_slice(source_ext):
        summary["would_slice"] = True
        _require_single_plate_for_slice(state)
    _complete_dry_run(
        state,
        status="dry_run_url_skipped",
        log_lines=(
            "🔍 Dry Run: URL source detected; skipping download, slicing, upload, and print.",
            "   Re-run without --dry-run to download and prepare the model.",
        ),
        updates={"remote_name": state.predicted_url_remote_name},
    )


def _download_url_source(state: _JobState) -> None:
    args = state.args
    summary = state.summary
    workdir = _ensure_workdir(state, allow_temp=True)
    logger.info("🚦 Job source is a URL; downloading first.")
    summary["would_download"] = True
    try:
        download_result = state.steps.get_download()(
            argparse.Namespace(
                url=state.source,
                output=workdir,
                name=getattr(args, "name", None),
                max_download_mb=getattr(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB),
                json=False,
                progress=not getattr(args, "json", False),
            )
        )
    except BambuError as exc:
        detail = _last_error_for("download", state.ctx, exc)
        _emit_job_failure(
            args,
            summary,
            "download",
            _exit_code_from_error(exc),
            error=detail.get("error") if detail else None,
            detail=detail,
        )
        raise
    download_path = str(download_result) if isinstance(download_result, DownloadResult) else download_result
    state.source_path = download_path
    summary["downloaded_path"] = download_path
    if isinstance(download_result, DownloadResult) and download_result.archive_entry:
        summary["would_extract"] = True
        summary["extracted_path"] = download_result.extracted_path or download_path
        summary["archive_entry"] = download_result.archive_entry
    state.workdir = state.workdir or os.path.dirname(os.path.abspath(state.source_path))


def _resolve_local_source(state: _JobState) -> None:
    source_path = _expand_path(state.source)
    if getattr(state.args, "name", None):
        logger.warning("⚠️  --name is only used for URL downloads; ignoring it for a local file.")
    if not os.path.exists(source_path):
        _job_fail(
            state.args, state.summary, "validate", EXIT_FILE_ERROR, f"File not found: {_path_for_message(source_path)}"
        )
    if _is_directory_input(source_path):
        _job_fail(state.args, state.summary, "validate", EXIT_FILE_ERROR, _directory_input_message(source_path))
    state.source_path = source_path


def _resolve_source_path(state: _JobState) -> None:
    if _is_http_url(state.source):
        _download_url_source(state)
    else:
        _resolve_local_source(state)

    state.ext = _file_extension(state.source_path)
    if state.ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        max_download_mb_error = _max_download_mb_error(state.args)
        if max_download_mb_error:
            _job_fail(state.args, state.summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
    if state.ext in SLICEABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS or _path_would_slice(
        state.source_path, state.ext
    ):
        _ensure_workdir(state, allow_temp=not getattr(state.args, "dry_run", False))


def _inspect_zip_member(state: _JobState) -> tuple[zipfile.ZipInfo, str]:
    try:
        with zipfile.ZipFile(state.source_path) as archive:
            info, member_filename = _select_zip_model_member(archive)
    except zipfile.BadZipFile:
        _job_fail(state.args, state.summary, "extract", EXIT_FILE_ERROR, "ZIP archive is invalid or corrupt.")
    except OSError as exc:
        # An unreadable/permission-denied/IO-erroring zip must stay inside
        # the structured job-failure contract (failed_step="extract"), not
        # escape to cli.py's generic handler that drops the job summary.
        _job_fail(
            state.args,
            state.summary,
            "extract",
            EXIT_FILE_ERROR,
            f"Could not read ZIP archive: {_exception_for_message(exc)}",
        )
    if info is None:
        _job_fail(
            state.args,
            state.summary,
            "extract",
            EXIT_FILE_ERROR,
            "ZIP archive did not contain a supported model or printer-ready file.",
        )
    return info, member_filename


def _dry_run_zip_member(
    state: _JobState,
    info: zipfile.ZipInfo,
    member_filename: str,
    member_ext: str,
    predicted_remote_name: str | None,
) -> None:
    args = state.args
    summary = state.summary
    max_bytes = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)) * 1024 * 1024
    if info.file_size > max_bytes:
        _job_fail(
            args,
            summary,
            "extract",
            EXIT_FILE_ERROR,
            _archive_member_too_large_message(member_filename, info.file_size, max_bytes),
        )
    # Symmetric 0-byte guard with the non-ZIP dry-run branches. A 0-byte
    # member is already filtered by _select_zip_model_member, but guard
    # explicitly so a knowable-offline empty input never reports success.
    if info.file_size <= 0:
        _job_fail(
            args,
            summary,
            "extract",
            EXIT_FILE_ERROR,
            f"Refusing to dry-run an empty ZIP member: {member_filename}",
        )
    # The real run's upload refuses a hot member; the dry run must say so too.
    heater = zip_member_heater_problem(state.source_path, info)
    if heater:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_FILE_ERROR,
            f"Refusing to upload {os.path.basename(member_filename)}: {heater}",
        )
    _complete_dry_run(
        state,
        status="dry_run_local_skipped",
        log_lines=(
            "🔍 Dry Run: ZIP archive contains a supported file; skipping extraction, slicing, upload, and print.",
        ),
        updates={
            "archive_entry": member_filename,
            "would_slice": _ext_would_slice(member_ext),
            "remote_name": predicted_remote_name,
        },
    )


def _handle_zip_source(state: _JobState) -> bool:
    """Validate and optionally extract a local ZIP source. Returns True when dry-run completed."""
    args = state.args
    summary = state.summary
    summary["would_extract"] = True
    logger.info("🚦 Job source is a ZIP archive; extracting a model file before continuing.")
    info, member_filename = _inspect_zip_member(state)
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
    # --copies only multiplies models during slicing; a printer-ready ZIP
    # member (.3mf/.gcode) prints once. Warn + flag, symmetric with the
    # non-ZIP printer-ready branch. (Set before the dry-run emit so the
    # dry-run summary carries it too.)
    if member_ext in PRINT_READY_EXTENSIONS and getattr(args, "copies", 1) != 1:
        logger.warning(
            "⚠️  --copies only applies when job slices a model; this printer-ready ZIP member will print once."
        )
        summary["copies_ignored"] = True
    if getattr(args, "dry_run", False):
        _dry_run_zip_member(state, info, member_filename, member_ext, predicted_remote_name)
        return True
    try:
        extracted_path, _extracted_filename, archive_entry, _ = _extract_zip_model(
            state.source_path,
            state.workdir,
            argparse.Namespace(name=None, max_download_mb=getattr(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)),
        )
    except ValueError as exc:
        _job_fail(args, summary, "extract", EXIT_FILE_ERROR, str(exc))
    except OSError as exc:
        # Disk-full / permission error while writing the extracted member
        # must also route through the structured failure contract.
        _job_fail(
            args,
            summary,
            "extract",
            EXIT_FILE_ERROR,
            f"Could not extract ZIP member: {_exception_for_message(exc)}",
        )
    state.source_path = extracted_path
    state.ext = _file_extension(state.source_path)
    state.extracted_path = extracted_path
    summary["extracted_path"] = extracted_path
    summary["archive_entry"] = archive_entry
    return False


def _prepare_sliceable_source(state: _JobState) -> bool:
    """Validate and slice a model source. Returns True when dry-run completed."""
    args = state.args
    summary = state.summary
    summary["would_slice"] = True
    _require_single_plate_for_slice(state)
    predicted_remote_name = _predicted_sliced_remote_name(state.source_path, getattr(args, "copies", 1))
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
        # Symmetry with the printer-ready dry-run branch: a 0-byte/unreadable
        # model is knowable offline and the real run deterministically fails
        # (OrcaSlicer errors on an empty STL), so dry-run must not report
        # success for it.
        _require_nonempty_local_file(state, state.source_path, kind="model file")
        _complete_dry_run(
            state,
            status="dry_run_local_skipped",
            log_lines=("🔍 Dry Run: local model is valid; skipping slicing, upload, and print.",),
            updates={"printable_path": state.source_path, "remote_name": predicted_remote_name},
        )
        return True
    try:
        slice_ns = _slice_args_for_job(state.source_path, args, state.workdir)
        slice_result = state.steps.get_slice()(slice_ns)
    except BambuError as exc:
        summary["printable_path"] = state.source_path
        detail = _last_error_for("slice", state.ctx, exc)
        _emit_job_failure(
            args,
            summary,
            "slice",
            _exit_code_from_error(exc),
            error=detail.get("error") if detail else None,
            detail=detail,
        )
        raise
    if isinstance(slice_result, SliceResult):
        state.printable_path = str(slice_result)
        if slice_result.orientation:
            summary["orientation"] = slice_result.orientation
    else:
        state.printable_path = slice_result
    return False


def _prepare_presliced_source(state: _JobState) -> bool:
    """Validate a printer-ready source. Returns True when dry-run completed."""
    args = state.args
    summary = state.summary
    if state.orientation_requested:
        # The pose of a sliced file is fixed in its G-code; turning it
        # would need a re-slice, which job never does behind your back.
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_COMMAND_ERROR,
            str(presliced_refusal(_portable_basename(state.source_path))),
        )
    remote_candidate = _portable_basename(state.source_path)
    if _safe_remote_name(remote_candidate) is None:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_FILE_ERROR,
            f"Refusing to upload file with unsafe name: {_name_for_message(remote_candidate)!r}",
        )
    summary["remote_name"] = remote_candidate
    if state.ext == ".3mf":
        summary["plate"] = _choose_plate(args, summary, state.source_path)
    if not _is_http_url(state.source) and not state.extracted_path and getattr(args, "output", None):
        logger.warning(
            "⚠️  --output is only used when job/send downloads, extracts, or slices; ignoring it for a printer-ready local file."
        )
    if getattr(args, "copies", 1) != 1:
        # --copies only multiplies models during slicing; a printer-ready
        # .3mf/.gcode already fixes its plate layout, so it prints once.
        # Warn (and flag in the dry-run summary) instead of silently
        # printing 1 when N were requested.
        logger.warning("⚠️  --copies only applies when job slices a model; this printer-ready file will print once.")
        summary["copies_ignored"] = True
    logger.info("🚦 Job source is already printer-ready; upload will use it directly.")
    if getattr(args, "dry_run", False):
        _require_nonempty_local_file(state, state.source_path, kind="printer-ready file")
        heater = file_heater_problem(state.source_path)
        if heater:
            # The real run's upload refuses this file; the dry run must say so.
            _job_fail(
                args,
                summary,
                "validate",
                EXIT_FILE_ERROR,
                f"Refusing to upload {os.path.basename(state.source_path)}: {heater}",
            )
        _complete_dry_run(
            state,
            status="dry_run_local_skipped",
            log_lines=("🔍 Dry Run: printer-ready file is valid; skipping upload and print.",),
            updates={"printable_path": state.source_path},
        )
        return True
    state.printable_path = state.source_path
    return False


def _upload_printable(state: _JobState) -> str:
    args = state.args
    summary = state.summary
    summary["printable_path"] = state.printable_path
    summary["would_upload"] = True
    try:
        remote_name = state.steps.get_upload()(
            argparse.Namespace(
                file=state.printable_path,
                dry_run=False,
                json=False,
                progress=not getattr(args, "json", False),
            )
        )
    except BambuError as exc:
        detail = _last_error_for("upload", state.ctx, exc)
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
    printer = getattr(state.ctx, "_printer", None)
    if printer is not None and printer.last_size_verified is not None:
        summary["size_verified"] = printer.last_size_verified
    return remote_name


def _dispatch_print(state: _JobState, remote_name: str) -> str | None:
    args = state.args
    summary = state.summary
    if getattr(args, "upload_only", False):
        logger.info(f"✅ Job uploaded {remote_name}; print not started because --upload-only was set.")
        if getattr(args, "json", False):
            summary["status"] = "uploaded"
            summary["next_command"] = _print_next_command(args, remote_name, plate=summary.get("plate"))
            _emit_job_ok(summary)
        return state.printable_path

    if not getattr(args, "confirm", False):
        logger.warning(f"⚠️  Job uploaded {remote_name}, but print was not started. Re-run with --confirm to print.")
        if getattr(args, "json", False):
            summary["status"] = "uploaded_not_printed"
            summary["next_command"] = _print_next_command(args, remote_name, plate=summary.get("plate"))
            _emit_job_ok(summary)
        return state.printable_path

    summary["would_print"] = True
    try:
        state.steps.get_print()(
            argparse.Namespace(
                file=remote_name,
                confirm=True,
                dry_run=False,
                use_ams=getattr(args, "use_ams", False),
                ams_mapping=getattr(args, "ams_mapping", None),
                timelapse=getattr(args, "timelapse", False),
                skip_bed_leveling=getattr(args, "skip_bed_leveling", False),
                skip_flow_cali=getattr(args, "skip_flow_cali", False),
                plate=summary.get("plate") or getattr(args, "plate", None),
                json=False,
            )
        )
    except BambuError as exc:
        detail = _last_error_for("print", state.ctx, exc)
        if detail and detail.get("sent") is True:
            # The print command went out unacknowledged: it may be printing,
            # so "printed: false" would invite a retry and a second print.
            summary["printed"] = None
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
        _emit_job_ok(summary)
    return state.printable_path


def _run_job(ctx, args, steps=None):
    """Agent-friendly one-shot workflow: URL/local file -> slice if needed -> upload -> optional print."""
    state = _init_job_state(ctx, args, steps)
    _validate_job_inputs(state)

    try:
        if getattr(args, "dry_run", False) and _is_http_url(state.source):
            _dry_run_url_source(state)
            return None

        _resolve_source_path(state)
        if state.ext in ARCHIVE_DOWNLOAD_EXTENSIONS and _handle_zip_source(state):
            return state.source_path

        if _path_would_slice(state.source_path, state.ext):
            if _prepare_sliceable_source(state):
                return state.source_path
        elif state.ext in PRINT_READY_EXTENSIONS:
            if _prepare_presliced_source(state):
                return state.source_path
        else:
            supported = ", ".join(SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
            _job_fail(
                args,
                state.summary,
                "validate",
                EXIT_FILE_ERROR,
                f"Unsupported source file type '{state.ext or 'none'}'. Supported types: {supported}",
            )

        remote_name = _upload_printable(state)
        return _dispatch_print(state, remote_name)
    finally:
        if os.environ.get("BAMBU_KEEP_WORKDIR") != "1":
            for temp_dir in state.temp_dirs:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
