"""Start a print of a file already on the printer."""

from bambu_cli import utils
from bambu_cli.cli import _exit_code_from_system_exit, _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.download.naming import (
    _is_print_ready_name,
    _name_for_message,
    _print_ready_error_message,
    _safe_remote_name,
)
from bambu_cli.errors import BambuError, abort
from bambu_cli.job import _last_error_for, _parse_print_options, _print_next_command, generate_print_payload
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json, emit_json_error


def cmd_print(args, ctx=None):
    """Start printing a file already on the printer."""

    ctx = ctx or RuntimeContext.for_request(args)
    dry_run = getattr(args, "dry_run", False)
    basename = str(args.file or "")

    if _safe_remote_name(basename) is None:
        message = f"Refusing to print file with unsafe name: {_name_for_message(basename)!r}"
        logger.error(message)
        emit_json_error(args, "print", EXIT_FILE_ERROR, message, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not _is_print_ready_name(basename):
        message = _print_ready_error_message(basename, "print")
        logger.error(message)
        emit_json_error(args, "print", EXIT_FILE_ERROR, message, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_FILE_ERROR)

    ams_mapping, print_option_error = _parse_print_options(args)
    if print_option_error:
        logger.error(print_option_error)
        emit_json_error(args, "print", EXIT_COMMAND_ERROR, print_option_error, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_COMMAND_ERROR)

    if not args.confirm and not dry_run:
        logger.warning("⚠️  This will START a print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "print",
                    "file": basename,
                    "printed": False,
                    "next_command": _print_next_command(args, basename),
                }
            )
        return

    payload = generate_print_payload(
        basename,
        use_ams=getattr(args, "use_ams", False),
        ams_mapping=ams_mapping,
        timelapse=getattr(args, "timelapse", False),
        bed_leveling=not getattr(args, "skip_bed_leveling", False),
        flow_cali=not getattr(args, "skip_flow_cali", False),
    )
    try:
        from bambu_cli.printer import get_printer

        printer = get_printer()
        utils._LAST_ERROR_PAYLOAD = None
        from bambu_cli.protocols.mqtt import execute_print_command

        execute_print_command(printer, payload, basename, dry_run=dry_run)
    except BambuError as exc:
        exit_code = getattr(exc, "exit_code", None) or _exit_code_from_system_exit(exc)
        detail = _last_error_for("print")
        emit_json_error(
            args,
            "print",
            exit_code,
            detail.get("error") if detail else "print failed; see stderr for details",
            failed_step="dry_run" if dry_run else "print",
            file=basename,
            printed=False,
            dry_run=bool(dry_run),
            **({"print_error": detail} if detail else {}),
        )
        raise
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "dry_run_ok" if dry_run else "print_started",
                "command": "print",
                "file": basename,
                "printed": not dry_run,
                "dry_run": bool(dry_run),
            }
        )
    return basename
