"""OrcaSlicer profile discovery, diagnostics, and temp-profile creation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from functools import lru_cache
from typing import IO

from bambu_cli.logging_utils import logger
from bambu_cli.paths import display_path as _display_path
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.slicer.options import (
    _coerce_override_value,
    _generic_section_overrides,
    _known_setting_keys,
    _normalize_wall_type,
    _profiles_dir_from_process,
    _warn_unknown_keys,
)


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
    from bambu_cli.config import detect_orca_slicer, orca_install_hint

    if not path or not str(path).strip():
        if not detect_orca_slicer():
            # Nothing installed: installing is the first step, and `plate setup`
            # picks up the paths afterwards, so don't also suggest a config edit.
            return f"OrcaSlicer is not configured. {orca_install_hint()}"
        return 'OrcaSlicer is not configured. Run `plate setup`, or set "orca_slicer" in your config.json.'
    expanded = _expand_path(path)
    display = _display_path(expanded)
    if not os.path.exists(expanded):
        message = f"OrcaSlicer not found at {display}"
        # Nothing anywhere on this machine — a config edit cannot fix that.
        if not detect_orca_slicer():
            message += f". {orca_install_hint()}"
        return message
    if sys.platform != "win32" and not os.access(expanded, os.X_OK):
        return (
            f"OrcaSlicer is not executable at {display}; run `chmod +x {display}` or update orca_slicer in config.json."
        )
    return None


@lru_cache(maxsize=128)
def _process_profile_compatible(path: str, compatible_printer: str | None) -> bool:
    if not compatible_printer:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()

        # Fast-path string match to avoid full JSON parse if not even present
        if compatible_printer not in content:
            return False

        data = json.loads(content)
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
            # No profile for this model at all. Borrowing another model's
            # (the old "fall back to P1P") tuned speeds and accelerations for
            # a different machine; report it instead.
            logger.error(f"No slicer profiles for {model_code} found in {proc_dir}")
            return None
    return None


def _load_flattened_profile(path: str) -> dict:
    """Load an OrcaSlicer profile with its ``inherits`` chain merged in.

    OrcaSlicer does not resolve ``inherits`` for a profile loaded from a temp
    file outside its profiles tree. Every inherited value then falls back to a
    generic default without a warning: measured on 2026-09-22, a PETG slice
    lost 50 process settings (acceleration 500 instead of 10000, no
    elephant-foot compensation) and 12 filament settings (``filament_type`` came
    out as PLA, nozzle 200 C). So parents are merged here, child keys winning,
    and ``inherits`` is dropped. A parent is looked up next to its child (same
    kind directory); a missing parent leaves the reference in place, and a
    cycle is an error rather than infinite recursion.
    """
    seen: set[str] = set()

    def _resolve(current: str) -> dict:
        real = os.path.realpath(current)
        if real in seen:
            raise ValueError(f"profile inherits cycle at {os.path.basename(current)}")
        seen.add(real)
        with open(current, encoding="utf-8") as f:
            data = json.load(f)
        parent = data.get("inherits")
        if parent:
            parent_path = os.path.join(os.path.dirname(current), f"{parent}.json")
            if os.path.exists(parent_path):
                merged = _resolve(parent_path)
                del data["inherits"]
                merged.update(data)
                return merged
        return data

    return _resolve(path)


# What every Bambu printer definition in OrcaSlicer's BBL profiles declares.
FALLBACK_BED_TYPE = "Textured PEI Plate"


def _default_bed_type(profiles_dir: str, full_model_name: str) -> str:
    """The plate OrcaSlicer's GUI assumes for this printer (``default_bed_type``).

    It lives in the printer definition (``machine/Bambu Lab P1P.json``), which a
    CLI slice never loads, so without it OrcaSlicer slices for its own default,
    the Cool Plate -- where Bambu's PETG and ABS profiles set 0 C (unsupported)
    and the bed would not be heated at all.
    """
    path = os.path.join(profiles_dir, "machine", f"{full_model_name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f).get("default_bed_type")
    except (OSError, ValueError, AttributeError):
        return FALLBACK_BED_TYPE
    return value if isinstance(value, str) and value else FALLBACK_BED_TYPE


def _create_temp_profiles(
    process: str, filament: str, args: argparse.Namespace, *, bed_type: str | None = None
) -> tuple[IO[str], IO[str]]:
    """Create temporary process and filament profiles with overrides.

    ``bed_type`` becomes the process ``curr_bed_type`` (the plate the filament's
    bed temperature is read for) unless an override sets it.
    """
    infill = getattr(args, "infill", 15)
    pattern = getattr(args, "pattern", "3dhoneycomb")
    supports = getattr(args, "supports", False)
    # None (the default) keeps the filament profile's own temperatures. They used
    # to default to 220/60 and overwrite every filament: PETG and ABS printed at
    # PLA temperatures unless the caller passed --nozzle-temp/--bed-temp.
    nozzle_temp = getattr(args, "nozzle_temp", None)
    bed_temp = getattr(args, "bed_temp", None)
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
        proc_data = _load_flattened_profile(process)
        if bed_type:
            proc_data["curr_bed_type"] = bed_type
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

        # Named convenience flags (sugar over the generic override machinery).
        layer_height = getattr(args, "layer_height", None)
        if isinstance(layer_height, (int, float)):
            proc_data["layer_height"] = str(layer_height)
        brim = getattr(args, "brim", None)
        if isinstance(brim, (int, float)):
            proc_data["brim_width"] = str(brim)
            proc_data["brim_type"] = "no_brim" if brim == 0 else "outer_only"
        speed = getattr(args, "speed", None)
        if isinstance(speed, (int, float)):
            for key in ("outer_wall_speed", "inner_wall_speed", "sparse_infill_speed"):
                proc_data[key] = str(speed)
        first_layer_height = getattr(args, "first_layer_height", None)
        if isinstance(first_layer_height, (int, float)):
            proc_data["initial_layer_print_height"] = str(first_layer_height)
        seam_position = getattr(args, "seam_position", None)
        if seam_position:
            proc_data["seam_position"] = seam_position
        ironing = getattr(args, "ironing", None)
        if ironing:
            proc_data["ironing_type"] = "no ironing" if ironing == "none" else ironing
        support_threshold = getattr(args, "support_threshold", None)
        if isinstance(support_threshold, (int, float)):
            proc_data["support_threshold_angle"] = str(support_threshold)

        # Generic process overrides (--set / --settings-json) win over the named
        # flags above; unknown keys warn but still pass through to OrcaSlicer.
        profiles_dir = _profiles_dir_from_process(process)
        proc_overrides = _generic_section_overrides(args, "process")
        _warn_unknown_keys(proc_overrides, _known_setting_keys(profiles_dir, "process"), "process")
        for key, value in proc_overrides.items():
            proc_data[key] = _coerce_override_value(proc_data.get(key), value)

        json.dump(proc_data, tmp_process)
        tmp_process.close()

        # Copy filament profile and merge overrides
        tmp_filament = tempfile.NamedTemporaryFile(  # noqa: SIM115 — handle outlives block; cleaned up by caller
            mode="w", suffix=".json", delete=False, prefix="fil_", encoding="utf-8"
        )
        created.append(tmp_filament)
        fil_data = _load_flattened_profile(filament)

        if nozzle_temp is not None:
            nozzle_temp_str_list = [str(nozzle_temp)]
            fil_data["nozzle_temperature"] = nozzle_temp_str_list
            fil_data["nozzle_temperature_initial_layer"] = nozzle_temp_str_list

        if bed_temp is not None:
            bed_temp_str_list = [str(bed_temp)]
            from bambu_cli.constants import BED_PLATE_TYPES

            for plate in BED_PLATE_TYPES:
                fil_data[plate] = bed_temp_str_list
                fil_data[f"{plate}_initial_layer"] = bed_temp_str_list

        # Named filament convenience flags (fan_max_speed is a per-extruder list).
        fan_speed = getattr(args, "fan_speed", None)
        if isinstance(fan_speed, (int, float)):
            fil_data["fan_max_speed"] = [str(fan_speed)]
        flow_ratio = getattr(args, "flow_ratio", None)
        if isinstance(flow_ratio, (int, float)):
            # Orca's filament profile key is `filament_flow_ratio` (the bare
            # `flow_ratio` is a process-level setting and would be ignored here).
            fil_data["filament_flow_ratio"] = str(flow_ratio)

        # Generic filament overrides (--set-filament / --settings-json). Temp
        # keys were already range-checked in _validate_slice_options, so a value
        # reaching here is within the printer-safety bounds.
        fil_overrides = _generic_section_overrides(args, "filament")
        _warn_unknown_keys(fil_overrides, _known_setting_keys(profiles_dir, "filament"), "filament")
        for key, value in fil_overrides.items():
            fil_data[key] = _coerce_override_value(fil_data.get(key), value)

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


# OrcaSlicer's own test for an extruder reset in the layer-change G-code.
_G92_E0_RE = re.compile(r"^[ \t]*[gG]92[ \t]*[eE](0(\.0*)?|\.0+)[ \t]*(;.*)?$", re.MULTILINE)


def _ensure_layer_extruder_reset(machine: dict) -> None:
    """Add ``G92 E0`` to the layer-change G-code when relative extrusion needs it.

    OrcaSlicer refuses to slice with relative E unless a layer-change hook
    resets the extruder. Bambu's X1/P1 profiles include the reset; the A1 and
    A1 mini profiles rely on an exemption the OrcaSlicer CLI does not apply, so
    every A1/A1 mini slice failed. With relative extrusion the reset only zeroes
    a counter; nothing moves.
    """
    if str(machine.get("use_relative_e_distances", "1")).strip("[]'\" ") == "0":
        return
    hooks = f"{machine.get('before_layer_change_gcode') or ''}\n{machine.get('layer_change_gcode') or ''}"
    if _G92_E0_RE.search(hooks):
        return
    layer = machine.get("layer_change_gcode") or ""
    machine["layer_change_gcode"] = f"{layer.rstrip(chr(10))}\nG92 E0\n" if layer else "G92 E0\n"


def _create_temp_machine(machine_path: str, profiles_dir: str) -> IO[str]:
    """Create a temp machine profile with its ``inherits`` chain flattened.

    OrcaSlicer resolves ``inherits`` relative to its own preset locations, which
    a temp file outside the profiles dir defeats — so parents are merged in here
    (child keys win) and the ``inherits`` key dropped. A parent missing on disk
    leaves the reference intact for Orca to resolve by name. Open/parse errors
    propagate; the caller reports them as profile-preparation failures.
    """

    resolved = _load_flattened_profile(machine_path)
    _ensure_layer_extruder_reset(resolved)
    tmp_machine = tempfile.NamedTemporaryFile(  # noqa: SIM115 — handle outlives block; cleaned up by caller
        mode="w", suffix=".json", delete=False, prefix="mach_", encoding="utf-8"
    )
    try:
        json.dump(resolved, tmp_machine)
        tmp_machine.close()
    except Exception:
        try:
            tmp_machine.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_machine.name)
        except OSError:
            pass
        raise
    return tmp_machine
