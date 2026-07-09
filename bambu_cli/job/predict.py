"""Dry-run prediction helpers for job remote names and slice args."""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

from bambu_cli.cli import _namespace_get
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DOWNLOADABLE_EXTENSIONS,
    PRINT_READY_EXTENSIONS,
    SLICEABLE_EXTENSIONS,
)
from bambu_cli.download import (
    _download_target_filename,
    _file_extension,
    _is_printables_model_url,
    _portable_basename,
    _sanitize_download_filename,
)
from bambu_cli.slicer import _sliced_output_path


def _slice_args_for_job(filepath, args, output_dir):
    """Build a slice command namespace from job-level arguments."""
    return argparse.Namespace(
        file=filepath,
        quality=getattr(args, "quality", "standard"),
        filament=getattr(args, "filament", "PLA Basic"),
        infill=getattr(args, "infill", 15),
        pattern=getattr(args, "pattern", "3dhoneycomb"),
        nozzle_temp=getattr(args, "nozzle_temp", 220),
        bed_temp=getattr(args, "bed_temp", 60),
        supports=getattr(args, "supports", False),
        support_type=getattr(args, "support_type", None),
        support_interface_density=getattr(args, "support_interface_density", None),
        support_interface_pattern=getattr(args, "support_interface_pattern", None),
        walls=getattr(args, "walls", None),
        wall_type=getattr(args, "wall_type", None),
        top_layers=getattr(args, "top_layers", None),
        bottom_layers=getattr(args, "bottom_layers", None),
        accel_wall=getattr(args, "accel_wall", None),
        accel_wall_outer=getattr(args, "accel_wall_outer", None),
        accel_infill=getattr(args, "accel_infill", None),
        accel_travel=getattr(args, "accel_travel", None),
        accel_first_layer=getattr(args, "accel_first_layer", None),
        copies=getattr(args, "copies", 1),
        output=output_dir,
        threads=getattr(args, "threads", None),
        layer_height=getattr(args, "layer_height", None),
        first_layer_height=getattr(args, "first_layer_height", None),
        brim=getattr(args, "brim", None),
        speed=getattr(args, "speed", None),
        seam_position=getattr(args, "seam_position", None),
        ironing=getattr(args, "ironing", None),
        support_threshold=getattr(args, "support_threshold", None),
        fan_speed=getattr(args, "fan_speed", None),
        flow_ratio=getattr(args, "flow_ratio", None),
        set_process=getattr(args, "set_process", None),
        set_filament=getattr(args, "set_filament", None),
        settings_json=getattr(args, "settings_json", None),
    )


def _predicted_sliced_remote_name(filepath, copies=1):
    """Return the remote filename Orca output will have after job/send slicing."""

    return _portable_basename(_sliced_output_path(filepath, ".", copies=copies))


def _predicted_url_download_extension(url, args):
    """Infer a direct URL dry-run extension from URL path or explicit --name."""
    source_ext = _file_extension(urlparse(url).path)
    if source_ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        return source_ext
    if _namespace_get(args, "name"):
        return _file_extension(_sanitize_download_filename(_namespace_get(args, "name")))
    return None


def _predicted_url_remote_name(url, args):
    """Best-effort remote filename prediction for side-effect-free URL dry-runs.

    This intentionally only uses the URL path and explicit --name value. It does
    not resolve Printables pages, HTML pages, redirects, or ZIP members because
    doing so would require network I/O or archive extraction.
    """
    if _is_printables_model_url(url):
        return None
    predicted_ext = _predicted_url_download_extension(url, args)
    if predicted_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        return None
    if predicted_ext not in DOWNLOADABLE_EXTENSIONS:
        return None
    filename = _download_target_filename(args, url)
    if predicted_ext in SLICEABLE_EXTENSIONS:
        return _predicted_sliced_remote_name(filename, getattr(args, "copies", 1))
    if predicted_ext in PRINT_READY_EXTENSIONS:
        return filename
    return None
