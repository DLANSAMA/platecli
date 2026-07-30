"""AMS panel: units and trays, with the active tray highlighted.

View layer only. It renders the rows ``services.ams_tray_rows`` produces; the
"which tray is active" and "how full" decisions are made in ``services``.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from bambu_cli.tui.services import StatusSnapshot, ams_tray_rows


class AmsPanel(Static):
    """Renders the AMS trays from a status snapshot as a highlighted table."""

    def on_mount(self) -> None:
        self.update_snapshot(StatusSnapshot(ok=False, error="Loading AMS…"))

    def update_snapshot(self, snapshot: StatusSnapshot) -> None:
        rows = ams_tray_rows(snapshot)
        if not rows:
            self.update("AMS unavailable" if not snapshot.ok else "No AMS detected")
            return

        table = Table.grid(padding=(0, 2))
        table.add_column(justify="left", style="bold")
        table.add_column()
        table.add_column(justify="right")
        for row in rows:
            marker = "●" if row["active"] else " "
            style = "bold green" if row["active"] else ("dim" if row["empty"] else "")
            label = Text(f"{marker} {row['label']}", style=style)
            table.add_row(label, str(row["type"]), str(row["remain"]))
        self.update(table)
