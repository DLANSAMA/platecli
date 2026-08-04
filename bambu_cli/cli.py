import logging
import socket
import sys

import bambu_cli.utils as utils
from bambu_cli.errors import BambuError

# Logging
from bambu_cli.logging_utils import logger, safe_log_error

# Path / JSON / argparse helpers extracted from this module (roadmap B.4).
# Aliased to their historical underscore names for internal use here.
from .argutils import exit_code_from_system_exit as _exit_code_from_system_exit
from .argutils import namespace_get as _namespace_get
from .argutils import setup_args_provided as _setup_args_provided
from .cliparse import (  # noqa: F401
    JsonArgumentParser,
    _add_job_arguments,
    _add_slice_override_args,
    _argv_json_requested,
    _guess_command_from_argv,
    _SilentArgumentParser,
    _SilentParseError,
    build_parser,
    get_global_parser,
)
from .constants import (
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    PRINTER_NETWORK_COMMANDS,
)

# The argparse tree lives in bambu_cli.cliparse so domain code can build a
# namespace without importing this entrypoint (audit item A1). Re-exported here
# because build_parser() is the documented source of truth for the command set,
# and the help/workflow smokes plus several tests import it from this module.
from .contracts import Version
from .jsonio import json_mode_requested as _json_mode_requested
from .utils import emit_json, emit_json_error


def setup_logging(verbose=False, json_mode=False):
    logging_module = logging
    sys_module = sys

    try:
        from rich.console import Console
        from rich.logging import RichHandler
        from rich.traceback import install

        install(show_locals=False)
        console = Console(stderr=True)
        # file.py:line origin column is developer detail — only show it with --verbose.
        # markup=False is mandatory: log lines interpolate user-controlled filenames, and rich
        # would parse 'part[v2].stl' as a style tag (silently dropping it) or raise MarkupError
        # on 'a[/b]c.stl'. No logger call in bambu_cli/ uses markup tags (verified by grep); the
        # only '[bold blue]' strings are rich Progress TextColumn templates, which are unaffected.
        handler = RichHandler(console=console, rich_tracebacks=True, markup=False, show_path=verbose)
    except ImportError:
        stream = sys_module.stderr
        handler = logging_module.StreamHandler(stream)
        formatter = logging_module.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)

    level = logging_module.DEBUG if verbose else logging_module.INFO
    root = logging_module.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in logger.handlers[:]:
        logger.removeHandler(h)

    logger.propagate = False  # type: ignore[attr-defined]  # LoggerProxy accepts instance attrs
    logger.setLevel(level)
    logger.addHandler(handler)
    logging_module.getLogger("paho").setLevel(logging_module.WARNING)


def _bare_plate_should_launch_wizard(args):
    """Decide whether bare `plate` (no subcommand) launches the guided wizard.

    Plan §11 Q1 (binding): launch only when stdin AND stdout are both TTYs and
    --json was not passed. Requiring both streams keeps the script pattern
    "TTY stdin, redirected stdout" on today's help path. --json forces help too,
    since interactive mode has no machine contract. Any machine-use context
    (CI, pipes, subprocess) is therefore unaffected — a zero-breakage default flip.
    """
    if getattr(args, "cmd", None):
        return False
    if getattr(args, "version", False):
        return False
    if _json_mode_requested(args):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _requires_printer_dns_check(args):
    if bool(getattr(args, "sim", False)):
        return False
    if args.cmd not in PRINTER_NETWORK_COMMANDS:
        return False
    return not (args.cmd in ("job", "send") and bool(getattr(args, "dry_run", False)))


def _json_setup_should_be_noninteractive(args):
    return (
        args.cmd == "setup"
        and bool(getattr(args, "json", False))
        and not _namespace_get(args, "migrate_access_code", False)
        and not _setup_args_provided(args)
        and not sys.stdin.isatty()
    )


def _resolve_command(name):
    """Look up the cmd_* handler for a command on bambu_cli.commands.

    Tests inject or patch ``bambu_cli.commands.cmd_*`` rather than the
    former ``bambu_cli.bambu`` facade.
    """
    func_name = "cmd_job" if name in ("job", "send") else f"cmd_{name}"
    from bambu_cli import commands as commands_mod

    return getattr(commands_mod, func_name, None)


def _safe_log_error(message, **kwargs):
    """Backwards-compatible alias for the shared helper in ``logging_utils``.

    The implementation lives in ``bambu_cli.logging_utils.safe_log_error`` so the whole
    package shares one copy; this name is kept because existing tests patch/call it here.
    """
    safe_log_error(message, **kwargs)


