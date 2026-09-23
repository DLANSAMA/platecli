"""Slice option validation, path helpers, wall-type normalization, and the
generic OrcaSlicer setting-override machinery (``--set`` / ``--set-filament`` /
``--settings-json`` and ``slice --list-settings``)."""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import stat
from functools import lru_cache
from typing import Any

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.fsutil import _portable_basename
from bambu_cli.logging_utils import logger
from bambu_cli.paths import path_for_message as _path_for_message

# Profile bookkeeping fields that are not user-tunable print settings; excluded
# from discovery (``--list-settings``) and unknown-key validation.
_NON_SETTING_KEYS = frozenset(
    {
        "name",
        "type",
        "inherits",
        "from",
        "setting_id",
        "instantiation",
        "version",
        "compatible_printers",
        "compatible_printers_condition",
        "compatible_prints",
        "compatible_prints_condition",
    }
)


def _normalize_wall_type(wall_type: str | None) -> str | None:
    """Accept the old 'archaic' spelling as an alias for Orca's classic walls."""
    if wall_type == "archaic":
        return "classic"
    return wall_type


def _profiles_dir_from_process(process_path: str) -> str:
    """``<profiles_dir>/process/<file>.json`` -> ``<profiles_dir>``."""
    return os.path.dirname(os.path.dirname(os.path.abspath(process_path)))


