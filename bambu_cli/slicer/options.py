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

from bambu_cli.cli import _namespace_get, _path_for_message
from bambu_cli.download.naming import _portable_basename
from bambu_cli.logging_utils import logger

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


def _numeric_values(value: Any) -> list[float]:
    """Best-effort extraction of numbers from a raw override value.

    Accepts ``"999"``, ``"[999]"``, ``[999]``, ``999`` — ignores non-numeric.
    """
    parsed: Any = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
    items = parsed if isinstance(parsed, list) else [parsed]
    out: list[float] = []
    for item in items:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _effective_override_temps(args: argparse.Namespace) -> tuple[list[float], list[float]]:
    """Nozzle and bed temps implied by generic filament overrides.

    Inspects ``--settings-json`` and ``--set-filament`` only; the named
    ``--nozzle-temp`` / ``--bed-temp`` flags are range-checked separately. This
    is what stops ``--set-filament nozzle_temperature=999`` from bypassing the
    printer-safety bounds.
    """
    from bambu_cli.constants import BED_PLATE_TYPES

    nozzle: list[float] = []
    bed: list[float] = []
    sources: list[dict[str, Any]] = []
    raw_json = _namespace_get(args, "settings_json", None)
    if raw_json:
        try:
            blob = json.loads(raw_json)
        except ValueError:
            blob = {}
        fil = blob.get("filament", {}) if isinstance(blob, dict) else {}
        if isinstance(fil, dict):
            sources.append(fil)
    try:
        sources.append(_parse_kv_overrides(_namespace_get(args, "set_filament", None), "set-filament"))
    except ValueError:
        pass  # format error surfaced elsewhere in validation
    bed_keys = set(BED_PLATE_TYPES) | {f"{plate}_initial_layer" for plate in BED_PLATE_TYPES}
    for src in sources:
        for key, value in src.items():
            if key in ("nozzle_temperature", "nozzle_temperature_initial_layer"):
                nozzle += _numeric_values(value)
            elif key in bed_keys:
                bed += _numeric_values(value)
    return nozzle, bed


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

    # Safety: overrides must not push temps past the printer-safety bounds.
    nozzle_over, bed_over = _effective_override_temps(args)
    for temp in nozzle_over:
        if not (MIN_NOZZLE_TEMP_C <= temp <= MAX_NOZZLE_TEMP_C):
            return (
                f"nozzle temperature override {temp:g}°C is outside the safe range "
                f"{MIN_NOZZLE_TEMP_C}-{MAX_NOZZLE_TEMP_C}°C"
            )
    for temp in bed_over:
        if not (MIN_BED_TEMP_C <= temp <= MAX_BED_TEMP_C):
            return f"bed temperature override {temp:g}°C is outside the safe range {MIN_BED_TEMP_C}-{MAX_BED_TEMP_C}°C"
    return None


def _safe_temp_prefix(value: Any, fallback: str = "tmp", max_length: int = 48) -> str:
    """Return a filesystem-safe, bounded tempfile prefix ending in '_'."""
    prefix = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(value or "")).strip(" .")
    if not prefix:
        prefix = fallback
    return f"{prefix[:max_length]}_"
