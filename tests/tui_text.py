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


def tree_text(widget, *, width: int = 200) -> str:
    """Every piece of text ``widget`` and its shown descendants put on screen.

    Static content, Digits values, border titles and subtitles, and widgets that
    draw themselves (an AMS tray card). A hidden widget and everything under it
    is skipped, so "not shown" assertions mean what they say.
    """
    from rich.text import Text
    from textual.content import Content
    from textual.widget import Widget
    from textual.widgets import Digits, Static

    parts: list[str] = []

    def plain(title) -> str:
        # Textual keeps a border title as markup (a Text title arrives escaped,
        # "model \\[remix]"); what reaches the screen is its plain text.
        if hasattr(title, "plain"):
            return title.plain
        return Content.from_markup(str(title)).plain

    def visit(node) -> None:
        if not node.display:
            return
        for title in (node.border_title, node.border_subtitle):
            if title:
                parts.append(plain(title))
        if isinstance(node, Digits):
            parts.append(node.value)
        elif isinstance(node, Static):
            parts.append(widget_text(node, width=width))
        elif type(node).render is not Widget.render:
            drawn = node.render()
            if isinstance(drawn, (str, Text)):
                parts.append(renderable_text(drawn, width=width))
        for child in node.children:
            visit(child)

    visit(widget)
    return "\n".join(part for part in parts if part)
