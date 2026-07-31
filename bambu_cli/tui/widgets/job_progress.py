"""Job progress widget: percent, layers, remaining time (view only).

All formatting decisions live in ``tui.services`` (``job_progress_lines`` /
``progress_percent``) so they are unit-tested without a pilot; this widget only
turns those rows into a Rich table plus a bar.
"""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from bambu_cli.tui.services import StatusSnapshot, job_progress_lines, progress_percent

_BAR_WIDTH = 30


class JobProgress(Static):
    """Renders the live state of the running job."""

    def update_snapshot(self, snapshot: StatusSnapshot) -> None:
        self.update(_render(snapshot))


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
