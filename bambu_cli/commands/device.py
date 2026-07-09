"""Device control: light, pause, resume, stop."""

import json

from bambu_cli.cli import _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json, emit_json_error, get_sequence_id


def cmd_light(args, ctx=None):
    """Control chamber light."""

    ctx = ctx or RuntimeContext.for_request(args)
    action = args.action  # on or off
    val = "on" if action == "on" else "off"
    payload = json.dumps(
        {
            "system": {
                "sequence_id": get_sequence_id(),
                "command": "ledctrl",
                "led_node": "chamber_light",
                "led_mode": val,
                "led_on_time": 500,
                "led_off_time": 500,
            }
        }
    )
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send light command."
        logger.error(message)
        emit_json_error(args, "light", EXIT_NETWORK_ERROR, message, failed_step="mqtt", action=action, changed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info(f"💡 Light turned {action}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "light_changed",
                "command": "light",
                "action": action,
                "changed": True,
            }
        )


def cmd_pause(args, ctx=None):
    """Pause current print."""

    ctx = ctx or RuntimeContext.for_request(args)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "pause"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send pause command."
        logger.error(message)
        emit_json_error(args, "pause", EXIT_NETWORK_ERROR, message, failed_step="mqtt", paused=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("⏸️  Print paused")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "paused",
                "command": "pause",
                "paused": True,
            }
        )


def cmd_resume(args, ctx=None):
    """Resume paused print."""

    ctx = ctx or RuntimeContext.for_request(args)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "resume"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send resume command."
        logger.error(message)
        emit_json_error(args, "resume", EXIT_NETWORK_ERROR, message, failed_step="mqtt", resumed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("▶️  Print resumed")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "resumed",
                "command": "resume",
                "resumed": True,
            }
        )


def cmd_stop(args, ctx=None):
    """Stop current print."""

    ctx = ctx or RuntimeContext.for_request(args)
    if not args.confirm:
        logger.warning("⚠️  This will STOP the current print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "stop",
                    "stopped": False,
                    "next_command": ["stop", "--confirm", "--json"],
                }
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "stop"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send stop command."
        logger.error(message)
        emit_json_error(args, "stop", EXIT_NETWORK_ERROR, message, failed_step="mqtt", stopped=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("⏹️  Print stopped")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "stopped",
                "command": "stop",
                "stopped": True,
            }
        )
