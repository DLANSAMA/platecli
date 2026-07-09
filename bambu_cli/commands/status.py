"""Printer status command."""

from bambu_cli.cli import _namespace_get
from bambu_cli.context import RuntimeContext
from bambu_cli.errors import PrinterConnectionError
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json


def cmd_status(args, ctx=None):
    """Query and display the printer's current status."""
    from bambu_cli.ams import parse_ams
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.protocols.mqtt import monitor_status

    ctx = ctx or RuntimeContext.for_request(args)
    if bool(_namespace_get(args, "monitor", False)):
        monitor_status(args)
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

    if args.json:
        payload = {
            "status": "ok",
            "command": "status",
            "printer": data,
        }
        payload.update({k: v for k, v in data.items() if k not in ("status", "command")})
        # Normalized AMS view (trays/filaments) for agents building --ams-mapping;
        # None on printers without an AMS.
        payload["ams"] = ams
        emit_json(payload)
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
