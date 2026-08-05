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

from rich.table import Table
from rich.text import Text


def summary_grid(rows: Any) -> Table:
    """Render label/value rows as a compact grid (an empty grid if none).

    Text(), not str: these rows carry filenames and slicer output, and a str
    cell is parsed as Rich markup — "model [remix].stl" would render as
    "model .stl", and "a[/b]c.gcode" would raise MarkupError mid-render.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold")
    # overflow="fold" so an unbreakable token (a long filename with no spaces)
    # folds inside the value column instead of widening the grid past its box.
    table.add_column(overflow="fold")
    for label, value in rows or []:
        table.add_row(Text(str(label)), Text(str(value), overflow="fold"))
    return table
