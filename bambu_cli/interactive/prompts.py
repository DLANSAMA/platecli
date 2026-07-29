"""Thin prompt layer for the interactive wizard.

This is the ONLY module that touches interactive input (rich.prompt). Keeping it
tiny means the ``# pragma: no cover -- interactive prompt`` markers cover almost
nothing, and the whole layer is swappable for a future Textual front-end.

Every prompt writes chrome to **stderr** (matching ``setup_cmd/common.py``'s
stream discipline: prompts on stderr, machine data on stdout). ``KeyboardInterrupt``
(Ctrl-C) and ``EOFError`` (Ctrl-D / closed stdin) at any prompt return the
``CANCELLED`` sentinel, which ``session.py`` converts into "Operation cancelled by
user." + ``EXIT_COMMAND_ERROR`` — the same behavior as ``cli.main``'s top-level
Ctrl-C handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from rich.console import Console


class _Cancelled:
    """Singleton sentinel returned when the user aborts a prompt (Ctrl-C / EOF)."""

    _instance: _Cancelled | None = None

    def __new__(cls) -> _Cancelled:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "CANCELLED"

    def __bool__(self) -> bool:
        return False


CANCELLED = _Cancelled()


class Prompts:
    """Rich-backed prompt layer.

    All methods return either the answer or ``CANCELLED``. The ``session`` module
    checks ``is_cancelled(...)`` after each call. Tests inject ``ScriptedPrompts``
    (see tests) instead of instantiating this class, so the rich calls below stay
    behind the coverage pragma.
    """

    def __init__(self, console: Console | None = None) -> None:  # pragma: no cover -- interactive prompt
        from rich.console import Console as _Console

        self.console = console or _Console(stderr=True)

    def text(
        self, message: str, *, default: str | None = None
    ) -> str | _Cancelled:  # pragma: no cover -- interactive prompt
        from rich.prompt import Prompt

        try:
            return Prompt.ask(message, console=self.console, default=default)
        except (KeyboardInterrupt, EOFError):
            return CANCELLED

    def choice(
        self, message: str, choices: list[str], *, default: str | None = None
    ) -> str | _Cancelled:  # pragma: no cover -- interactive prompt
        from rich.prompt import Prompt

        try:
            return Prompt.ask(message, console=self.console, choices=choices, default=default)
        except (KeyboardInterrupt, EOFError):
            return CANCELLED

    def confirm(
        self, message: str, *, default: bool = False
    ) -> bool | _Cancelled:  # pragma: no cover -- interactive prompt
        from rich.prompt import Confirm

        try:
            return Confirm.ask(message, console=self.console, default=default)
        except (KeyboardInterrupt, EOFError):
            return CANCELLED

    def print(self, message: str = "") -> None:  # pragma: no cover -- interactive prompt
        self.console.print(message)


def is_cancelled(value: object) -> bool:
    """True when a prompt returned the cancellation sentinel."""
    return value is CANCELLED
