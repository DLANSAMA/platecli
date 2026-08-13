"""Start a print of a file already on the printer."""

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.contracts import Print
from bambu_cli.download.naming import (
    _is_print_ready_name,
    _name_for_message,
    _print_ready_error_message,
    _safe_remote_name,
)
from bambu_cli.errors import abort
from bambu_cli.job import _parse_print_options, _print_next_command, generate_print_payload
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json


def cmd_print(args, ctx=None):
    """Start printing a file already on the printer."""

    ctx = ctx or RuntimeContext.for_request(args)
    dry_run = getattr(args, "dry_run", False)
    basename = str(args.file or "")

    if _safe_remote_name(basename) is None:
        message = f"Refusing to print file with unsafe name: {_name_for_message(basename)!r}"
        abort(
            message,
            exit_code=EXIT_FILE_ERROR,
            failed_step="validate",
            extra={"file": basename},
        )
    if not _is_print_ready_name(basename):
        message = _print_ready_error_message(basename, "print")
        abort(
            message,
            exit_code=EXIT_FILE_ERROR,
            failed_step="validate",
            extra={"file": basename},
        )

    ams_mapping, print_option_error = _parse_print_options(args)
    if print_option_error:
        abort(
            print_option_error,
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
            extra={"file": basename},
        )

    if not args.confirm and not dry_run:
        logger.warning("⚠️  This will START a print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                Print(
                    status="confirmation_required",
                    command="print",
                    file=basename,
                    printed=False,
                    next_command=_print_next_command(args, basename),
                )
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)

    payload = generate_print_payload(
        basename,
        use_ams=getattr(args, "use_ams", False),
        ams_mapping=ams_mapping,
        timelapse=getattr(args, "timelapse", False),
        bed_leveling=not getattr(args, "skip_bed_leveling", False),
        flow_cali=not getattr(args, "skip_flow_cali", False),
    )
    from bambu_cli.printer import get_printer
    from bambu_cli.protocols.mqtt import execute_print_command

    printer = get_printer()
    execute_print_command(printer, payload, basename, dry_run=dry_run)
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            Print(
                status="dry_run_ok" if dry_run else "print_started",
                command="print",
                file=basename,
                printed=not dry_run,
                dry_run=bool(dry_run),
            )
        )
    return basename
