"""Printer-status panel: temps, state, progress, Wi-Fi, current file.

View layer only. It is handed a ``StatusSnapshot`` and renders the rows
``services.status_lines`` produces; it makes no decisions of its own.
"""

from __future__ import annotations

from rich.table import Table
from textual.widgets import Static

from bambu_cli.tui.services import StatusSnapshot, status_lines


class StatusPanel(Static):
    """Renders a single printer-status snapshot as a label/value table."""

    def on_mount(self) -> None:
        # A framed box with no title is a box the reader has to identify.
        self.border_title = "Printer"
        # Start with a placeholder; the screen calls update_snapshot once the
        # first fetch lands.
        self.update_snapshot(StatusSnapshot(ok=False, error="Loading printer status…"))

    def update_snapshot(self, snapshot: StatusSnapshot) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="bold")
        table.add_column()
        for label, value in status_lines(snapshot):
            table.add_row(label, value)
        self.update(table)
