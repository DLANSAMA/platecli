"""Sliced .3mf validation and slice-result finalization."""

from __future__ import annotations

import argparse
import os
import subprocess
import zipfile

from bambu_cli.cli import (
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
)
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.protocols.ftps import _remove_partial_file
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


def _finalize_slice(
    result: subprocess.CompletedProcess[str] | None,
    outpath: str,
    args: argparse.Namespace,
    filepath: str,
    step_converted: bool,
) -> str:
    """Evaluate the OrcaSlicer result, emit success/error output, and return the .3mf path."""

    # OrcaSlicer can exit non-zero on a headless GL/thumbnail step even when the slice
    # itself succeeded and a valid .3mf was written. Treat that specific case as success
    # only when the output is a real, non-corrupt 3MF package (not truncated garbage).
    _benign_rc = False
    if result is not None and result.returncode != 0 and os.path.exists(outpath):
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
    if result is not None and os.path.exists(outpath) and (result.returncode == 0 or _benign_rc):
        try:
            size = os.path.getsize(outpath)
        except OSError as exc:
            message = f"Could not read sliced output file: {_exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
            )
            abort("", exit_code=EXIT_FILE_ERROR)
        if size <= 0:
            _remove_partial_file(outpath)
            message = f"Slicing produced an empty output file: {_path_for_message(outpath)}"
            logger.error(message)
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
            abort("", exit_code=EXIT_FILE_ERROR)
        # Zero returncode also requires a real 3MF — do not trust size alone.
        if not _is_valid_sliced_3mf(outpath):
            _remove_partial_file(outpath)
            message = f"Slicing produced a corrupt or incomplete .3mf: {_path_for_message(outpath)}"
            logger.error(message)
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
            abort("", exit_code=EXIT_FILE_ERROR)
        logger.info(f"✅ Sliced: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "sliced",
                    "command": "slice",
                    "file": _expand_path(args.file),
                    "path": outpath,
                    "filename": os.path.basename(outpath),
                    "bytes": size,
                    "step_converted": step_converted,
                }
            )
        return outpath
    else:
        rc = result.returncode if result is not None else -1
        message = f"Slicing failed (RC={rc})"
        logger.error(message)
        all_output = ""
        if result is not None:
            all_output = (result.stdout or "") + (result.stderr or "")
        error_found = False
        for line in all_output.split("\n"):
            lower_line = line.lower()
            if "[error]" in lower_line or "nothing to be sliced" in lower_line or "error:" in lower_line:
                msg = line.split("] ")[-1].strip() if "] " in line else line.strip()
                if msg:
                    logger.error(f"   {msg}")
                    error_found = True

        if not error_found:
            logger.info("   Check OrcaSlicer profiles or syntax.")
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
        abort("", exit_code=EXIT_COMMAND_ERROR)
