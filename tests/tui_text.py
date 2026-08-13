"""Plain text of a Textual widget, including Rich tables.

Textual 8 dropped ``Static.renderable``; ``content`` is the public equivalent
(a string or a Rich renderable). ``str(table)`` is a repr, so grids go through
a Console the same way they did under 1.x.
"""

from __future__ import annotations

import re

from rich.console import Console

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def renderable_text(payload, *, width: int = 200) -> str:
    """Plain text of a string or Rich renderable at ``width``."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return _plain(payload)
    console = Console(width=width, no_color=True, highlight=False, force_terminal=False)
    with console.capture() as capture:
        console.print(payload)
    return _plain(capture.get())


def widget_text(widget, *, width: int = 200) -> str:
    """Return the visible text of ``widget`` (Static, panel, etc.)."""
    payload = getattr(widget, "content", None)
    if payload is None:
        payload = getattr(widget, "renderable", None)
    return renderable_text(payload, width=width)
