"""Quality and material presets for the interactive guided print wizard."""

from __future__ import annotations

import argparse
from typing import Any

# Quality presets map user-facing names to --quality values
QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "draft": {"quality": "draft"},  # -> 0.28mm Extra Draft @BBL <model>
    "standard": {"quality": "standard"},  # -> 0.20mm Standard
    "fine": {"quality": "high"},  # -> 0.12mm Fine
}

# Material presets: filament profile substring + temps sourced from Bambu @base profiles.
#
# `filament` is matched by slicer/cmd.py as a case-insensitive SUBSTRING against
# every "@base" filename in <profiles_dir>/filament/, taking the FIRST os.listdir
# hit.  listdir order is filesystem-dependent, so an ambiguous substring resolves
# differently on Linux and Windows.  Each value below is therefore spelled out far
# enough to match exactly one @base profile -- e.g. a bare "ABS" also matches
# "Bambu Support for ABS @base.json" (support-interface filament!), "Bambu ABS-GF"
# and "Generic ABS".  test_material_filament_substrings_are_unambiguous pins this.
#
# Temps are resolved through each profile's `inherits` chain, scoped to the BBL
# vendor dir (resolving across vendors picks up e.g. Snapmaker bases and yields
# wrong values).  Bed temp is `hot_plate_temp`: _create_temp_profiles writes the
# single bed_temp to every entry of BED_PLATE_TYPES.
MATERIAL_PRESETS: dict[str, dict[str, Any]] = {
    # BBL/filament/Bambu PLA Basic @base.json <- fdm_filament_pla.json
    # nozzle_temperature: ['220'], hot_plate_temp: ['55']
    "PLA": {"filament": "Bambu PLA Basic @base", "nozzle_temp": 220, "bed_temp": 55},
    # BBL/filament/Bambu PETG Basic @base.json <- fdm_filament_pet.json
    # nozzle_temperature: ['255'], hot_plate_temp: ['70']
    "PETG": {"filament": "Bambu PETG Basic @base", "nozzle_temp": 255, "bed_temp": 70},
    # BBL/filament/Bambu ABS @base.json <- fdm_filament_abs.json
    # nozzle_temperature: ['270'], hot_plate_temp: ['90']
    "ABS": {"filament": "Bambu ABS @base", "nozzle_temp": 270, "bed_temp": 90},
    # BBL/filament/Bambu TPU 95A @base.json <- fdm_filament_tpu.json
    # nozzle_temperature: ['230'], hot_plate_temp: ['35']
    "TPU": {"filament": "Bambu TPU 95A @base", "nozzle_temp": 230, "bed_temp": 35},
}


def preset_to_job_args(material: str, quality: str, supports: bool, source: str) -> argparse.Namespace:
    """Build an argparse Namespace for 'job' with preset values applied.

    Args:
        material: Key from MATERIAL_PRESETS (e.g. "PLA").
        quality: Key from QUALITY_PRESETS (e.g. "standard").
        supports: Whether to enable tree supports.
        source: Local path or URL passed as the positional 'source' argument.

    Returns:
        argparse.Namespace with all job defaults plus preset overrides applied.
    """
    from bambu_cli.cli import build_parser

    mat = MATERIAL_PRESETS[material]
    qual = QUALITY_PRESETS[quality]
    parser = build_parser()
    # Parse with a dummy source to get all defaults
    ns = parser.parse_args(["job", source])
    # Apply preset overrides
    ns.filament = mat["filament"]
    ns.nozzle_temp = mat["nozzle_temp"]
    ns.bed_temp = mat["bed_temp"]
    ns.quality = qual["quality"]
    ns.infill = 15
    ns.pattern = "3dhoneycomb"
    if supports:
        ns.supports = True
        ns.support_type = "tree"
    return ns
