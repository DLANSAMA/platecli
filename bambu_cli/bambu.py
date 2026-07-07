#!/usr/bin/env python3
"""Bambu Lab printer local control via MQTT. No cloud account needed.

Config file location (auto-detected by platform):
  - Linux:   $XDG_CONFIG_HOME/bambu/config.json (or ~/.config/bambu/config.json)
  - macOS:   ~/Library/Application Support/bambu/config.json
  - Windows: %APPDATA%\\bambu\\config.json

  {
    "printer_ip": "192.168.0.XXX",
    "serial": "YOUR_SERIAL",
    "access_code_file": "~/.config/bambu/access_code",
    "orca_slicer": "~/tools/OrcaSlicer.AppImage",
    "profiles_dir": "~/tools/squashfs-root/resources/profiles/BBL"
  }

Put only the printer access code in the separate access_code file. Inline
"access_code" still works for legacy configs, but access_code_file is safer for
agent workflows and shared machines.

Optional TLS keys:
  - "cert_fingerprint": "<sha256 hex>" pins the printer's self-signed cert for
    both FTPS and MQTT (run `doctor` to print the value to copy). Recommended.
  - "insecure_tls": true disables certificate verification entirely (last resort).

An existing ~/.config/bambu/config.json is always honored first, so legacy
installs on macOS/Windows keep working.

This module is the CLI entry point and the shared runtime-state namespace.
Command logic lives in the sibling modules (cli, commands, download, job,
setup_cmd, camera, slicer, config, printer, protocols); every public and
private helper is reachable through this module via a lazy ``__getattr__``
forwarder, so ``from bambu_cli import bambu`` remains a stable facade for
tests and scripts, and so runtime state can be patched in one place
(``bambu.SIMULATION_MODE``, ``bambu.PRINTER_IP``, ...).
"""
import importlib
import logging
import sys

# Best-effort: make emoji/unicode output work on Windows consoles that default to cp1252.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# --- Runtime state -----------------------------------------------------------
# Mutable, config-derived globals. Modules read these via bambu.<NAME> so a
# loaded config (config.apply_config) or a test patch takes effect everywhere.
SIMULATION_MODE = False
ALLOW_PRIVATE_IPS = False
_LAST_ERROR_PAYLOAD = None  # canonical copies live in bambu_cli.utils

_cfg = {}
PRINTER_IP = "0.0.0.0"
SERIAL = "UNKNOWN"
MQTT_PORT = 8883
INSECURE_TLS = False
ORCA_SLICER = ""
PROFILES_DIR = ""
PRINTER_MODEL = "P1P"
NOZZLE_SIZE = "0.4"
CAMERA_IMAGE = "bambu_p1_streamer"
CAMERA_CONTAINER_NAME = "bambu_camera"
CAMERA_PORT = "1985:1984"
CAMERA_STREAM_URL = ""

# Logging
logger = logging.getLogger("bambu")
# Default config for top-level calls before main()
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', stream=sys.stderr)


def _redacted_serial():
    """Return a non-identifying serial placeholder for reports written to disk."""
    return "UNKNOWN" if not SERIAL or SERIAL == "UNKNOWN" else "<redacted>"


# --- Facade ------------------------------------------------------------------
# `main` is pinned as a real import so `python -m bambu_cli.bambu` and the
# `bambu-cli` console-script entry point (bambu_cli.bambu:main) keep working
# without going through __getattr__.
from bambu_cli.cli import main

_FACADE_MODULES = (
    "bambu_cli.constants", "bambu_cli.cli", "bambu_cli.config",
    "bambu_cli.slicer", "bambu_cli.download", "bambu_cli.netsafety",
    "bambu_cli.printables", "bambu_cli.job",
    "bambu_cli.setup_cmd", "bambu_cli.camera", "bambu_cli.commands",
    "bambu_cli.utils", "bambu_cli.errors", "bambu_cli.context",
    "bambu_cli.printer", "bambu_cli.protocols.ftps", "bambu_cli.protocols.mqtt",
)


def __getattr__(name):
    """Lazily resolve any public or private helper from the implementation
    modules, so ``bambu_cli.bambu`` remains a stable facade (``bambu.<name>``)
    for tests and scripts without eagerly importing (or re-listing) every
    submodule symbol here."""
    for _mod_name in _FACADE_MODULES:
        mod = importlib.import_module(_mod_name)
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module 'bambu_cli.bambu' has no attribute {name!r}")


if __name__ == "__main__":
    main()
