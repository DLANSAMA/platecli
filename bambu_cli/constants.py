"""Shared immutable constants for bambu-cli.

Single source of truth for exit codes, file-type tables, safety limits, and
default timeouts. Mutable runtime state (printer address, simulation flag,
loaded config) lives on ``RuntimeContext`` (see ``bambu_cli.context``).

``VERSION`` is resolved from package metadata when installed, otherwise from
the repo ``pyproject.toml`` (canonical release version). Edit version only in
``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path


def _version_from_pyproject() -> str | None:
    """Read project.version from the source-tree pyproject.toml, if present."""
    root = Path(__file__).resolve().parents[1]
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _resolve_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        from importlib_metadata import PackageNotFoundError, version  # type: ignore

    try:
        return version("bambu-local-cli")
    except PackageNotFoundError:
        pass
    return _version_from_pyproject() or "0.0.0+dev"


def __getattr__(name: str) -> str:
    """Lazily resolve and cache ``VERSION`` on first access (PEP 562).

    ``_resolve_version`` costs a real importlib.metadata lookup (or a
    pyproject.toml read/regex fallback), which is unnecessary work for the
    vast majority of invocations that never touch ``VERSION`` (only the
    ``--version`` CLI path does). Resolving lazily via module ``__getattr__``
    avoids paying that cost at import time; caching the result in
    ``globals()`` means subsequent accesses are plain attribute hits and
    never re-enter this function.
    """
    if name == "VERSION":
        value = _resolve_version()
        globals()["VERSION"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Exit codes
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_NETWORK_ERROR = 2
EXIT_FILE_ERROR = 3
EXIT_PRINTER_ERROR = 4
EXIT_COMMAND_ERROR = 5
EXIT_TIMEOUT = 6

# Default timeouts (seconds); override per-run via CLI flags or config keys.
DEFAULT_NETWORK_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 60.0
SLICER_TIMEOUT = 120.0
COMMAND_TIMEOUT = 5.0
PRINT_ACK_TIMEOUT = 10.0
UPLOAD_TIMEOUT = 300.0

# Download safety limits
HTML_LINK_SCAN_LIMIT = 1024 * 1024
DEFAULT_MAX_DOWNLOAD_MB = 2048
MAX_DOWNLOAD_FILENAME_LENGTH = 160
DNS_CACHE_TTL = 300

# Physical safety bounds for slice / print options (Bambu-class FDM printers).
# Nozzle: ambient through high-temp engineering filaments (~300 °C); reject absurd values.
# Bed: Bambu heated beds top out near 120 °C; allow headroom to 150.
# AMS: 4 slots per unit; up to 4 units on an AMS hub → indexes 0..15.
MIN_NOZZLE_TEMP_C = 0
MAX_NOZZLE_TEMP_C = 350
MIN_BED_TEMP_C = 0
MAX_BED_TEMP_C = 150
MAX_AMS_SLOT_INDEX = 15

# File-type tables
BED_PLATE_TYPES = ["cool_plate_temp", "hot_plate_temp", "textured_plate_temp", "eng_plate_temp"]
SLICEABLE_EXTENSIONS = (".stl", ".step", ".stp", ".obj")
PRINT_READY_EXTENSIONS = (".3mf", ".gcode")
DOWNLOADABLE_EXTENSIONS = SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS
ARCHIVE_DOWNLOAD_EXTENSIONS = (".zip",)
DOWNLOAD_CANDIDATE_EXTENSIONS = DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS
DOWNLOAD_LINK_EXTENSION_PRIORITY = {
    ".stl": 0,
    ".step": 1,
    ".stp": 1,
    ".obj": 2,
    ".3mf": 3,
    ".gcode": 4,
    ".zip": 5,
}
KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS = {
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".pdf",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
}
KNOWN_UNSUPPORTED_CONTENT_TYPES = {
    "application/json",
    "application/pdf",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-gzip",
    "application/x-rar-compressed",
    "application/x-tar",
    "text/csv",
    "text/plain",
    "text/xml",
}
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Command routing sets
PRINTER_CONFIG_COMMANDS = {
    "status",
    "light",
    "pause",
    "resume",
    "stop",
    "upload",
    "files",
    "print",
    "job",
    "send",
    "delete",
    "snapshot",
    "gcode",
    "doctor",
}
LOCAL_COMMANDS = {"slice", "download", "preflight", "setup", "config"}
PRINTER_NETWORK_COMMANDS = PRINTER_CONFIG_COMMANDS - LOCAL_COMMANDS
