"""Heater cards: the reading, what the heater is doing, and a history sparkline.

View layer only. ``services.temp_view`` words the reading and
``services.TempHistory`` keeps the samples; this module draws them. The
sparkline is drawn on a fixed scale from 0 °C (``spark_ceiling``): Textual's own
``Sparkline`` stretches whatever range the data has to the full height, so a
nozzle holding 220 ± 1 °C would look like it was swinging wildly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from bambu_cli.tui.services import TempView

_BLOCKS = " ▁▂▃▄▅▆▇█"


def spark_ceiling(series: Sequence[float], target: float | None, scale: float) -> float:
    """The top of the sparkline's scale: the heater's usual maximum, or more if exceeded."""
    return max([scale, target or 0.0, *series])


def spark_rows(series: Sequence[float], width: int, height: int, ceiling: float) -> list[str]:
    """Draw the newest ``width`` samples as ``height`` rows of block characters.

    The newest sample is at the right edge; with fewer samples than columns the
    left is blank (no history yet, rather than a made-up flat line).
    """
    width, height = max(width, 0), max(height, 1)
    samples = list(series)[-width:] if width else []
    levels = [round(max(0.0, min(1.0, value / ceiling)) * height * 8) if ceiling > 0 else 0 for value in samples]
    levels = [0] * (width - len(levels)) + levels
    rows = []
    for row in range(height):
        floor = (height - 1 - row) * 8
        rows.append("".join(_BLOCKS[max(0, min(8, level - floor))] for level in levels))
    return rows


class Spark(Widget):
    """A fixed-scale, multi-row sparkline; columns with no sample yet show a dim baseline."""

    COMPONENT_CLASSES = {"spark--bar", "spark--baseline"}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.series: list[float] = []
        self.ceiling = 1.0

    def set_series(self, series: Sequence[float], ceiling: float) -> None:
        self.series = list(series)
        self.ceiling = ceiling
        self.refresh()

    def render(self) -> Text:
        width, height = self.content_size.width, self.content_size.height
        rows = spark_rows(self.series, width, height, self.ceiling)
        missing = max(width - len(self.series), 0)
        bar = self.get_component_rich_style("spark--bar")
        baseline = self.get_component_rich_style("spark--baseline")
        text = Text(no_wrap=True)
        for index, row in enumerate(rows):
            last = index == len(rows) - 1
            # The bottom row of an empty column is a dim rule, so an empty
            # history reads as a chart waiting for data, not as nothing.
            text.append(("─" if last else " ") * missing, style=baseline)
            text.append(row[missing:], style=bar)
            if not last:
                text.append("\n")
        return text


class TempCard(Vertical):
    """``Nozzle``: ``220 °C   target 220 °C`` over a sparkline of recent readings."""

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.border_title = title

    def compose(self) -> ComposeResult:
        with Horizontal(classes="temp-reading"):
            yield Static("—", classes="temp-value", markup=False)
            yield Static("", classes="temp-caption", markup=False)
        yield Spark(classes="temp-spark")

    def show(self, view: TempView, series: Sequence[float], scale: float) -> None:
        self.query_one(".temp-value", Static).update(view.value)
        self.query_one(".temp-caption", Static).update(view.caption if view.current is not None else "")
        self.set_class(view.changing is not None, "-heating")
        self.query_one(Spark).set_series(series, spark_ceiling(series, view.target, scale))
