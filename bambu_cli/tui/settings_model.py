"""Field table and pure logic behind the advanced-settings screen.

No Textual imports live here: the screen renders from ``SETTING_FIELDS`` and
calls these functions, so every decision (what a field accepts, how a typed
value is parsed) is unit-testable without a pilot — the plan's view/logic split.

Every field maps 1:1 onto a ``slice`` parser *dest*. Nothing here invents slicer
vocabulary — the named fields are the CLI's named flags. Blank means "no
override" everywhere, matching CLI semantics where an unset flag leaves the
profile default alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROCESS = "process"
FILAMENT = "filament"


@dataclass(frozen=True)
class SettingField:
    """One form field: a slice parser dest plus how to render and parse it."""

    dest: str
    label: str
    group: str
    kind: str  # "int" | "float" | "text" | "choice"
    hint: str = ""
    choices: tuple[str, ...] = ()

    @property
    def widget_id(self) -> str:
        return f"set-{self.dest.replace('_', '-')}"


SETTING_FIELDS: tuple[SettingField, ...] = (
    # Quality
    SettingField("layer_height", "Layer height (mm)", "Quality", "float", "e.g. 0.16"),
    SettingField("first_layer_height", "First layer height (mm)", "Quality", "float", "e.g. 0.2"),
    # Strength
    SettingField("infill", "Infill (%)", "Strength", "int", "0-100"),
    SettingField("pattern", "Infill pattern", "Strength", "text", "e.g. grid, gyroid"),
    SettingField("walls", "Walls", "Strength", "int", "perimeter count"),
    SettingField("wall_type", "Wall type", "Strength", "choice", "normal | classic", ("normal", "classic")),
    SettingField("top_layers", "Top layers", "Strength", "int", "e.g. 4"),
    SettingField("bottom_layers", "Bottom layers", "Strength", "int", "e.g. 3"),
    # Supports
    SettingField("support_type", "Support type", "Supports", "choice", "tree | normal", ("tree", "normal")),
    SettingField("support_threshold", "Support threshold (°)", "Supports", "float", "0-90"),
    SettingField("support_interface_density", "Interface density (%)", "Supports", "float", "0-100"),
    # Adhesion
    SettingField("brim", "Brim width (mm)", "Adhesion", "float", "0 disables"),
    # Filament
    SettingField("nozzle_temp", "Nozzle temp (°C)", "Filament", "int", "e.g. 220"),
    SettingField("bed_temp", "Bed temp (°C)", "Filament", "int", "e.g. 60"),
    SettingField("fan_speed", "Fan speed (%)", "Filament", "float", "0-100"),
    SettingField("flow_ratio", "Flow ratio", "Filament", "float", "e.g. 0.98"),
    # Speed
    SettingField("speed", "Print speed (mm/s)", "Speed", "float", "e.g. 200"),
    SettingField("accel_wall", "Inner wall accel (mm/s²)", "Speed", "int", "e.g. 5000"),
    SettingField("accel_wall_outer", "Outer wall accel (mm/s²)", "Speed", "int", "e.g. 5000"),
    SettingField("accel_infill", "Infill accel (mm/s²)", "Speed", "int", "e.g. 8000"),
    SettingField("accel_travel", "Travel accel (mm/s²)", "Speed", "int", "e.g. 10000"),
    SettingField("accel_first_layer", "First layer accel (mm/s²)", "Speed", "int", "e.g. 500"),
    # Plate
    SettingField("copies", "Copies", "Plate", "int", "1 or more"),
    SettingField(
        "seam_position",
        "Seam position",
        "Plate",
        "choice",
        "nearest | aligned | back | random",
        ("nearest", "aligned", "back", "random"),
    ),
    SettingField(
        "ironing",
        "Ironing",
        "Plate",
        "choice",
        "none | top | topmost | solid",
        ("none", "top", "topmost", "solid"),
    ),
)

GROUP_ORDER: tuple[str, ...] = ("Quality", "Strength", "Supports", "Adhesion", "Filament", "Speed", "Plate")


def fields_by_group() -> list[tuple[str, list[SettingField]]]:
    """The field table grouped for rendering, in a stable order."""
    return [(group, [f for f in SETTING_FIELDS if f.group == group]) for group in GROUP_ORDER]


def field_for(dest: str) -> SettingField | None:
    for candidate in SETTING_FIELDS:
        if candidate.dest == dest:
            return candidate
    return None


def parse_field_value(field: SettingField, raw: str) -> tuple[Any, str | None]:
    """Parse one typed field value: ``(value, error)``; blank ⇒ ``(None, None)``.

    Type errors are caught here so the user hears about "abc" immediately.
    *Range* is deliberately NOT checked here — the printer-safety bounds live in
    ``_validate_slice_options`` and are applied through
    ``core.overrides_problem`` so there is exactly one definition of "unsafe".
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    if field.kind == "int":
        try:
            return int(text), None
        except ValueError:
            return None, f"{field.label}: expected a whole number (got {text!r})"
    if field.kind == "float":
        try:
            return float(text), None
        except ValueError:
            return None, f"{field.label}: expected a number (got {text!r})"
    if field.kind == "choice" and field.choices and text not in field.choices:
        return None, f"{field.label}: expected one of {', '.join(field.choices)} (got {text!r})"
    return text, None


def collect_field_overrides(raw_values: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Parse a ``{dest: raw_text}`` form snapshot into ``({dest: value}, errors)``."""
    parsed: dict[str, Any] = {}
    errors: list[str] = []
    for field in SETTING_FIELDS:
        value, error = parse_field_value(field, raw_values.get(field.dest, ""))
        if error:
            errors.append(error)
        elif value is not None:
            parsed[field.dest] = value
    return parsed, errors
