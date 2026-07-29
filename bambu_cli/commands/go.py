"""Thin wrapper for the interactive ``go`` command (lazy import).

Mirrors the ``setup_wrappers`` pattern: the CLI resolves ``cmd_go`` here and the
real implementation lives in ``bambu_cli.interactive.session`` so importing the
command surface never pulls in the interactive package eagerly.
"""

from __future__ import annotations

import argparse


def cmd_go(args: argparse.Namespace) -> None:
    """Interactive guided print: URL in, plastic out — no slicer knowledge needed."""
    from bambu_cli.interactive.session import cmd_go as _cmd_go

    _cmd_go(args)
