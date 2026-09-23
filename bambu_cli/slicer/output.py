from __future__ import annotations

import argparse
import os
import subprocess
import zipfile
from enum import Enum, auto

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import FileError, SliceError
from bambu_cli.fsutil import _remove_partial_file
from bambu_cli.logging_utils import logger, safe_log_error
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.utils import emit_json


class SliceOutcome(Enum):
    SUCCESS = auto()
    BENIGN_GL_WARNING = auto()
    STALE_OUTPUT = auto()
    EMPTY_OUTPUT = auto()
    CORRUPT_3MF = auto()
    SLICER_ERROR = auto()


def _classify_slice_result(
    returncode: int,
    stdout_stderr: str,
    fresh: bool,
    file_exists: bool,
    file_size: int,
    is_valid_3mf: bool,
) -> SliceOutcome:
    """Pure classifier for OrcaSlicer execution outcome.

    Evaluates return code, process output text, freshness, and file validity
    to categorize the slice outcome into a discrete SliceOutcome enum.
    """
    if file_exists and not fresh:
        return SliceOutcome.STALE_OUTPUT

    blob = (stdout_stderr or "").lower()
    gl_noise = any(k in blob for k in ("glfw", "glew", "init opengl failed", "skip thumbnail"))
    real_err = ("nothing to be sliced" in blob) or ("slicing error" in blob)

    if returncode != 0:
        if file_exists and fresh and file_size > 0 and gl_noise and not real_err and is_valid_3mf:
            return SliceOutcome.BENIGN_GL_WARNING
        return SliceOutcome.SLICER_ERROR

    # returncode == 0
    if not file_exists:
        return SliceOutcome.SLICER_ERROR
    if file_size <= 0:
        return SliceOutcome.EMPTY_OUTPUT
    if not is_valid_3mf:
        return SliceOutcome.CORRUPT_3MF
    return SliceOutcome.SUCCESS


def _is_valid_sliced_3mf(path: str) -> bool:
    """Return True if *path* is a non-corrupt 3MF zip with expected members.

    A sliced Bambu/Orca .3mf is an OPC zip package. We require:
    - openable as a zip archive with no CRC errors (``testzip()`` is None)
    - ``[Content_Types].xml`` (OPC package marker)
    - either ``3D/3dmodel.model`` (core 3MF model) or a ``Metadata/plate_*.gcode``
      plate (what the printer print payload references)
    """

    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as zf:
            if zf.testzip() is not None:
                return False
            names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile, TypeError, ValueError):
        return False

    if "[Content_Types].xml" not in names:
        return False
    has_model = "3D/3dmodel.model" in names
    has_plate = any(n.startswith("Metadata/plate_") and n.endswith(".gcode") for n in names)
    return has_model or has_plate


def _output_snapshot(outpath: str) -> tuple[bool, float, int] | None:
    """Capture (exists, mtime_ns, size) of *outpath* before a slice run.

    Returned to ``_finalize_slice`` so a pre-existing sliced .3mf that OrcaSlicer
    did NOT rewrite this run is rejected instead of being accepted as fresh
    output (which could then be uploaded/printed as a stale model).
    """
    try:
        st = os.stat(outpath)
    except OSError:
        return None
    return (True, st.st_mtime_ns, st.st_size)


def _was_written_this_run(outpath: str, pre_snapshot: tuple[bool, float, int] | None) -> bool:
    """True if *outpath* is new or was modified since *pre_snapshot* was taken."""
    if pre_snapshot is None:
        return True  # nothing existed before the run; any file now is fresh
    try:
        st = os.stat(outpath)
    except OSError:
        return False
    _, pre_mtime_ns, pre_size = pre_snapshot
    # A real re-slice rewrites the file: mtime advances (and usually size changes).
    return st.st_mtime_ns != pre_mtime_ns or st.st_size != pre_size


def _finalize_slice(
    result: subprocess.CompletedProcess[str] | None,
    outpath: str,
    args: argparse.Namespace,
    filepath: str,
    step_converted: bool,
    pre_snapshot: tuple[bool, float, int] | None = None,
) -> str:
    """Evaluate the OrcaSlicer result, emit success/error output, and return the .3mf path."""
    _fresh = _was_written_this_run(outpath, pre_snapshot)
    file_exists = os.path.exists(outpath)
    file_size = 0
    if file_exists:
        try:
            file_size = os.path.getsize(outpath)
        except OSError as exc:
            message = f"Could not read sliced output file: {_exception_for_message(exc)}"
            raise FileError(
                message,
                exit_code=EXIT_FILE_ERROR,
                failed_step="slicer",
                extra={"file": filepath, "output": outpath},
            ) from exc

    is_valid = _is_valid_sliced_3mf(outpath) if file_exists and file_size > 0 else False
    rc = result.returncode if result is not None else -1
    stdout_stderr = ((result.stdout or "") + (result.stderr or "")) if result is not None else ""

    outcome = _classify_slice_result(
        returncode=rc,
        stdout_stderr=stdout_stderr,
        fresh=_fresh,
        file_exists=file_exists,
        file_size=file_size,
        is_valid_3mf=is_valid,
    )

    if outcome == SliceOutcome.STALE_OUTPUT:
        message = f"Slicing did not write a new output file; refusing to reuse the stale {_path_for_message(outpath)}"
        raise SliceError(
            message,
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="slicer",
            extra={"file": filepath, "output": outpath, "returncode": rc},
        )

    if outcome == SliceOutcome.EMPTY_OUTPUT:
        _remove_partial_file(outpath)
        message = f"Slicing produced an empty output file: {_path_for_message(outpath)}"
        raise FileError(
            message,
            exit_code=EXIT_FILE_ERROR,
            failed_step="slicer",
            extra={"file": filepath, "output": outpath, "bytes": file_size},
        )

    if outcome == SliceOutcome.CORRUPT_3MF:
        _remove_partial_file(outpath)
        message = f"Slicing produced a corrupt or incomplete .3mf: {_path_for_message(outpath)}"
        raise FileError(
            message,
            exit_code=EXIT_FILE_ERROR,
            failed_step="slicer",
            extra={"file": filepath, "output": outpath, "bytes": file_size},
        )

    if outcome in (SliceOutcome.SUCCESS, SliceOutcome.BENIGN_GL_WARNING):
        if outcome == SliceOutcome.BENIGN_GL_WARNING:
            logger.warning(
                "   OrcaSlicer exited non-zero on a headless GL/thumbnail step, but a valid .3mf was produced — continuing."
            )
        logger.info(f"✅ Sliced: {_path_for_message(outpath)} ({file_size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Slice

            emit_json(
                Slice(
                    status="sliced",
                    command="slice",
                    file=_expand_path(args.file),
                    path=outpath,
                    filename=os.path.basename(outpath),
                    bytes=file_size,
                    step_converted=step_converted,
                )
            )
        return outpath

    # outcome == SliceOutcome.SLICER_ERROR
    message = f"Slicing failed (RC={rc})"
    safe_log_error(message)
    error_found = False
    for line in stdout_stderr.split("\n"):
        lower_line = line.lower()
        if "[error]" in lower_line or "nothing to be sliced" in lower_line or "error:" in lower_line:
            msg = line.split("] ")[-1].strip() if "] " in line else line.strip()
            if msg:
                safe_log_error(f"   {msg}")
                error_found = True

    if not error_found:
        logger.info("   Check OrcaSlicer profiles or syntax.")
    raise SliceError(
        message,
        exit_code=EXIT_COMMAND_ERROR,
        failed_step="slicer",
        extra={"file": filepath, "output": outpath, "returncode": rc},
        logged=True,
    )
