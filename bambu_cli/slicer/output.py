"""Sliced .3mf validation and slice-result finalization."""

from __future__ import annotations

import argparse
import os
import subprocess
import zipfile

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import abort
from bambu_cli.fsutil import _remove_partial_file
from bambu_cli.logging_utils import logger, safe_log_error
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.utils import emit_json, emit_json_error


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

    # A pre-existing *_sliced.3mf at outpath that this run did NOT rewrite must
    # never be accepted as fresh output — otherwise an OrcaSlicer failure whose
    # message lacks the known error markers would "succeed" with a stale artifact
    # (uploadable/printable). Require the file to be newly written this run.
    _fresh = _was_written_this_run(outpath, pre_snapshot)

    # OrcaSlicer can exit non-zero on a headless GL/thumbnail step even when the slice
    # itself succeeded and a valid .3mf was written. Treat that specific case as success
    # only when the output is a real, non-corrupt 3MF package (not truncated garbage).
    _benign_rc = False
    if result is not None and result.returncode != 0 and _fresh and os.path.exists(outpath):
        try:
            _ok_size = os.path.getsize(outpath) > 0
        except OSError:
            _ok_size = False
        _blob = ((result.stdout or "") + (result.stderr or "")).lower()
        _gl_noise = any(k in _blob for k in ("glfw", "glew", "init opengl failed", "skip thumbnail"))
        _real_err = ("nothing to be sliced" in _blob) or ("slicing error" in _blob)
        _benign_rc = _ok_size and _gl_noise and not _real_err and _is_valid_sliced_3mf(outpath)
        if _benign_rc:
            logger.warning(
                "   OrcaSlicer exited non-zero on a headless GL/thumbnail step, but a valid .3mf was produced — continuing."
            )
    # Reject a pre-existing output that this run did not rewrite. Without this,
    # rc==0-with-no-write (OrcaSlicer exits 0 but never exports over an old file)
    # or a benign-GL non-zero exit would accept a STALE *_sliced.3mf as fresh.
    if result is not None and os.path.exists(outpath) and not _fresh:
        message = f"Slicing did not write a new output file; refusing to reuse the stale {_path_for_message(outpath)}"
        emit_json_error(
            args,
            "slice",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="slicer",
            file=filepath,
            output=outpath,
            returncode=(result.returncode if result is not None else -1),
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_COMMAND_ERROR)
    if result is not None and os.path.exists(outpath) and _fresh and (result.returncode == 0 or _benign_rc):
        try:
            size = os.path.getsize(outpath)
        except OSError as exc:
            message = f"Could not read sliced output file: {_exception_for_message(exc)}"
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
            )
            safe_log_error(message)
            abort("", exit_code=EXIT_FILE_ERROR)
        if size <= 0:
            _remove_partial_file(outpath)
            message = f"Slicing produced an empty output file: {_path_for_message(outpath)}"
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
                bytes=size,
            )
            safe_log_error(message)
            abort("", exit_code=EXIT_FILE_ERROR)
        # Zero returncode also requires a real 3MF — do not trust size alone.
        if not _is_valid_sliced_3mf(outpath):
            _remove_partial_file(outpath)
            message = f"Slicing produced a corrupt or incomplete .3mf: {_path_for_message(outpath)}"
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
                bytes=size,
            )
            safe_log_error(message)
            abort("", exit_code=EXIT_FILE_ERROR)
        logger.info(f"✅ Sliced: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Slice

            emit_json(
                Slice(
                    status="sliced",
                    command="slice",
                    file=_expand_path(args.file),
                    path=outpath,
                    filename=os.path.basename(outpath),
                    bytes=size,
                    step_converted=step_converted,
                )
            )
        return outpath
    else:
        rc = result.returncode if result is not None else -1
        message = f"Slicing failed (RC={rc})"
        emit_json_error(
            args,
            "slice",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="slicer",
            file=filepath,
            output=outpath,
            returncode=rc,
        )
        safe_log_error(message)
        all_output = ""
        if result is not None:
            all_output = (result.stdout or "") + (result.stderr or "")
        error_found = False
        for line in all_output.split("\n"):
            lower_line = line.lower()
            if "[error]" in lower_line or "nothing to be sliced" in lower_line or "error:" in lower_line:
                msg = line.split("] ")[-1].strip() if "] " in line else line.strip()
                if msg:
                    safe_log_error(f"   {msg}")
                    error_found = True

        if not error_found:
            logger.info("   Check OrcaSlicer profiles or syntax.")
        abort("", exit_code=EXIT_COMMAND_ERROR)
