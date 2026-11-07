"""The ``?`` help overlay: every key the TUI answers to, grouped by screen.

``HELP_ROWS`` is the single source of truth for what the overlay prints, and
``tests/test_tui_polish.py`` cross-checks it against the real ``BINDINGS`` of the
app and of every screen — so a key that stops working, or a new key nobody
documented, fails a test instead of quietly misleading the user.

The overlay is a modal: it never disturbs the screen underneath, and Esc / ? / q
all close it (q closes the overlay rather than quitting the app, because a help
screen that quits on the key it is currently explaining would be a trap).
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

# (screen label, [(key, what it does)]) — mirrored by the screens' BINDINGS.
HELP_ROWS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Anywhere",
        [
            ("?", "Show this help"),
            ("f1", "Help, even while typing"),
            ("q", "Quit (waits for an upload)"),
            ("ctrl+q", "Quit (same guard)"),
        ],
    ),
    (
        "Dashboard",
        [
            ("r", "Refresh now"),
            ("n", "New print"),
            ("m", "Watch the running job"),
        ],
    ),
    (
        "Prepare",
        [
            ("enter", "Prepare the model"),
            ("s", "Slice settings"),
            ("escape", "Back to the dashboard"),
        ],
    ),
    (
        "Settings",
        [
            ("escape", "Back without applying"),
        ],
    ),
    (
        "Confirm",
        [
            ("escape", "Back; nothing is sent"),
        ],
    ),
    (
        "Monitor",
        [
            ("escape", "Back; it keeps printing"),
        ],
    ),
]

_FOOTNOTE = (
    "Printing only ever starts from the confirm dialog. Declining keeps the "
    "sliced file and tells you where. Leaving the monitor never stops a print."
)


def help_text() -> str:
    """Render the help table as plain text (pure — unit-testable without a pilot)."""
    lines: list[str] = []
    for section, rows in HELP_ROWS:
        lines.append(section)
        lines.extend(f"  {key:<8}{description}" for key, description in rows)
        lines.append("")
    lines.append(_FOOTNOTE)
    return "\n".join(lines)


# The footer spells it "esc"; the overlay should agree with the footer.
_KEY_NAMES = {"escape": "esc"}
_KEY_WIDTH = max(len(_KEY_NAMES.get(key, key)) for _section_name, rows in HELP_ROWS for key, _desc in rows)


def _section(title: str, rows: list[tuple[str, str]]) -> Group:
    # One fixed key width for every section, so the descriptions line up down
    # the whole column rather than per section.
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True, style="bold", width=_KEY_WIDTH)
    table.add_column()
    for key, description in rows:
        table.add_row(Text(_KEY_NAMES.get(key, key)), Text(description))
    # A span, not Text(style=...): a line style fills the whole column width,
    # which underlined empty space out to the next column.
    return Group(Text.assemble((title, "bold underline")), table)


def help_grid() -> Group:
    """The overlay's layout: the sections in two balanced columns, then the footnote.

    Two columns so the whole reference fits an 80x24 terminal without
    scrolling; ``help_text`` is the same content as plain lines.
    """
    split = _balanced_split([len(rows) + 1 for _section_name, rows in HELP_ROWS])
    sections = [_section(section, rows) for section, rows in HELP_ROWS]
    left, right = sections[:split], sections[split:]
    columns = Table.grid(padding=(0, 2), expand=True)
    columns.add_column(ratio=1)
    columns.add_column(ratio=1)
    columns.add_row(Group(*_spaced(left)), Group(*_spaced(right)))
    return Group(columns, Text(""), Text(_FOOTNOTE, style="italic"))


def _balanced_split(heights: list[int]) -> int:
    """Where to cut the sections (kept in order) so the taller column is shortest."""

    def column(part: list[int]) -> int:
        return sum(part) + max(len(part) - 1, 0)

    return min(range(1, len(heights)), key=lambda cut: max(column(heights[:cut]), column(heights[cut:])))


def _spaced(tables: list[Group]) -> list[Any]:
    out: list[Any] = []
    for index, table in enumerate(tables):
        if index:
            out.append(Text(""))
        out.append(table)
    return out


class HelpScreen(ModalScreen[None]):
    """Key reference overlay (Esc / ? / q close it)."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("question_mark", "close", "Close"),
        ("f1", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def on_mount(self) -> None:
        self.query_one("#help-body").border_title = "Keys"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-body"):
            yield Static(help_grid(), id="help-keys")
        yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)
