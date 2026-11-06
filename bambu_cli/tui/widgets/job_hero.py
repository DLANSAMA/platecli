"""The job panel: the big percentage, time left, layer, feed, and a full-width bar.

View layer only. ``services.job_view`` decides every word and number; this
widget lays them out and picks a colour from the badge's tone. With no progress
worth drawing (idle, getting ready, offline) the percentage and bar are hidden
and the first line reads as the headline.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Digits, Static

from bambu_cli.tui.services import JobView, StateBadge
from bambu_cli.tui.widgets.topbar import set_tone

_LINES = 3
CONNECTING = JobView(StateBadge("Connecting", "busy"), "", None, ("Connecting to the printer…",))


class WideBar(Widget):
    """A one-row progress bar drawn across the widget's whole width."""

    COMPONENT_CLASSES = {"widebar--done", "widebar--todo"}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.percent = 0

    def set_percent(self, percent: int) -> None:
        self.percent = max(0, min(100, int(percent)))
        self.refresh()

    def render(self) -> Text:
        width = max(self.content_size.width, 1)
        filled = round(width * self.percent / 100)
        return Text.assemble(
            ("━" * filled, self.get_component_rich_style("widebar--done")),
            ("━" * (width - filled), self.get_component_rich_style("widebar--todo")),
        )


class JobHero(Vertical):
    """Renders a ``JobView``: what the printer is doing, at a glance."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="hero-row"):
            yield Digits("", classes="hero-percent")
            with Vertical(classes="hero-lines"):
                for index in range(_LINES):
                    yield Static("", classes=f"hero-line hero-line-{index + 1}", markup=False)
        yield WideBar(classes="hero-bar")

    def on_mount(self) -> None:
        self.show(CONNECTING)

    def show(self, view: JobView) -> None:
        # Text, not str: a str border title is parsed as markup, and the job
        # name is printer-supplied.
        self.border_title = Text(view.name or "Printer")
        set_tone(self, view.badge.tone)
        has_percent = view.percent is not None
        self.set_class(not has_percent, "-message")
        digits = self.query_one(".hero-percent", Digits)
        digits.display = has_percent
        bar = self.query_one(".hero-bar", WideBar)
        bar.display = has_percent
        if view.percent is not None:
            digits.update(f"{view.percent}%")
            bar.set_percent(view.percent)
        for index, line in enumerate(self.query(".hero-line").results(Static)):
            line.update(view.lines[index] if index < len(view.lines) else "")
