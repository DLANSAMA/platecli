#!/usr/bin/env python3
"""Bambu Lab printer local control via MQTT. No cloud account needed.

Config file location (auto-detected by platform):
  - Linux:   $XDG_CONFIG_HOME/bambu/config.json (or ~/.config/bambu/config.json)
  - macOS:   ~/Library/Application Support/bambu/config.json
  - Windows: %APPDATA%\\bambu\\config.json

This module is the CLI entry point. Command logic lives in focused sibling
modules (cli, commands, download, job, setup_cmd, camera, slicer, config,
printer, protocols). Collaborators are injected at command boundaries;
tests pass fakes instead of patching this module.

Runtime config state lives on the installed ``RuntimeContext``
(``bambu_cli.context``); read it via ``context.current_settings()`` /
``current_config()``.
"""

import logging
import sys

# Best-effort: make emoji/unicode output work on Windows consoles that default to cp1252.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# Logging — process logger; modules use bambu_cli.logging_utils.logger (proxy).
logger = logging.getLogger("bambu")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)


def _redacted_serial():
    """Return a non-identifying serial placeholder for reports written to disk."""
    from bambu_cli.context import current_settings

    serial = current_settings().serial
    return "UNKNOWN" if not serial or serial == "UNKNOWN" else "<redacted>"


# Console-script / ``python -m bambu_cli.bambu`` entrypoint.
from bambu_cli.cli import main

if __name__ == "__main__":
    main()
