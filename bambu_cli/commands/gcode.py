"""Send raw G-code via MQTT."""

import json

from bambu_cli.cli import _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.download.naming import _has_command_injection_chars
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json, emit_json_error, get_sequence_id


def cmd_gcode(args, ctx=None):
    """Send raw G-code to the printer via MQTT."""

    ctx = ctx or RuntimeContext.for_request(args)
    gcode = str(args.code if args.code is not None else "")

    # Reject empty/whitespace-only and CR/LF/NUL (shared helper with remote-name
    # sanitization — same command-injection risk on MQTT as on FTP lines).
    if not gcode.strip() or _has_command_injection_chars(gcode):
        message = "Invalid G-code: must be non-empty and must not contain control characters (CR/LF/NUL)."
        logger.error(message)
        emit_json_error(args, "gcode", EXIT_COMMAND_ERROR, message, failed_step="validate", gcode=gcode, sent=False)
        abort("", exit_code=EXIT_COMMAND_ERROR)

    if not args.confirm:
        logger.warning("⚠️  This will SEND raw G-code to the printer. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "gcode",
                    "gcode": gcode,
                    "sent": False,
                    "next_command": ["gcode", gcode, "--confirm", "--json"],
                }
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)

    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "gcode_line", "param": gcode}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send G-code command."
        logger.error(message)
        emit_json_error(args, "gcode", EXIT_NETWORK_ERROR, message, failed_step="mqtt", gcode=gcode, sent=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info(f"📡 Sent: {gcode}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "sent",
                "command": "gcode",
                "gcode": gcode,
                "sent": True,
            }
        )
