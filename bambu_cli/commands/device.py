"""Device control: light, pause, resume, stop."""

import json

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.contracts import Light, Pause, Resume, Stop
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger, safe_log_error
from bambu_cli.utils import emit_json, get_sequence_id


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
        safe_log_error(message)
        abort(
            message,
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
            extra={"action": action, "changed": False},
            command="light",
        )
    logger.info(f"💡 Light turned {action}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(Light(status="light_changed", command="light", action=action, changed=True))


def cmd_pause(args, ctx=None):
    """Pause current print."""

    ctx = ctx or RuntimeContext.for_request(args)
    if not getattr(args, "confirm", False):
        logger.warning("⚠️  This will PAUSE the current print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                Pause(
                    status="confirmation_required",
                    command="pause",
                    paused=False,
                    next_command=["pause", "--confirm", "--json"],
                )
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "pause"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send pause command."
        safe_log_error(message)
        abort(
            message,
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
            extra={"paused": False},
            command="pause",
        )
    logger.info("⏸️  Print paused")
    if bool(_namespace_get(args, "json", False)):
        emit_json(Pause(status="paused", command="pause", paused=True))


def cmd_resume(args, ctx=None):
    """Resume paused print."""

    ctx = ctx or RuntimeContext.for_request(args)
    if not getattr(args, "confirm", False):
        logger.warning("⚠️  This will RESUME the paused print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                Resume(
                    status="confirmation_required",
                    command="resume",
                    resumed=False,
                    next_command=["resume", "--confirm", "--json"],
                )
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "resume"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send resume command."
        safe_log_error(message)
        abort(
            message,
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
            extra={"resumed": False},
            command="resume",
        )
    logger.info("▶️  Print resumed")
    if bool(_namespace_get(args, "json", False)):
        emit_json(Resume(status="resumed", command="resume", resumed=True))


def cmd_stop(args, ctx=None):
    """Stop current print."""

    ctx = ctx or RuntimeContext.for_request(args)
    if not args.confirm:
        logger.warning("⚠️  This will STOP the current print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                Stop(
                    status="confirmation_required",
                    command="stop",
                    stopped=False,
                    next_command=["stop", "--confirm", "--json"],
                )
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "stop"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send stop command."
        safe_log_error(message)
        abort(
            message,
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
            extra={"stopped": False},
            command="stop",
        )
    logger.info("⏹️  Print stopped")
    if bool(_namespace_get(args, "json", False)):
        emit_json(Stop(status="stopped", command="stop", stopped=True))
