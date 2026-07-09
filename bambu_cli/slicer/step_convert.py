"""STEP/STP → STL conversion via gmsh."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from bambu_cli.cli import _exception_for_message, _expand_path
from bambu_cli.logging_utils import logger
from bambu_cli.slicer.options import _safe_temp_prefix

GMSH_MESH_SCALE = "0.5"


def _convert_step_to_stl(
    filepath: str,
) -> tuple[str | None, bool]:  # pragma: no cover -- gmsh external process; platform matrix
    """Convert STEP to STL using gmsh."""

    filepath = os.path.abspath(_expand_path(filepath))
    stem = _safe_temp_prefix(os.path.splitext(os.path.basename(filepath))[0], fallback="converted")

    # Create a secure, owner-restricted temporary directory
    tmpdir = tempfile.mkdtemp(prefix="bambu_step_")
    stl_path = os.path.join(tmpdir, f"{stem}.stl")

    logger.info("🔄 Converting STEP → STL (OrcaSlicer CLI requires STL)...")
    try:
        cmd_args = ["gmsh", filepath, "-3", "-format", "stl", "-o", stl_path, "-clscale", GMSH_MESH_SCALE]
        if sys.platform != "win32" and shutil.which("nice"):
            cmd_args = ["nice", "-n", "10"] + cmd_args
        conv = subprocess.run(cmd_args, capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not os.path.exists(stl_path):
            if conv.stdout or conv.stderr:
                logger.error(f"STEP conversion failed (RC={conv.returncode}).")
                if conv.stdout:
                    logger.error(f"Stdout:\n{conv.stdout}")
                if conv.stderr:
                    logger.error(f"Stderr:\n{conv.stderr}")
            logger.error("STEP conversion failed.")
            try:
                os.unlink(stl_path)
            except OSError:
                pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
            return None, False

    except FileNotFoundError:
        logger.error("STEP conversion failed. Please install gmsh for your platform.")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    except subprocess.TimeoutExpired:
        logger.error("STEP conversion timed out.")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    except OSError as exc:
        logger.error(f"STEP conversion failed: {_exception_for_message(exc)}")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    try:
        size = os.path.getsize(stl_path) // 1024
    except OSError as exc:
        logger.error(f"STEP conversion failed: {_exception_for_message(exc)}")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    logger.info(f"   Converted: {os.path.basename(stl_path)} ({size}KB)")
    return stl_path, True
