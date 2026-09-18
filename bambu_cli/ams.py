"""Parse the AMS (Automatic Material System) block from a printer status payload.

The printer reports AMS state inside the MQTT ``print`` payload under ``ams``.
That structure is verbose, string-heavy, and hardware-shaped. This module
normalizes it into a compact form an agent can use to reason about what
filament is loaded where and to build a correct ``--ams-mapping`` argument.
"""


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_color(raw):
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


def parse_ams(status):
    """Normalize the AMS section of a printer status payload.

    Returns ``None`` when the payload carries no AMS data and no external spool
    (printers without an AMS or loaded spool, or a simulated status).
    Otherwise returns::

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
          "external_tray": {
            "slot": 254, "type": "PLA", "color": "FFFFFF",
            "remain": None, "empty": False, "active": False,
          } | None,
        }

    ``active`` marks the tray whose absolute index (``unit_id * 4 + slot``)
    matches ``tray_now``. ``remain`` is the reported percentage (may be -1 when
    the printer cannot measure it) or None when absent.
    """
    if not isinstance(status, dict):
        return None

    ams_block = status.get("ams")
    units_raw = ams_block.get("ams") if isinstance(ams_block, dict) else None

    # Resolve raw tray_now before sentinel normalization
    raw_tray_now = None
    if isinstance(ams_block, dict):
        raw_tray_now = _to_int(ams_block.get("tray_now"))
    if raw_tray_now is None:
        raw_tray_now = _to_int(status.get("tray_now"))

    # Resolve vt_tray (external spool / virtual tray)
    vt_tray_raw = status.get("vt_tray")
    if vt_tray_raw is None and isinstance(ams_block, dict):
        vt_tray_raw = ams_block.get("vt_tray")
    if vt_tray_raw is None and isinstance(status.get("print"), dict):
        vt_tray_raw = status["print"].get("vt_tray")

    external_tray = None
    if isinstance(vt_tray_raw, dict) and any(vt_tray_raw.values()):
        vt_slot = _to_int(vt_tray_raw.get("id"), default=254)
        if vt_slot is None:
            vt_slot = 254
        vt_type = vt_tray_raw.get("tray_type") or None
        vt_color = _normalize_color(vt_tray_raw.get("tray_color"))
        vt_remain = _to_int(vt_tray_raw.get("remain"))
        is_vt_active = raw_tray_now in (254, 255) if raw_tray_now is not None else False
        external_tray = {
            "slot": vt_slot,
            "type": vt_type,
            "color": vt_color,
            "remain": vt_remain,
            "empty": not vt_type,
            "active": is_vt_active,
        }

    if (not isinstance(units_raw, list) or not units_raw) and external_tray is None:
        return None

    active_tray = raw_tray_now
    # Bambu firmware reports tray_now 254/255 as a sentinel for the external
    # spool / nothing loaded, not a real AMS slot index. Normalize those to None
    # so no tray is falsely marked active and consumers don't present an
    # external-spool state as an AMS detection.
    if active_tray in (254, 255):
        active_tray = None

    units = []
    if isinstance(units_raw, list):
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
    return {"active_tray": active_tray, "units": units, "external_tray": external_tray}
