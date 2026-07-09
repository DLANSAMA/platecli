"""Slice option validation, path helpers, and wall-type normalization."""

from __future__ import annotations

import argparse
import os
import re
import stat
from typing import Any

from bambu_cli.cli import _path_for_message
from bambu_cli.download.naming import _portable_basename


def _normalize_wall_type(wall_type: str | None) -> str | None:
    """Accept the old 'archaic' spelling as an alias for Orca's classic walls."""
    if wall_type == "archaic":
        return "classic"
    return wall_type


def _sliced_output_path(filepath: str, output_dir: str | None = None, copies: int = 1) -> str:

    basename = os.path.splitext(_portable_basename(filepath))[0]
    outdir = output_dir or os.path.dirname(os.path.abspath(filepath))
    outfile = f"{basename}_x{copies}_sliced.3mf" if copies > 1 else f"{basename}_sliced.3mf"
    return os.path.join(outdir, outfile)


def _is_directory_input(path: str) -> bool:
    """Return True for real directory inputs without trusting broad test mocks."""

    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except OSError:
        return False


def _directory_input_message(path: str) -> str:

    return f"Path is a directory, not a file: {_path_for_message(path)}"


def _validate_slice_options(args: argparse.Namespace) -> str | None:
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import (
        MAX_BED_TEMP_C,
        MAX_NOZZLE_TEMP_C,
        MIN_BED_TEMP_C,
        MIN_NOZZLE_TEMP_C,
    )

    copies = getattr(args, "copies", 1)
    if isinstance(copies, int) and copies < 1:
        return f"--copies must be a positive integer (got {copies})"
    infill = getattr(args, "infill", 15)
    if isinstance(infill, int) and not (0 <= infill <= 100):
        return f"--infill must be between 0 and 100 (got {infill})"
    nozzle_temp = getattr(args, "nozzle_temp", None)
    if isinstance(nozzle_temp, int) and not (MIN_NOZZLE_TEMP_C <= nozzle_temp <= MAX_NOZZLE_TEMP_C):
        return f"--nozzle-temp must be between {MIN_NOZZLE_TEMP_C} and {MAX_NOZZLE_TEMP_C} °C (got {nozzle_temp})"
    bed_temp = getattr(args, "bed_temp", None)
    if isinstance(bed_temp, int) and not (MIN_BED_TEMP_C <= bed_temp <= MAX_BED_TEMP_C):
        return f"--bed-temp must be between {MIN_BED_TEMP_C} and {MAX_BED_TEMP_C} °C (got {bed_temp})"
    wall_type = _namespace_get(args, "wall_type", None)
    if wall_type and wall_type not in ("normal", "classic", "archaic"):
        return "--wall-type must be one of: normal, classic"
    return None


def _safe_temp_prefix(value: Any, fallback: str = "tmp", max_length: int = 48) -> str:
    """Return a filesystem-safe, bounded tempfile prefix ending in '_'."""
    prefix = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(value or "")).strip(" .")
    if not prefix:
        prefix = fallback
    return f"{prefix[:max_length]}_"
