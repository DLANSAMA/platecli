"""The platecli colour themes (view layer only).

``platecli`` is the default: a dark theme in the project's teal, from the
social card. ``platecli-light`` is its light twin, for anyone who switches theme
from the command palette (ctrl+p). Every colour the TUI draws comes from these
theme variables, except an AMS swatch, which is the filament's own colour.
"""

from __future__ import annotations

from textual.theme import Theme

PLATE_DARK = Theme(
    name="platecli",
    primary="#2DD4BF",
    secondary="#38BDF8",
    accent="#F59E0B",
    foreground="#E6EDF3",
    background="#0D1117",
    surface="#151B23",
    panel="#1C2430",
    success="#4ADE80",
    warning="#FBBF24",
    error="#F87171",
    dark=True,
)

PLATE_LIGHT = Theme(
    name="platecli-light",
    primary="#0F766E",
    secondary="#0369A1",
    accent="#B45309",
    foreground="#1F2328",
    background="#F6F8FA",
    surface="#FFFFFF",
    panel="#E3E8EE",
    success="#15803D",
    warning="#A16207",
    error="#B91C1C",
    dark=False,
)

THEMES = (PLATE_DARK, PLATE_LIGHT)
DEFAULT_THEME = PLATE_DARK.name
