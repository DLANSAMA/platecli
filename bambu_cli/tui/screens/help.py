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
            ("f1", "Show this help (works while typing, too)"),
            ("q", "Quit (refused while an upload is in flight)"),
            ("ctrl+q", "Quit (same guard)"),
        ],
    ),
    (
        "Dashboard",
        [
            ("r", "Refresh printer status now"),
            ("n", "New print — source, presets, slice"),
            ("m", "Monitor the running job"),
        ],
    ),
    (
        "Prepare",
        [
            ("enter", "Prepare the model in the source box"),
            ("s", "Advanced slice settings"),
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
            ("escape", "Back to the preview — nothing is sent"),
        ],
    ),
    (
        "Monitor",
        [
            ("escape", "Back — the print keeps going"),
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
            yield Static("Keys", id="help-title")
            yield Static(help_text(), id="help-keys", markup=False)
        yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)
