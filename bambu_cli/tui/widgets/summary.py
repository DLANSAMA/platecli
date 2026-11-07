"""The one label/value grid used for ``preview_rows`` output.

Two screens render the same ``PrepareResult.rows``: the prepare screen's preview
and the confirmation modal's summary. They were formatted separately, and the
prepare side used ``f"{label:<11}{value}"`` — which lays out fine until a value
wraps, at which point the continuation starts in column 0, *inside* the label
column, so "Bambu Lab P1S, 0.4mm nozzle" rendered as a "Printer" row followed by
a line that reads like a field called "nozzle". A real two-column grid wraps the
value against the value column and leaves the label column blank, which is what
this module exists to guarantee for both callers.

View layer only — no domain logic; the rows come from ``interactive.core``.
"""

from __future__ import annotations

from typing import Any

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text

# The separator ``interactive.core.preview_rows`` puts between the facts of one
# row ("PLA  ·  Quality: standard (0.20mm)  ·  Supports: no").
SEPARATOR = "  ·  "


class SeparatedValue:
    """A row value that wraps only between its ``·`` parts, never inside one.

    Rich wraps at any space (a non-breaking one included), so a narrow column
    split "Supports: no" into "Supports:" and a lone "no" on the next line, and
    "60 °C" into "60" and "°C". This packs whole parts onto each line instead.
    A single part wider than the column still wraps (and folds an unbreakable
    token), because cutting it off would hide what is about to be printed.
    """

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        longest = max(cell_len(part) for part in self.parts)
        full = cell_len(SEPARATOR.join(self.parts))
        return Measurement(min(longest, options.max_width), min(full, options.max_width))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        lines: list[str] = []
        current = ""
        for part in self.parts:
            candidate = f"{current}{SEPARATOR}{part}" if current else part
            if current and cell_len(candidate) > width:
                lines.append(current)
                current = part
            else:
                current = candidate
        lines.append(current)
        for line in lines:
            yield Text(line, overflow="fold")


def _value(value: Any) -> Text | SeparatedValue:
    text = str(value)
    parts = text.split(SEPARATOR)
    if len(parts) > 1 and all(parts):
        return SeparatedValue(parts)
    # overflow="fold" so an unbreakable token (a long filename with no spaces)
    # folds inside the value column instead of widening the grid past its box.
    return Text(text, overflow="fold")


def summary_grid(rows: Any) -> Table:
    """Render label/value rows as a compact grid (an empty grid if none).

    Text(), not str: these rows carry filenames and slicer output, and a str
    cell is parsed as Rich markup — "model [remix].stl" would render as
    "model .stl", and "a[/b]c.gcode" would raise MarkupError mid-render.
    ``SeparatedValue`` renders ``Text`` too, for the same reason.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold", no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows or []:
        table.add_row(Text(str(label)), _value(value))
    return table
