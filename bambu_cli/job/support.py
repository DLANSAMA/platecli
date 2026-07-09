"""Job failure reporting, last-error lookup, and output-dir prep."""

from __future__ import annotations

import os

from bambu_cli import utils
from bambu_cli.cli import (
    _expand_path,
    _namespace_get,
    _path_for_message,
)
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _ensure_output_dir


def _exit_code_from_error(exc, default=EXIT_COMMAND_ERROR):
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
    _safe_remote_name,
)
from bambu_cli.utils import emit_json


def _emit_job_failure(args, summary, failed_step, exit_code, error=None, detail=None):
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


def _job_fail(args, summary, failed_step, exit_code, message):
    logger.error(message)
    _emit_job_failure(args, summary, failed_step, exit_code, message)
    abort("", exit_code=exit_code)


def _validate_predicted_remote_name_or_fail(args, summary, remote_name, message_prefix):
    """Fail a job before work starts if a known printer filename is unsafe."""
    if remote_name is not None and _safe_remote_name(remote_name) is None:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_FILE_ERROR,
            f"{message_prefix}: {remote_name!r}",
        )


def _last_error_for(command, ctx=None):
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


def _prepare_job_output_dir(args, summary):
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
