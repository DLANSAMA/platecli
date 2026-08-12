"""Printer status command."""

import dataclasses

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.context import RuntimeContext
from bambu_cli.contracts import PrinterState, Status
from bambu_cli.errors import PrinterConnectionError
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json

_PRINTER_STATE_FIELDS = {f.name for f in dataclasses.fields(PrinterState)}


def _status_payload(data, ams):
    """Build the Status contract. Firmware extras stay on ``printer``, never the envelope."""
    known = {key: data[key] for key in _PRINTER_STATE_FIELDS if key in data and key != "ams"}
    extras = {
        key: value
        for key, value in data.items()
        if key not in _PRINTER_STATE_FIELDS and key not in {"status", "command", "printer", "ams"}
    }
    payload = Status(status="ok", command="status", printer=PrinterState(**known), ams=ams).to_payload()
    if extras:
        printer = dict(payload["printer"])
        printer.update(extras)
        payload["printer"] = printer
    return payload


def cmd_status(args, ctx=None):
    """Query and display the printer's current status."""
    from bambu_cli.ams import parse_ams
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.protocols.mqtt import monitor_status

    ctx = ctx or RuntimeContext.for_request(args)
    if bool(_namespace_get(args, "monitor", False)):
        monitor_status(args, ctx.printer())
        return

    printer = ctx.printer()
    data = printer.status()
    if not data:
        raise PrinterConnectionError(
            "Could not connect to printer.",
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
        )

    ams = parse_ams(data)

    if bool(_namespace_get(args, "json", False)):
        emit_json(_status_payload(data, ams))
        return

    state = data.get("gcode_state", "UNKNOWN")
    pct = data.get("mc_percent", 0)
    layer = data.get("layer_num", 0)
    total_layers = data.get("total_layer_num", 0)
    try:
        remaining = int(data.get("mc_remaining_time", 0))
    except (TypeError, ValueError):
        remaining = 0
    filename = data.get("gcode_file", "")
    bed_temp = data.get("bed_temper", "?")
    bed_target = data.get("bed_target_temper", "?")
    nozzle_temp = data.get("nozzle_temper", "?")
    nozzle_target = data.get("nozzle_target_temper", "?")
    fan = data.get("cooling_fan_speed", "?")
    wifi = str(data.get("wifi_signal", "?")).replace("dBm", "")

    logger.info("🖨️  Bambu Printer Status")
    logger.info(f"   State: {state}")
    if state == "RUNNING":
        hrs, mins = divmod(remaining, 60)
        logger.info(f"   File: {filename}")
        logger.info(f"   Progress: {pct}% | Layer {layer}/{total_layers}")
        logger.info(f"   Time left: {hrs}h {mins}m")
    logger.info(f"   Bed: {bed_temp}°C / {bed_target}°C")
    logger.info(f"   Nozzle: {nozzle_temp}°C / {nozzle_target}°C")
    logger.info(f"   Fan: {fan} | WiFi: {wifi}dBm")

    if ams and ams["units"]:
        logger.info("   AMS:")
        for unit in ams["units"]:
            logger.info(f"     Unit {unit['id']} (humidity {unit['humidity']}, {unit['temp']}°C)")
            for tray in unit["trays"]:
                marker = "▶ " if tray["active"] else "  "
                if tray["empty"]:
                    logger.info(f"       {marker}Slot {tray['slot']}: empty")
                else:
                    color = f" #{tray['color']}" if tray["color"] else ""
                    remain = f" | {tray['remain']}%" if tray["remain"] is not None else ""
                    logger.info(f"       {marker}Slot {tray['slot']}: {tray['type']}{color}{remain}")
