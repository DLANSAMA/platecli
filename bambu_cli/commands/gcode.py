"""Send raw G-code via MQTT."""

import json
import re

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR, MAX_BED_TEMP_C, MAX_NOZZLE_TEMP_C
from bambu_cli.context import RuntimeContext
from bambu_cli.contracts import Gcode
from bambu_cli.download.naming import _has_command_injection_chars
from bambu_cli.errors import CommandUnconfirmed, abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json, get_sequence_id

# One G-code word: a letter and the numeric text right after it.
_WORD = re.compile(r"([A-Za-z])[ \t]*([-+]?[0-9.]*)", re.ASCII)
_PLAIN_TEMPERATURE = re.compile(r"\+?[0-9]+(?:\.[0-9]+)?", re.ASCII)
# Heater commands and the limit their S (and Marlin's R) temperature must stay under.
_HEATERS = {
    104: ("nozzle", MAX_NOZZLE_TEMP_C),
    109: ("nozzle", MAX_NOZZLE_TEMP_C),
    140: ("bed", MAX_BED_TEMP_C),
    190: ("bed", MAX_BED_TEMP_C),
}


def _heater_problem(gcode):
    """Why ``gcode`` would set a heater past the slicer's limits, or None.

    The same 350 °C nozzle / 150 °C bed ceilings that slice overrides obey: a
    typo such as ``M104 S2200`` must not reach the heater just because it came
    through the raw-G-code path. Deliberately conservative: comments are
    scanned too, and a temperature that is not a plain decimal (``S2e3``,
    ``S0x3E8``, ``S250T0``) is refused because firmware parsers read those
    forms differently.
    """
    if not gcode.isascii():
        return "G-code must be plain ASCII."
    heater = None
    for match in _WORD.finditer(gcode):
        letter, value = match.group(1).upper(), match.group(2)
        # T is a parameter here, not a new command: "M104 T0 S3000" heats tool 0.
        if letter in ("G", "M"):
            heater = None
            if letter == "M" and _PLAIN_TEMPERATURE.fullmatch(value):
                heater = _HEATERS.get(int(float(value)))
            continue
        if heater is None or letter not in ("S", "R"):
            continue
        label, limit = heater
        end = match.end()
        if not _PLAIN_TEMPERATURE.fullmatch(value) or (end < len(gcode) and gcode[end].isalpha()):
            token = gcode[match.start() :].split(None, 1)[0].split(";", 1)[0]
            return f"Unreadable {label} temperature '{token}'; write it as a plain number such as {letter}220."
        if float(value) > limit:
            return f"{label.capitalize()} temperature {value} °C is above the {limit} °C limit."
    return None


def cmd_gcode(args, ctx=None):
    """Send raw G-code to the printer via MQTT."""

    ctx = ctx or RuntimeContext.for_request(args)
    gcode = str(args.code if args.code is not None else "")

    # Reject empty/whitespace-only and CR/LF/NUL (shared helper with remote-name
    # sanitization — same command-injection risk on MQTT as on FTP lines).
    if not gcode.strip() or _has_command_injection_chars(gcode):
        abort(
            "Invalid G-code: must be non-empty and must not contain control characters (CR/LF/NUL).",
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
            extra={"gcode": gcode, "sent": False},
        )
    problem = _heater_problem(gcode)
    if problem:
        abort(
            f"Invalid G-code: {problem}",
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
            extra={"gcode": gcode, "sent": False},
        )

    if not args.confirm:
        logger.warning("⚠️  This will SEND raw G-code to the printer. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                Gcode(
                    status="confirmation_required",
                    command="gcode",
                    gcode=gcode,
                    sent=False,
                    next_command=["gcode", gcode, "--confirm", "--json"],
                )
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)

    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "gcode_line", "param": gcode}})
    printer = ctx.printer()
    try:
        sent = printer.send_command(payload)
    except CommandUnconfirmed as exc:
        # Published but never acknowledged: it may have run. Say so, with the
        # G-code, rather than "not sent" -- a caller that retried would run it twice.
        exc.extra = {"gcode": gcode, **exc.extra}
        exc.next_command = exc.next_command or ["status", "--json"]
        raise
    if not sent:
        abort(
            "Failed to send G-code command.",
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
            extra={"gcode": gcode, "sent": False},
        )
    logger.info(f"📡 Sent: {gcode}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(Gcode(status="sent", command="gcode", gcode=gcode, sent=True))
