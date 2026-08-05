"""Job progress widget: percent, layers, remaining time (view only).

All formatting decisions live in ``tui.services`` (``job_progress_lines`` /
``progress_percent``) so they are unit-tested without a pilot; this widget only
turns those rows into a Rich table plus a bar.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from bambu_cli.tui.services import StatusSnapshot, job_progress_lines, progress_percent

_BAR_WIDTH = 30


class JobProgress(Static):
    """Renders the live state of the running job.

    ``compact`` renders the bar alone. The dashboard already shows state,
    progress and layer in its printer panel, so the full table there would say
    everything twice; the monitor, which has the screen to itself, shows all of
    it.
    """

    def __init__(self, *args: Any, compact: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._compact = compact

    def update_snapshot(self, snapshot: StatusSnapshot) -> None:
        self.update(_bar(progress_percent(snapshot)) if self._compact else _render(snapshot))


def _bar(percent: int) -> Text:
    filled = round(_BAR_WIDTH * percent / 100)
    return Text(f"[{'█' * filled}{'░' * (_BAR_WIDTH - filled)}] {percent}%")


def _render(snapshot: StatusSnapshot) -> Group:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", no_wrap=True)
    table.add_column(justify="left")
    for label, value in job_progress_lines(snapshot):
        table.add_row(Text(label), Text(value))
    return Group(_bar(progress_percent(snapshot)), Text(""), table)
