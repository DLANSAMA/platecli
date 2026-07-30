"""Thin wrapper for the interactive ``tui`` command (lazy import).

Mirrors ``commands/go.py``: the CLI resolves ``cmd_tui`` here and the real
implementation lives in ``bambu_cli.tui`` so importing the command surface never
pulls in the TUI package (or Textual) eagerly.
"""

from __future__ import annotations

import argparse


def cmd_tui(args: argparse.Namespace) -> None:
    """Full-screen terminal UI: dashboard, guided print, job monitor."""
    from bambu_cli.tui import cmd_tui as _cmd_tui

    _cmd_tui(args)