def main():
    from bambu_cli.config import load_config
    from bambu_cli.constants import VERSION
    from bambu_cli.setup_cmd import _cmd_setup_noninteractive

    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "version", False):
        if bool(getattr(args, "json", False)):
            emit_json(Version(status="ok", command="version", version=VERSION))
        else:
            print(f"plate {VERSION}")
        return
    if not args.cmd and bool(getattr(args, "json", False)):
        emit_json(
            {
                "status": "error",
                "command": "main",
                "failed_step": "parse",
                "exit_code": EXIT_COMMAND_ERROR,
                "error": "Missing subcommand. Put --json with a command that supports it.",
            }
        )
        sys.exit(EXIT_COMMAND_ERROR)

    verbose_val = getattr(args, "verbose", False)
    json_mode_val = _json_mode_requested(args)
    if json_mode_val:
        setup_logging(verbose_val, json_mode=json_mode_val)
    else:
        setup_logging(verbose_val)
    simulation = bool(getattr(args, "sim", False))
    if simulation:
        logger.info("🤖 Simulation mode enabled.")

    load_config(exit_on_fail=False)

    # load_config installs a RuntimeContext from the parsed config; layer the
    # request-scoped flags (simulation / json / SSRF override) onto it.
    from dataclasses import replace

    from bambu_cli import context as _context

    _ctx = _context.get_current()
    _ctx.simulation = simulation
    _ctx.json_mode = _json_mode_requested(args)
    # CLI-only safety override: never read from config.json (avoid a sticky SSRF hole).
    if _namespace_get(args, "allow_private_ips", False):
        _ctx.settings = replace(_ctx.settings, allow_private_ips=True)

    def _handle_bambu_error(exc, command_name):
        # Expected domain failures: log the message only (no traceback noise for agents).
        msg = str(exc)
        # Emit the machine-readable envelope FIRST: stdout must stay parseable even if the
        # human-readable log line below fails to render.
        if _json_mode_requested(args) and not utils._JSON_EMITTED:
            extra = {}
            if exc.detail:
                extra["detail"] = exc.detail
            if exc.next_command:
                extra["next_command"] = exc.next_command
            emit_json_error(
                args,
                command_name,
                exc.exit_code,
                msg,
                failed_step=exc.failed_step,
                **extra,
            )
        if msg and not msg.startswith("Command failed (exit "):
            _safe_log_error(msg)
        sys.exit(exc.exit_code)

    def _handle_interrupt(interrupt_args, command_name):
        # An agent may SIGINT a long `plate … --json` run on timeout. Keep stdout
        # machine-parseable: emit the standard error envelope (like every other
        # failure branch) and send the human line to stderr, not stdout.
        message = "Operation cancelled by user."
        if _json_mode_requested(interrupt_args) and not utils._JSON_EMITTED:
            emit_json_error(
                interrupt_args,
                command_name,
                EXIT_COMMAND_ERROR,
                message,
                failed_step="interrupted",
            )
        print(f"\n{message}", file=sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)

    if _json_setup_should_be_noninteractive(args):
        try:
            _cmd_setup_noninteractive(args)
        except BambuError as exc:
            _handle_bambu_error(exc, "setup")
        return

    # Global settings validation
    if _requires_printer_dns_check(args):
        printer_ip = _context.current_settings().printer_ip
        if printer_ip == "0.0.0.0":
            message = "Printer IP is not configured. Please run `plate setup` first."
            emit_json_error(args, args.cmd or "main", EXIT_CONFIG_ERROR, message, failed_step="config")
            logger.error(message)
            sys.exit(EXIT_CONFIG_ERROR)
        try:
            socket.getaddrinfo(printer_ip, None)
        except socket.gaierror:
            message = f"Invalid printer_ip or hostname in config: {printer_ip}"
            emit_json_error(args, args.cmd or "main", EXIT_CONFIG_ERROR, message, failed_step="config")
            logger.error(message)
            sys.exit(EXIT_CONFIG_ERROR)

    _handler = _resolve_command(args.cmd)
    if _handler is not None:
        try:
            _handler(args)
        except SystemExit as exc:
            exit_code = _exit_code_from_system_exit(exc)
            if exit_code != EXIT_SUCCESS and _json_mode_requested(args) and not utils._JSON_EMITTED:
                emit_json_error(
                    args,
                    args.cmd,
                    exit_code,
                    f"{args.cmd} failed; see stderr for details",
                )
            raise
        except (KeyboardInterrupt, EOFError):
            _handle_interrupt(args, args.cmd)
        except BambuError as exc:
            _handle_bambu_error(exc, args.cmd)
        except Exception as exc:
            # Envelope first — see _safe_log_error: a logging failure must not eat stdout.
            if _json_mode_requested(args) and not utils._JSON_EMITTED:
                emit_json_error(
                    args,
                    args.cmd,
                    EXIT_COMMAND_ERROR,
                    f"Unexpected error: {str(exc)}",
                )
            _safe_log_error(f"Uncaught exception: {exc}", exc_info=True)
            sys.exit(EXIT_COMMAND_ERROR)
    elif _bare_plate_should_launch_wizard(args):
        # Bare `plate` on an interactive terminal (both stdin and stdout are TTYs)
        # and without --json launches the guided wizard — the highest-leverage
        # ease-of-use win for someone who just installed `plate` and typed it to
        # see what happens (plan §11 Q1). Any machine-use flag (--json) or a
        # non-TTY stream (CI, pipes, subprocess, `plate | less`) keeps today's
        # exact behavior below: help to stderr, EXIT_COMMAND_ERROR.
        _go = _resolve_command("go")
        if _go is None:  # pragma: no cover -- go is always registered
            parser.print_help(sys.stderr)
            sys.exit(EXIT_COMMAND_ERROR)
        args.cmd = "go"
        try:
            _go(args)
        except (KeyboardInterrupt, EOFError):
            _handle_interrupt(args, "go")
        except BambuError as exc:
            _handle_bambu_error(exc, "go")
    else:
        parser.print_help(sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)


if __name__ == "__main__":
    main()
