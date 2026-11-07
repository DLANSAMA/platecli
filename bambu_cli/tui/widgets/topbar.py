"""The one-line bar at the top of every full screen (replaces Textual's Header).

Left: the ``plate`` mark and the screen's name. Right: which printer this is
(model and address, or "Simulated P1S") and, where a screen has one, the
printer's state as a coloured badge. Every piece is its own ``Static`` with
``markup=False``: the printer model comes from the user's config, and a state
label can come from firmware, so neither may be parsed as markup.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from bambu_cli.tui.services import StateBadge, printer_descriptor

TONES = ("active", "paused", "done", "error", "busy", "idle")


def set_tone(widget: Any, tone: str | None) -> None:
    """Put exactly one ``-tone-*`` class on ``widget`` (none for ``None``)."""
    for name in TONES:
        widget.set_class(name == tone, f"-tone-{name}")


class TopBar(Horizontal):
    """``plate  Dashboard ··· P1S · 192.168.1.23   ● Printing``."""

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static("plate", id="topbar-brand", markup=False)
        yield Static(self._title, id="topbar-title", markup=False)
        yield Static(printer_descriptor(), id="topbar-printer", markup=False)
        badge = Static("", id="topbar-badge", markup=False)
        badge.display = False
        yield badge

    def show_status(self, badge: StateBadge | None, extra: str | None = None) -> None:
        """Show the state badge (hidden for ``None``) and an optional extra fact."""
        printer = printer_descriptor()
        self.query_one("#topbar-printer", Static).update(f"{printer} · {extra}" if extra else printer)
        widget = self.query_one("#topbar-badge", Static)
        widget.display = badge is not None
        if badge is not None:
            widget.update(f"● {badge.label}")
            set_tone(widget, badge.tone)
