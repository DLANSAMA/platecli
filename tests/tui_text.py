"""Plain text of a Textual widget, including Rich tables.

Textual 8 dropped ``Static.renderable``; ``content`` is the public equivalent
(a string or a Rich renderable). ``str(table)`` is a repr, so grids go through
a Console the same way they did under 1.x.
"""

from __future__ import annotations

from rich.console import Console


def renderable_text(payload, *, width: int = 200) -> str:
    """Plain text of a string or Rich renderable at ``width``."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    console = Console(width=width)
    with console.capture() as capture:
        console.print(payload)
    return capture.get()


def widget_text(widget, *, width: int = 200) -> str:
    """Return the visible text of ``widget`` (Static, panel, etc.)."""
    payload = getattr(widget, "content", None)
    if payload is None:
        payload = getattr(widget, "renderable", None)
    return renderable_text(payload, width=width)