@lru_cache(maxsize=16)
def _known_setting_keys(profiles_dir: str, kind: str) -> dict[str, Any]:
    """Union of tunable setting keys across every ``kind`` profile on disk.

    Returns ``{key: representative_value}`` (first value seen wins) so the
    discovery command can show agents both the key names and example shapes.

    Performance note (Bolt ⚡): Memoized because reading/parsing all profile
    JSON files on disk is expensive and this is called multiple times per slice
    command for the exact same directories (static over execution lifetime).
    """
    result: dict[str, Any] = {}
    for path in sorted(glob.glob(os.path.join(profiles_dir, kind, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if key in _NON_SETTING_KEYS:
                continue
            result.setdefault(key, value)
    return result


def setting_catalog(profiles_dir: str) -> dict[str, dict[str, Any]]:
    """Return ``{"process": {key: example}, "filament": {key: example}}``.

    The one discovery seam over the installed profiles, read by
    ``slice --list-settings``. Empty sections mean "no profiles readable here" —
    callers degrade rather than fail.
    """
    # G-code/script keys are refused as overrides (see _override_safety_problem),
    # so they are not advertised as settable either.
    return {
        kind: {
            key: value
            for key, value in _known_setting_keys(profiles_dir, kind).items()
            if not _SCRIPT_KEY_RE.search(key)
        }
        for kind in ("process", "filament")
    }


def _parse_kv_overrides(entries: list[str] | None, label: str) -> dict[str, str]:
    """``['k=v', ...]`` -> ``{'k': 'v'}``. Raises ``ValueError`` on a bad entry."""
    out: dict[str, str] = {}
    for entry in entries or []:
        if "=" not in entry:
            raise ValueError(f"Invalid --{label} '{entry}': expected KEY=VALUE")
        key, _, value = entry.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --{label} '{entry}': empty setting name")
        out[key] = value
    return out


def _coerce_override_value(base_value: Any, raw: Any) -> Any:
    """Shape ``raw`` to match the base profile's value type.

    Orca profile values are strings or lists-of-strings. ``raw`` may be a bare
    string (``"4"``), a JSON scalar, or a JSON array (``"[220]"``). A list-typed
    base gets a list; a scalar base gets a string, mirroring how the built-in
    flag overrides already write values.
    """
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
    if isinstance(base_value, list):
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [str(raw)]
    if isinstance(parsed, (dict, list)):
        return parsed
    return str(raw)


def _generic_section_overrides(args: argparse.Namespace, kind: str) -> dict[str, str]:
    """Merge generic overrides for ``kind`` ('process'|'filament').

    Precedence low->high: ``--settings-json`` then ``--set`` / ``--set-filament``
    (explicit ``--set`` wins). Returns raw (un-coerced) string values.
    """
    overrides: dict[str, str] = {}
    raw_json = _namespace_get(args, "settings_json", None)
    if raw_json:
        blob = json.loads(raw_json)  # validated earlier; malformed raises here
        section = blob.get(kind, {}) if isinstance(blob, dict) else {}
        if isinstance(section, dict):
            for key, value in section.items():
                overrides[str(key)] = value if isinstance(value, str) else json.dumps(value)
    dest = "set_process" if kind == "process" else "set_filament"
    label = "set" if kind == "process" else "set-filament"
    overrides.update(_parse_kv_overrides(_namespace_get(args, dest, None), label))
    return overrides


def _warn_unknown_keys(overrides: dict[str, str], known: dict[str, Any], kind: str) -> None:
    """Warn (but never block) on keys absent from the installed profiles."""
    if not known:
        return
    for key in overrides:
        if key in known:
            continue
        suggestion = difflib.get_close_matches(key, known.keys(), n=1)
        hint = f" (did you mean '{suggestion[0]}'?)" if suggestion else ""
        logger.warning(f"⚠️  Unknown {kind} setting '{key}'{hint} — passing through to OrcaSlicer anyway.")


# Keys whose value is G-code or a program to run. Their contents are never
# range-checked, so letting them through would sidestep every bound below
# (``filament_start_gcode=M104 S399`` reached the printer G-code unchanged).
_SCRIPT_KEY_RE = re.compile(r"gcode|post_process", re.IGNORECASE)

# OrcaSlicer merges every loaded profile into one config, so a machine key sent
# through --set replaces the printer profile's value: ``printable_area`` changes
# the bed the model is laid out on. The installed machine profiles name the full
# set; these are refused even when no profiles can be read.
_BUILTIN_MACHINE_KEYS = frozenset(
    {
        "bed_exclude_area",
        "extruder_clearance_height_to_lid",
        "extruder_clearance_height_to_rod",
        "extruder_clearance_radius",
        "extruder_offset",
        "gcode_flavor",
        "nozzle_diameter",
        "printable_area",
        "printable_height",
        "printer_model",
        "printer_variant",
        "z_offset",
    }
)
_BUILTIN_MACHINE_PREFIXES = ("machine_",)


# A plain ASCII decimal: what OrcaSlicer reads the same way Python does. float()
# also accepts "3_5_0", "1e2" and non-ASCII digits, which OrcaSlicer reads
# differently (or not at all), so the checked and sliced values could differ.
_PLAIN_NUMBER_RE = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")


def _temperature_values(value: Any) -> list[float] | None:
    """Every number OrcaSlicer would read from a temperature override.

    Returns ``None`` when any part of the value is not a plain number, so the
    caller can refuse it: the check has to fail closed. OrcaSlicer reads a
    comma-separated string as a per-extruder list (``"400,220"`` sets extruder
    0 to 400 C) and joins JSON array elements the same way, so both are split
    here. A bare JSON number, a numeric string, and a flat list of either are
    accepted; booleans, nested lists, objects and empty values are not.
    """
    parsed: Any = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        if not isinstance(parsed, list):
            # A bare scalar reaches OrcaSlicer as the raw text (see
            # _coerce_override_value), so check that text, not json's reading
            # of it ("1e2" decodes to 100.0 but OrcaSlicer reads 1).
            parsed = value
    items = parsed if isinstance(parsed, list) else [parsed]
    out: list[float] = []
    for item in items:
        if isinstance(item, bool):
            return None
        if isinstance(item, (int, float)):
            out.append(float(item))
            continue
        if not isinstance(item, str):
            return None
        for token in item.split(","):
            token = token.strip()
            if not _PLAIN_NUMBER_RE.fullmatch(token):
                return None
            out.append(float(token))
    return out or None


def _is_bed_temp_key(key: str) -> bool:
    """True for any bed-plate temperature filament key (``*_plate_temp`` or its
    ``*_plate_temp_initial_layer`` companion), e.g. ``supertack_plate_temp``.

    Bed temps must be range-checked against MAX_BED_TEMP_C regardless of plate
    type; new Orca bed types (SuperTack, etc.) must not slip past validation just
    because they are absent from the legacy BED_PLATE_TYPES list.
    """
    return isinstance(key, str) and (key.endswith("_plate_temp") or key.endswith("_plate_temp_initial_layer"))


def _is_temperature_key(key: str) -> bool:
    """Any setting that holds a temperature: nozzle, bed, chamber, or otherwise."""
    lowered = key.lower()
    return "temperature" in lowered or lowered.endswith(("_temp", "_temp_initial_layer"))


def _override_sections(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    """Every generic override as ``(kind, {key: raw value})``, kind 'process'|'filament'.

    Reads ``--settings-json`` (both sections), ``--set-filament`` and ``--set``.
    Format errors are reported by the caller's own checks, so they are skipped
    here rather than raised.
    """
    sections: list[tuple[str, dict[str, Any]]] = []
    raw_json = _namespace_get(args, "settings_json", None)
    if raw_json:
        try:
            blob = json.loads(raw_json)
        except ValueError:
            blob = {}
        if isinstance(blob, dict):
            for kind in ("filament", "process"):
                section = blob.get(kind, {})
                if isinstance(section, dict):
                    sections.append((kind, section))
    for kind, dest, label in (("filament", "set_filament", "set-filament"), ("process", "set_process", "set")):
        try:
            sections.append((kind, _parse_kv_overrides(_namespace_get(args, dest, None), label)))
        except ValueError:
            pass
    return sections


def _machine_key_problem(key: str, kind: str, profiles_dir: str) -> str | None:
    """Refuse a printer (machine) setting sent as a process/filament override."""
    machine_keys = _known_setting_keys(profiles_dir, "machine") if profiles_dir else {}
    own_keys = _known_setting_keys(profiles_dir, kind) if profiles_dir else {}
    is_machine = (key in machine_keys and key not in own_keys) or (
        key in _BUILTIN_MACHINE_KEYS or key.startswith(_BUILTIN_MACHINE_PREFIXES)
    )
    if not is_machine:
        return None
    message = (
        f"'{key}' is a printer (machine) setting and cannot be overridden: it would slice for "
        "hardware your printer does not have. Change the printer profile in OrcaSlicer instead."
    )
    filament_keys = _known_setting_keys(profiles_dir, "filament") if profiles_dir else {}
    if f"filament_{key}" in filament_keys:
        message += f" To change it for this print, use --set-filament filament_{key}=..."
    return message


def _override_safety_problem(args: argparse.Namespace) -> str | None:
    """First reason a generic override is unsafe, or ``None``.

    Temperatures must be plain numbers inside the printer-safety bounds;
    G-code, scripts and printer (machine) settings cannot be overridden at all.
    """
    from bambu_cli.constants import MAX_BED_TEMP_C, MAX_NOZZLE_TEMP_C, MIN_BED_TEMP_C, MIN_NOZZLE_TEMP_C
    from bambu_cli.context import current_settings

    profiles_dir = current_settings().profiles_dir
    for kind, section in _override_sections(args):
        for key, value in section.items():
            key = str(key)
            if _SCRIPT_KEY_RE.search(key):
                return (
                    f"'{key}' cannot be overridden: G-code and post-processing scripts are not "
                    "safety-checked. Edit the profile in OrcaSlicer instead."
                )
            machine_problem = _machine_key_problem(key, kind, profiles_dir)
            if machine_problem:
                return machine_problem
            if not _is_temperature_key(key):
                continue
            temps = _temperature_values(value)
            if temps is None:
                return f"temperature override {key}={value!r} must be a number or a comma-separated list of numbers"
            if _is_bed_temp_key(key):
                label, low, high = "bed", MIN_BED_TEMP_C, MAX_BED_TEMP_C
            elif key.lower().endswith("_delta"):
                # standby_temperature_delta is an offset and legitimately negative.
                label, low, high = "nozzle", -MAX_NOZZLE_TEMP_C, MAX_NOZZLE_TEMP_C
            else:
                label, low, high = "nozzle", MIN_NOZZLE_TEMP_C, MAX_NOZZLE_TEMP_C
            for temp in temps:
                if not (low <= temp <= high):
                    return f"{label} temperature override {key}={temp:g}°C is outside the safe range {low}-{high}°C"
    return None


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
    from bambu_cli.argutils import namespace_get as _namespace_get
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

    layer_height = _namespace_get(args, "layer_height", None)
    if isinstance(layer_height, (int, float)) and not (0 < layer_height <= 1.0):
        return f"--layer-height must be between 0 and 1.0 mm (got {layer_height})"
    brim = _namespace_get(args, "brim", None)
    if isinstance(brim, (int, float)) and brim < 0:
        return f"--brim must be >= 0 mm (got {brim})"
    speed = _namespace_get(args, "speed", None)
    if isinstance(speed, (int, float)) and speed <= 0:
        return f"--speed must be a positive mm/s value (got {speed})"
    first_layer_height = _namespace_get(args, "first_layer_height", None)
    if isinstance(first_layer_height, (int, float)) and not (0 < first_layer_height <= 1.0):
        return f"--first-layer-height must be between 0 and 1.0 mm (got {first_layer_height})"
    support_threshold = _namespace_get(args, "support_threshold", None)
    if isinstance(support_threshold, (int, float)) and not (0 <= support_threshold <= 90):
        return f"--support-threshold must be between 0 and 90 degrees (got {support_threshold})"
    fan_speed = _namespace_get(args, "fan_speed", None)
    if isinstance(fan_speed, (int, float)) and not (0 <= fan_speed <= 100):
        return f"--fan-speed must be between 0 and 100 (got {fan_speed})"
    flow_ratio = _namespace_get(args, "flow_ratio", None)
    if isinstance(flow_ratio, (int, float)) and not (0 < flow_ratio <= 2.0):
        return f"--flow-ratio must be between 0 and 2.0 (got {flow_ratio})"

    # Generic overrides: fail fast on malformed KEY=VALUE / bad JSON.
    try:
        _parse_kv_overrides(_namespace_get(args, "set_process", None), "set")
        _parse_kv_overrides(_namespace_get(args, "set_filament", None), "set-filament")
    except ValueError as exc:
        return str(exc)
    raw_json = _namespace_get(args, "settings_json", None)
    if raw_json:
        try:
            blob = json.loads(raw_json)
        except ValueError as exc:
            return f"--settings-json is not valid JSON: {exc}"
        if not isinstance(blob, dict):
            return "--settings-json must be a JSON object with 'process' and/or 'filament' keys"
        for section in ("process", "filament"):
            if section in blob and not isinstance(blob[section], dict):
                return f"--settings-json '{section}' must be an object of key/value overrides"

    # Safety: overrides must not push temps past the printer-safety bounds, and
    # G-code, scripts and printer settings cannot be overridden at all.
    return _override_safety_problem(args)


def _safe_temp_prefix(value: Any, fallback: str = "tmp", max_length: int = 48) -> str:
    """Return a filesystem-safe, bounded tempfile prefix ending in '_'."""
    prefix = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(value or "")).strip(" .")
    if not prefix:
        prefix = fallback
    return f"{prefix[:max_length]}_"
