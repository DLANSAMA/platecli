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


def _looks_like_local_path(value: str) -> bool:
    """True when *value* carries a path separator, so it cannot be a printer-side name."""
    return "/" in value or "\\" in value


def _local_path_error(path: str) -> tuple[str, str]:
    """Message + next command for a local path handed to `print`.

    A model file (STL/STEP/OBJ) needs slicing, so `job` is the one-step fix.
    An already-sliced 3MF/G-code only needs `upload` before `print <name>`.
    """
    shown = _name_for_message(path)
    if _is_print_ready_name(path):
        next_command = f"plate upload {shown}"
        fix = f"upload it first with `{next_command}`, then `plate print <name> --confirm`"
    else:
        next_command = f"plate job {shown} --confirm"
        fix = f"slice, upload and print it in one step with `{next_command}`"
    message = (
        f"`print` starts a file that is already on the printer, by name "
        f"(for example `plate print model.gcode.3mf --confirm`). "
        f"{shown!r} looks like a local path: {fix}."
    )
    return message, next_command


def cmd_print(args, ctx=None):
    """Start printing a file already on the printer."""

    ctx = ctx or RuntimeContext.for_request(args)
    dry_run = getattr(args, "dry_run", False)
    basename = str(args.file or "")

    if _safe_remote_name(basename) is None:
        if _looks_like_local_path(basename):
            # A path with separators is never a printer-side name. Blaming the
            # user for an "unsafe name" hides the real mistake: `print` takes
            # the name of a file already on the printer, and the local file
            # needs `job` (model) or `upload` (sliced) first.
            message, next_command = _local_path_error(basename)
            abort(
                message,
                exit_code=EXIT_FILE_ERROR,
                failed_step="validate",
                next_command=next_command,
                extra={"file": basename},
            )
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
