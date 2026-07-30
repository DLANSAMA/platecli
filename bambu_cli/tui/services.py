"""Thin sync adapters between the Textual view layer and the domain.

Screens and widgets hold *no* domain logic (plan §4.1 view/logic split): every
decision — how to reach the printer, how to normalize AMS data, how to describe
a failure — lives here in plain synchronous functions/dataclasses that are
unit-tested without a Textual pilot. The screens call these from thread workers
(``printer.status()`` blocks on a ``threading.Event``) and render the result.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StatusSnapshot:
    """A normalized, view-ready printer status snapshot.

    ``ok`` is False when the fetch failed (printer unreachable, timeout, etc.);
    ``error`` then carries a short human message and ``raw``/``ams`` are empty.
    This dataclass is what the dashboard renders, so a failure is an ordinary
    value the UI shows inline — it never surfaces as an exception that could
    crash the app.
    """

    ok: bool
    raw: dict[str, Any] = field(default_factory=dict)
    ams: dict[str, Any] | None = None
    error: str | None = None

    @property
    def gcode_state(self) -> str:
        return str(self.raw.get("gcode_state", "UNKNOWN"))


class StatusService:
    """Fetch a normalized status snapshot from the configured/simulated printer.

    Wraps ``RuntimeContext.for_request(args).printer().status()`` and
    ``parse_ams``. NEVER raises: any failure (MQTT error, timeout, missing
    config) is captured into ``StatusSnapshot(ok=False, error=...)`` so the
    dashboard renders an inline "printer unreachable" state instead of crashing.
    """

    def fetch(self, args: argparse.Namespace) -> StatusSnapshot:
        try:
            from bambu_cli.ams import parse_ams
            from bambu_cli.context import RuntimeContext

            ctx = RuntimeContext.for_request(args)
            data = ctx.printer().status()
            if not isinstance(data, dict) or not data:
                return StatusSnapshot(ok=False, error="Printer returned no status.")
            ams = parse_ams(data)
            return StatusSnapshot(ok=True, raw=data, ams=ams)
        except Exception as exc:  # noqa: BLE001 -- see class docstring: never crash the UI
            return StatusSnapshot(ok=False, error=_short_error(exc))


def _short_error(exc: Exception) -> str:
    """Render an exception as a single short line for the unreachable state."""
    message = str(exc).strip()
    if message:
        return message
    return f"Printer unreachable ({type(exc).__name__})."


# --- View-model helpers (pure; unit-tested without a pilot) -----------------


def status_lines(snapshot: StatusSnapshot) -> list[tuple[str, str]]:
    """Return ``(label, value)`` rows describing the printer status panel.

    Pure formatting over a snapshot — no I/O — so the widget only has to render
    strings. On a failed snapshot returns a single explanatory row.
    """
    if not snapshot.ok:
        return [("State", snapshot.error or "Printer unreachable.")]

    raw = snapshot.raw

    def _temp(cur_key: str, target_key: str) -> str:
        cur = raw.get(cur_key)
        target = raw.get(target_key)
        if cur is None:
            return "—"
        if target:
            return f"{cur}°C → {target}°C"
        return f"{cur}°C"

    rows: list[tuple[str, str]] = [
        ("State", str(raw.get("gcode_state", "UNKNOWN"))),
        ("Progress", f"{raw.get('mc_percent', 0)}%"),
        ("Nozzle", _temp("nozzle_temper", "nozzle_target_temper")),
        ("Bed", _temp("bed_temper", "bed_target_temper")),
    ]
    layer = raw.get("layer_num")
    total_layer = raw.get("total_layer_num")
    if layer is not None or total_layer is not None:
        rows.append(("Layer", f"{layer or 0} / {total_layer or 0}"))
    if raw.get("wifi_signal"):
        rows.append(("Wi-Fi", str(raw.get("wifi_signal"))))
    if raw.get("gcode_file"):
        rows.append(("File", str(raw.get("gcode_file"))))
    return rows


def ams_tray_rows(snapshot: StatusSnapshot) -> list[dict[str, Any]]:
    """Flatten the AMS units/trays into view-ready rows, or ``[]`` when absent.

    Each row: ``{"unit": int, "slot": int, "label": str, "type": str,
    "color": str|None, "remain": str, "active": bool, "empty": bool}``.
    """
    if not snapshot.ok or not snapshot.ams:
        return []
    rows: list[dict[str, Any]] = []
    for unit in snapshot.ams.get("units", []):
        unit_id = unit.get("id", 0)
        for tray in unit.get("trays", []):
            slot = tray.get("slot", 0)
            empty = bool(tray.get("empty"))
            ftype = tray.get("type") or ("empty" if empty else "?")
            remain = tray.get("remain")
            remain_label = "—" if remain is None or remain < 0 else f"{remain}%"
            rows.append(
                {
                    "unit": unit_id,
                    "slot": slot,
                    "label": f"AMS{unit_id}·{slot}",
                    "type": ftype,
                    "color": tray.get("color"),
                    "remain": remain_label,
                    "active": bool(tray.get("active")),
                    "empty": empty,
                }
            )
    return rows
