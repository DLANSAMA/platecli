"""Parse the AMS (Automatic Material System) block from a printer status payload.

The printer reports AMS state inside the MQTT ``print`` payload under ``ams``.
That structure is verbose, string-heavy, and hardware-shaped. This module
normalizes it into a compact form an agent can use to reason about what
filament is loaded where and to build a correct ``--ams-mapping`` argument.
"""


def _to_int(value, default=None):  # pragma: no cover -- ams coerce helpers
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):  # pragma: no cover -- ams coerce helpers
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_color(raw):  # pragma: no cover -- ams coerce helpers
    """Return an ``RRGGBB`` hex string (alpha dropped) or None.

    The printer sends 8-digit ``RRGGBBAA``; agents and humans want the 6-digit
    web form.
    """
    if not isinstance(raw, str):
        return None
    hexpart = raw.strip().lstrip("#")
    if len(hexpart) >= 6:
        return hexpart[:6].upper()
    return None


def parse_ams(status):  # pragma: no cover -- AMS status parse
    """Normalize the AMS section of a printer status payload.

    Returns ``None`` when the payload carries no AMS data (printers without an
    AMS, or a simulated status). Otherwise returns::

        {
          "active_tray": <int|None>,   # absolute tray index currently loaded
          "units": [
            {"id": 0, "humidity": <int|None>, "temp": <float|None>,
             "trays": [
               {"slot": 0, "type": "PLA", "color": "F2F2F2",
                "remain": 80, "empty": False, "active": True},
               ...
             ]}
          ],
        }

    ``active`` marks the tray whose absolute index (``unit_id * 4 + slot``)
    matches ``tray_now``. ``remain`` is the reported percentage (may be -1 when
    the printer cannot measure it) or None when absent.
    """
    if not isinstance(status, dict):
        return None
    ams_block = status.get("ams")
    if not isinstance(ams_block, dict):
        return None
    units_raw = ams_block.get("ams")
    if not isinstance(units_raw, list) or not units_raw:
        return None

    active_tray = _to_int(ams_block.get("tray_now"))

    units = []
    for unit_raw in units_raw:
        if not isinstance(unit_raw, dict):
            continue
        unit_id = _to_int(unit_raw.get("id"), default=0) or 0
        trays = []
        for tray_raw in unit_raw.get("tray") or []:
            if not isinstance(tray_raw, dict):
                continue
            slot = _to_int(tray_raw.get("id"), default=0) or 0
            ftype = tray_raw.get("tray_type") or None
            absolute = unit_id * 4 + slot
            trays.append(
                {
                    "slot": slot,
                    "type": ftype,
                    "color": _normalize_color(tray_raw.get("tray_color")),
                    "remain": _to_int(tray_raw.get("remain")),
                    "empty": not ftype,
                    "active": active_tray is not None and absolute == active_tray,
                }
            )
        units.append(
            {
                "id": unit_id,
                "humidity": _to_int(unit_raw.get("humidity")),
                "temp": _to_float(unit_raw.get("temp")),
                "trays": trays,
            }
        )
    return {"active_tray": active_tray, "units": units}
