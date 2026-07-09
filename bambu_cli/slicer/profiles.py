"""OrcaSlicer profile discovery, diagnostics, and temp-profile creation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import IO

from bambu_cli.cli import _display_path, _expand_path
from bambu_cli.logging_utils import logger
from bambu_cli.slicer.options import _normalize_wall_type


def _profiles_dir_diagnostic(profiles_dir):
    """Return ``(hint_or_None, detected_dir_or_None)`` for a bad profiles dir.

    ``detected_dir`` is a real, *different* OrcaSlicer BBL profiles directory
    found on disk that the user should point ``profiles_dir`` at when the
    configured one is unusable. Mirrors the binary-missing detection hint so
    profile errors are just as actionable, including in ``--json`` mode.
    """
    from bambu_cli.config import detect_profiles_dir

    detected = detect_profiles_dir()
    if detected and detected != profiles_dir:
        hint = f'Detected OrcaSlicer profiles at {_display_path(detected)} — set "profiles_dir" to this in config.json.'
        return hint, detected
    return None, detected


def _slicer_executable_problem(path: str | None) -> str | None:
    """Return a human-readable OrcaSlicer path problem, or None when usable."""

    if path is None:
        return "OrcaSlicer path not specified in configuration."
    expanded = _expand_path(path)
    display = _display_path(expanded)
    if not os.path.exists(expanded):
        return f"OrcaSlicer not found at {display}"
    if sys.platform != "win32" and not os.access(expanded, os.X_OK):
        return (
            f"OrcaSlicer is not executable at {display}; run `chmod +x {display}` or update orca_slicer in config.json."
        )
    return None


def _process_profile_compatible(path: str, compatible_printer: str | None) -> bool:
    if not compatible_printer:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    compatible = data.get("compatible_printers")
    if not isinstance(compatible, list):
        return False
    return compatible_printer in compatible


def _discover_process_profile(
    quality_arg: str,
    quality_map: dict[str, str],
    model_code: str = "P1P",
    compatible_printer: str | None = None,
    profiles_dir: str | None = None,
) -> str | None:
    """Discover a matching process profile."""
    if profiles_dir is None:
        from bambu_cli.context import current_settings

        profiles_dir = current_settings().profiles_dir
    layer_height = (
        quality_arg
        if quality_arg.startswith("0.")
        else quality_map.get(quality_arg, f"0.20mm Standard @BBL {model_code}").split(" ")[0]
    )
    proc_dir = os.path.join(profiles_dir, "process")
    if os.path.isdir(proc_dir):
        files = os.listdir(proc_dir)
        process_file = next(
            (f for f in files if f.startswith(layer_height) and model_code in f and "nozzle" not in f), None
        )
        if not process_file and compatible_printer:
            process_file = next(
                (
                    f
                    for f in files
                    if f.startswith(layer_height)
                    and "nozzle" not in f
                    and _process_profile_compatible(os.path.join(proc_dir, f), compatible_printer)
                ),
                None,
            )
        if process_file:
            logger.debug(f"Profile auto-discovered: {process_file}")
            return os.path.join(proc_dir, process_file)
        else:
            # Fall back to standard 0.20mm for this model
            process_file = next(
                (f for f in files if f.startswith("0.20mm") and model_code in f and "nozzle" not in f), None
            )
            if not process_file and compatible_printer:
                process_file = next(
                    (
                        f
                        for f in files
                        if f.startswith("0.20mm")
                        and "nozzle" not in f
                        and _process_profile_compatible(os.path.join(proc_dir, f), compatible_printer)
                    ),
                    None,
                )
            if process_file:
                logger.warning(f"⚠️  Requested quality not found, using: {process_file}")
                return os.path.join(proc_dir, process_file)
            else:
                # If still not found, try falling back to P1P standard
                process_file = next(
                    (f for f in files if f.startswith("0.20mm") and "P1P" in f and "nozzle" not in f), None
                )
                if process_file:
                    logger.warning(f"⚠️  Requested quality/model profile not found, falling back to: {process_file}")
                    return os.path.join(proc_dir, process_file)
                else:
                    logger.error(f"No slicer profiles found in {proc_dir}")
                    return None
    return None


def _create_temp_profiles(process: str, filament: str, args: argparse.Namespace) -> tuple[IO[str], IO[str]]:
    """Create temporary process and filament profiles with overrides."""
    infill = getattr(args, "infill", 15)
    pattern = getattr(args, "pattern", "3dhoneycomb")
    supports = getattr(args, "supports", False)
    nozzle_temp = getattr(args, "nozzle_temp", 220)
    bed_temp = getattr(args, "bed_temp", 60)
    support_type = getattr(args, "support_type", None)
    support_interface_density = getattr(args, "support_interface_density", None)
    walls = getattr(args, "walls", None)
    wall_type = getattr(args, "wall_type", None)
    top_layers = getattr(args, "top_layers", None)
    bottom_layers = getattr(args, "bottom_layers", None)
    support_interface_pattern = getattr(args, "support_interface_pattern", None)
    accel_wall = getattr(args, "accel_wall", None)
    accel_wall_outer = getattr(args, "accel_wall_outer", None)
    accel_infill = getattr(args, "accel_infill", None)
    accel_travel = getattr(args, "accel_travel", None)
    accel_first_layer = getattr(args, "accel_first_layer", None)

    created: list[IO[str]] = []
    try:
        tmp_process = tempfile.NamedTemporaryFile(  # noqa: SIM115 — handle outlives block; cleaned up by caller
            mode="w", suffix=".json", delete=False, prefix="proc_", encoding="utf-8"
        )
        created.append(tmp_process)
        with open(process, encoding="utf-8") as f:
            proc_data = json.load(f)
        proc_data["sparse_infill_density"] = f"{infill}%"
        proc_data["sparse_infill_pattern"] = pattern
        proc_data["enable_support"] = "1" if supports else "0"

        if support_type:
            proc_data["support_style"] = support_type
        if support_interface_density is not None:
            proc_data["support_interface_density"] = f"{support_interface_density}%"
        if support_interface_pattern:
            proc_data["support_interface_pattern"] = support_interface_pattern
        if walls is not None:
            proc_data["wall_loops"] = str(walls)
        if wall_type:
            wall_type = _normalize_wall_type(wall_type)
            proc_data["wall_generator"] = "arachne" if wall_type == "normal" else "classic"
        if top_layers is not None:
            proc_data["top_shell_layers"] = str(top_layers)
        if bottom_layers is not None:
            proc_data["bottom_shell_layers"] = str(bottom_layers)

        # Acceleration settings
        if accel_wall is not None:
            proc_data["inner_wall_acceleration"] = str(accel_wall)
        if accel_wall_outer is not None:
            proc_data["outer_wall_acceleration"] = str(accel_wall_outer)
        if accel_infill is not None:
            proc_data["sparse_infill_acceleration"] = str(accel_infill)
        if accel_travel is not None:
            proc_data["travel_acceleration"] = str(accel_travel)
        if accel_first_layer is not None:
            proc_data["initial_layer_acceleration"] = str(accel_first_layer)

        json.dump(proc_data, tmp_process)
        tmp_process.close()

        # Copy filament profile and merge overrides
        tmp_filament = tempfile.NamedTemporaryFile(  # noqa: SIM115 — handle outlives block; cleaned up by caller
            mode="w", suffix=".json", delete=False, prefix="fil_", encoding="utf-8"
        )
        created.append(tmp_filament)
        with open(filament, encoding="utf-8") as f:
            fil_data = json.load(f)

        nozzle_temp_str_list = [str(nozzle_temp)]
        fil_data["nozzle_temperature"] = nozzle_temp_str_list
        fil_data["nozzle_temperature_initial_layer"] = nozzle_temp_str_list

        bed_temp_str_list = [str(bed_temp)]
        from bambu_cli.constants import BED_PLATE_TYPES

        for plate in BED_PLATE_TYPES:
            fil_data[plate] = bed_temp_str_list
            fil_data[f"{plate}_initial_layer"] = bed_temp_str_list
        json.dump(fil_data, tmp_filament)
        tmp_filament.close()
    except Exception:
        for tmp in created:
            try:
                tmp.close()
            except Exception:
                pass
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        raise

    return tmp_process, tmp_filament
