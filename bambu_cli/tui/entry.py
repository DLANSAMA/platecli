"""Entry point for ``plate tui`` — TTY / ``--json`` / missing-extra guards.

Mirrors ``bambu_cli.interactive.session.cmd_go``'s front-door contract: the TUI
has no machine contract (agents use ``job`` / ``send``), so ``--json`` and a
non-TTY stdin both emit the standard error envelope and abort with
``EXIT_COMMAND_ERROR``. The Textual dependency is import-guarded here so a user
without the extra gets an actionable install hint (``EXIT_CONFIG_ERROR``) rather
than an ``ImportError`` traceback.

Domain code never terminates the process itself — terminal conditions raise
``BambuError`` via ``abort``; the one process-exit call in the codebase lives in
``cli.py`` (CI greps for that, in code and in prose).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR
from bambu_cli.errors import abort

_NON_TTY_MESSAGE = "plate tui is interactive; use 'plate job <url> --confirm' for scripts."
_MISSING_EXTRA_MESSAGE = "plate tui requires the TUI extra: pip install 'platecli[tui]'"


def _textual_available() -> bool:
    """True when Textual can be imported (the ``tui`` extra is installed)."""
    return importlib.util.find_spec("textual") is not None


def cmd_tui(args: argparse.Namespace) -> None:
    """Launch the full-screen terminal UI, or abort with a clear reason.

    Guards, in order: ``--json`` (no machine contract), non-TTY stdin (needs a
    real terminal), and the missing Textual extra. Only past all three does the
    real Textual app run — imported lazily so the guards never pay for it.
    """
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        # Interactive mode has no machine contract; agents already have `job`.
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse", command="tui")

    if not sys.stdin.isatty():
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse")

    if not _textual_available():
        abort(_MISSING_EXTRA_MESSAGE, exit_code=EXIT_CONFIG_ERROR, failed_step="config")

    # Import the Textual app only now — after the guards — so importing the
    # command surface (or running the guarded paths) never imports Textual.
    from bambu_cli.tui.app import run_app

    run_app(args)
