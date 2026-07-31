"""Field table and pure logic behind the advanced-settings screen.

No Textual imports live here: the screen renders from ``SETTING_FIELDS`` and
calls these functions, so every decision (what a field accepts, how a typed
value is parsed, how the browser filters, which bucket a browsed key belongs to)
is unit-testable without a pilot — the plan's view/logic split.

Every field maps 1:1 onto a ``slice`` parser *dest*. Nothing here invents slicer
vocabulary: the named fields are the CLI's named flags, and the browser lists
whatever the installed profiles actually contain (the same discovery
``slice --list-settings`` uses). Blank means "no override" everywhere, matching
CLI semantics where an unset flag leaves the profile default alone.
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
    SettingField("top_layers", "Top layers", "Strength", "int"),
    SettingField("bottom_layers", "Bottom layers", "Strength", "int"),
    # Supports
    SettingField("support_type", "Support type", "Supports", "choice", "tree | normal", ("tree", "normal")),
    SettingField("support_threshold", "Support threshold (°)", "Supports", "float", "0-90"),
    SettingField("support_interface_density", "Interface density (%)", "Supports", "float"),
    # Adhesion
    SettingField("brim", "Brim width (mm)", "Adhesion", "float", "0 disables"),
    # Filament
    SettingField("nozzle_temp", "Nozzle temp (°C)", "Filament", "int"),
    SettingField("bed_temp", "Bed temp (°C)", "Filament", "int"),
    SettingField("fan_speed", "Fan speed (%)", "Filament", "float", "0-100"),
    SettingField("flow_ratio", "Flow ratio", "Filament", "float", "e.g. 0.98"),
    # Speed
    SettingField("speed", "Print speed (mm/s)", "Speed", "float"),
    SettingField("accel_wall", "Inner wall accel (mm/s²)", "Speed", "int"),
    SettingField("accel_wall_outer", "Outer wall accel (mm/s²)", "Speed", "int"),
    SettingField("accel_infill", "Infill accel (mm/s²)", "Speed", "int"),
    SettingField("accel_travel", "Travel accel (mm/s²)", "Speed", "int"),
    SettingField("accel_first_layer", "First layer accel (mm/s²)", "Speed", "int"),
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


# --- "All settings" browser -------------------------------------------------


# How a browsed setting should be edited. Derived from the values the installed
# profiles actually hold — never from a hand-written table of slicer vocabulary.
EDITOR_SWITCH = "switch"
EDITOR_SELECT = "select"
EDITOR_NUMBER = "number"
EDITOR_TEXT = "text"

# A choice list stops being a usable picker past a dozen entries, and a "value"
# longer than this is prose (a custom g-code block), not a choice.
_MAX_SELECT_CHOICES = 12
_MAX_CHOICE_LEN = 40


@dataclass(frozen=True)
class CatalogEntry:
    """One browsable OrcaSlicer setting: key, bucket, example, observed domain."""

    key: str
    kind: str  # PROCESS | FILAMENT
    example: str
    values: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"[{self.kind}] {self.key} = {self.example}"

    @property
    def editor(self) -> str:
        """Which control edits this setting: switch / select / number / text."""
        return editor_for(self.values)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def editor_for(values: tuple[str, ...]) -> str:
    """Pick an editor from a setting's observed values.

    Ordered deliberately: ``0``/``1`` are numeric too, so the boolean test runs
    first or every toggle in OrcaSlicer would render as a number box. Anything
    that is not confidently a toggle, a number, or a short closed set falls back
    to free text — the escape hatch has to stay, because a profile can hold a
    custom g-code block and no picker can represent that.
    """
    if not values:
        return EDITOR_TEXT
    if set(values) <= {"0", "1"}:
        return EDITOR_SWITCH
    if all(_is_number(v) for v in values):
        return EDITOR_NUMBER
    if len(values) <= _MAX_SELECT_CHOICES and all(v and len(v) <= _MAX_CHOICE_LEN and "\n" not in v for v in values):
        return EDITOR_SELECT
    return EDITOR_TEXT


def load_catalog(profiles_dir: str | None) -> list[CatalogEntry]:
    """Every settable key from the installed profiles, sorted, or ``[]``.

    Reads through ``slicer.options.setting_catalog`` — the same discovery
    ``slice --list-settings`` uses — so the browser and the agent surface list
    the same vocabulary. Any failure (no profiles configured, unreadable dir,
    ``--sim`` on a machine with no slicer) degrades to an empty catalog; the
    screen then offers free-form ``KEY=VALUE`` entry, which the CLI's
    warn-but-pass handling of unknown keys already tolerates.
    """
    if not profiles_dir:
        return []
    try:
        from bambu_cli.slicer.options import setting_catalog, setting_value_domains

        catalog = setting_catalog(profiles_dir)
        domains = setting_value_domains(profiles_dir)
    except Exception:  # noqa: BLE001 -- discovery is a nicety; never break the UI
        return []
    entries = [
        CatalogEntry(
            key=key,
            kind=kind,
            example=_example(value),
            values=domains.get(kind, {}).get(key, ()),
        )
        for kind in (PROCESS, FILAMENT)
        for key, value in catalog.get(kind, {}).items()
    ]
    return sorted(entries, key=lambda e: (e.key, e.kind))


def _example(value: Any) -> str:
    text = value[0] if isinstance(value, list) and value else value
    text = "" if text is None else str(text)
    return text if len(text) <= 40 else text[:37] + "…"


def filter_catalog(entries: list[CatalogEntry], query: str, limit: int = 200) -> list[CatalogEntry]:
    """Case-insensitive substring filter over keys (pure; drives the browser)."""
    needle = (query or "").strip().lower()
    if not needle:
        return entries[:limit]
    return [entry for entry in entries if needle in entry.key.lower()][:limit]


def bucket_for_key(entries: list[CatalogEntry], key: str, default: str = PROCESS) -> str:
    """Which override bucket a browsed key belongs to.

    The source profile decides — never a guess about the name. This is what
    keeps ``filament_flow_ratio`` out of ``--set`` (where OrcaSlicer would
    silently ignore it). Unknown keys fall back to ``default`` (process), which
    is what a bare ``--set`` does today.
    """
    key = (key or "").strip()
    for entry in entries:
        if entry.key == key:
            return entry.kind
    return default
