"""Job failure reporting, last-error lookup, and output-dir prep."""

from __future__ import annotations

import os

from bambu_cli import utils
from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
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


def _dir_is_writable(directory):
    """Probe whether *directory* is actually writable by creating+removing a temp
    entry in it.

    ``os.access(dir, os.W_OK)`` ignores NTFS ACLs on Windows (it only reflects the
    read-only attribute, which directories don't meaningfully carry), so an
    ACL-denied location like ``C:\\Program Files`` passes ``os.access`` but fails
    the real ``os.makedirs`` on the actual run. A create-and-remove probe matches
    real-run behaviour on every OS.

    Note: this is NOT side-effect free — it creates and deletes a
    ``.plate-writetest-*`` temp file. On the rare path where the create succeeds
    but the unlink fails, that temp file is left behind (best effort).
    """
    import tempfile

    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".plate-writetest-", dir=directory)
    except OSError:
        # PermissionError (ACL/permission denial) and every other OSError mean the
        # directory is not usably writable for the real run.
        return False
    try:
        os.close(fd)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return True


def _prepare_job_output_dir(args, summary):
    """Validate job/send working directory before expensive work starts.

    In dry-run mode this does not create the requested output directory (it
    reports that the directory *would* be created). The writability check for a
    not-yet-existing directory does briefly create+remove a ``.plate-writetest-*``
    temp file in the nearest existing ancestor (see ``_dir_is_writable``), so it
    is not strictly side-effect free.
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
            if not parent or not os.path.isdir(parent) or not _dir_is_writable(parent):
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
