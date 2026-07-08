import argparse
import logging
import os
import socket
import sys
from urllib.parse import urlparse, urlunparse

import bambu_cli.utils as utils
from bambu_cli.errors import BambuError

# Logging
from bambu_cli.logging_utils import logger

from .constants import (
    DEFAULT_MAX_DOWNLOAD_MB,
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    PRINTER_NETWORK_COMMANDS,
)
from .utils import emit_json, emit_json_error


def setup_logging(verbose=False, json_mode=False):  # pragma: no cover -- cli helper
    logging_module = logging
    sys_module = sys

    try:
        from rich.console import Console
        from rich.logging import RichHandler
        from rich.traceback import install

        install(show_locals=False)
        console = Console(stderr=True)
        handler = RichHandler(console=console, rich_tracebacks=True, markup=True)
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

    logger.propagate = False
    logger.setLevel(level)
    logger.addHandler(handler)
    logging_module.getLogger("paho").setLevel(logging_module.WARNING)


def _argv_json_requested(argv=None):  # pragma: no cover -- cli helper
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False, parents=[get_global_parser()])
    args, _ = parser.parse_known_args(argv)
    return getattr(args, "json", False)


def _guess_command_from_argv(argv=None):  # pragma: no cover -- cli helper
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False, parents=[get_global_parser()])
    parser.add_argument("command", nargs="?")
    args, _ = parser.parse_known_args(argv)
    return args.command or "main"


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse parser that keeps --json calls machine-readable on parse errors."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("conflict_handler", "resolve")
        super().__init__(*args, **kwargs)

    def error(self, message):  # pragma: no cover -- argparse error path (human + JSON)
        if not _argv_json_requested():
            self.print_usage(sys.stderr)
            self.exit(EXIT_COMMAND_ERROR, f"{self.prog}: error: {message}\n")
        emit_json(
            {
                "status": "error",
                "command": _guess_command_from_argv(),
                "failed_step": "parse",
                "exit_code": EXIT_COMMAND_ERROR,
                "error": message,
            }
        )
        self.exit(EXIT_COMMAND_ERROR)


def _expand_path(path):  # pragma: no cover -- cli helper
    """Expand user and environment variables in local filesystem paths."""
    if path is None:
        return None
    return os.path.expandvars(os.path.expanduser(str(path)))


def _display_path(path):  # pragma: no cover -- cli helper
    """Return a user-facing path with the current home directory compacted."""
    if path is None:
        return None
    text = str(path)
    expanded = _expand_path(text)
    if not os.path.isabs(expanded):
        return text
    home = os.path.expanduser("~")
    try:
        norm_expanded = os.path.normcase(os.path.abspath(expanded))
        norm_home = os.path.normcase(os.path.abspath(home))
    except (TypeError, ValueError, OSError):
        return text
    if norm_expanded == norm_home:
        return "~"
    prefix = norm_home + os.sep
    if norm_expanded.startswith(prefix):
        return "~" + os.sep + os.path.relpath(expanded, home)
    return text


def _path_for_message(path):  # pragma: no cover -- cli helper
    """Return a local path suitable for human and agent-facing messages."""
    display = _display_path(path)
    if display is None or os.sep == "/":
        return display
    return display.replace(os.sep, "/")


def _exception_for_message(exc):  # pragma: no cover -- cli helper
    """Return exception text with local filesystem paths compacted for output."""
    message = str(exc)
    for attr in ("filename", "filename2"):
        path = getattr(exc, attr, None)
        if path is not None:
            message = message.replace(str(path), _display_path(path))
    return message


def _looks_like_schemeless_credential_url(value):  # pragma: no cover -- cli helper
    """Detect userinfo-bearing URLs where the user omitted https://."""
    text = str(value or "")
    if "\\" in text or any(char.isspace() for char in text):
        return False
    if "@" not in text or text.startswith(("/", ".", "~", "$")):
        return False
    try:
        parsed = urlparse(f"https://{text}")
        host = parsed.hostname or ""
        return bool(parsed.netloc and (parsed.username is not None or parsed.password is not None) and "." in host)
    except Exception:
        return False


def _redact_url_credentials(value):  # pragma: no cover -- cli helper
    """Return URL text with any userinfo removed before logging or JSON output."""
    text = str(value or "")
    parsed = urlparse(text)
    if "://" not in text and _looks_like_schemeless_credential_url(text):
        redacted = _redact_url_credentials(f"https://{text}")
        prefix = "https://"
        return redacted[len(prefix) :] if isinstance(redacted, str) and redacted.startswith(prefix) else redacted
    if not parsed.scheme or not parsed.netloc or (parsed.username is None and parsed.password is None):
        return value
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _namespace_get(args, name, default=None):  # pragma: no cover -- cli helper
    """Read argparse.Namespace values without treating MagicMock attributes as set."""
    try:
        return vars(args).get(name, default)
    except TypeError:
        return default


def _exit_code_from_system_exit(exc, default=EXIT_COMMAND_ERROR):  # pragma: no cover -- cli helper
    """Normalize SystemExit / BambuError codes for machine-readable summaries."""
    code = getattr(exc, "exit_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if code is None:
        return EXIT_SUCCESS
    return default


def _add_job_arguments(parser):  # pragma: no cover -- cli helper
    parser.add_argument("source", help="URL or local path to .stl/.step/.stp/.obj/.3mf/.gcode/.zip")
    parser.add_argument("--confirm", action="store_true", help="Confirm print start after upload")
    parser.add_argument(
        "--dry-run", action="store_true", help="No-side-effect validation; skip download/slice/upload/print"
    )
    parser.add_argument("--upload-only", action="store_true", help="Upload the printable but do not start the print")
    parser.add_argument("--name", help="Save downloaded URL as filename before slicing/upload")
    parser.add_argument(
        "--output",
        help="Working/output directory for downloads, ZIP extraction, and sliced .3mf files (default: private temp dir)",
    )
    parser.add_argument(
        "--max-download-mb",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_MB,
        help=f"Maximum URL download and ZIP extraction size in MB (default: {DEFAULT_MAX_DOWNLOAD_MB})",
    )
    parser.add_argument("--quality", default="standard", help="draft/standard/high (default: standard)")
    parser.add_argument("--filament", type=str, default="PLA Basic", help="Filament type (e.g. 'PLA Basic', 'PETG')")
    parser.add_argument("--infill", type=int, default=15, help="Infill density %% (default: 15)")
    parser.add_argument("--pattern", default="3dhoneycomb", help="Infill pattern (default: 3dhoneycomb)")
    parser.add_argument("--nozzle-temp", type=int, default=220, help="Nozzle temp °C (default: 220)")
    parser.add_argument("--bed-temp", type=int, default=60, help="Bed temp °C (default: 60)")
    parser.add_argument("--supports", action="store_true", help="Enable supports")
    parser.add_argument("--support-type", choices=["tree", "normal"], help="Support type: tree or normal")
    parser.add_argument("--support-interface-density", type=float, help="Support interface density %%")
    parser.add_argument(
        "--support-interface-pattern",
        choices=["rectilinear", "concentric", "honeycomb"],
        help="Support interface pattern",
    )
    parser.add_argument("--walls", type=int, help="Number of walls/perimeters")
    parser.add_argument(
        "--wall-type", choices=["normal", "classic", "archaic"], help="Wall type: normal (arachne) or classic"
    )
    parser.add_argument("--top-layers", type=int, help="Number of top layers")
    parser.add_argument("--bottom-layers", type=int, help="Number of bottom layers")
    parser.add_argument("--accel-wall", type=int, help="Inner wall acceleration (mm/s²)")
    parser.add_argument("--accel-wall-outer", type=int, help="Outer wall acceleration (mm/s²)")
    parser.add_argument("--accel-infill", type=int, help="Infill acceleration (mm/s²)")
    parser.add_argument("--accel-travel", type=int, help="Travel acceleration (mm/s²)")
    parser.add_argument("--accel-first-layer", type=int, help="First-layer acceleration (mm/s²)")
    parser.add_argument("--copies", type=int, default=1, help="Number of copies to arrange on plate (default: 1)")
    parser.add_argument("--use-ams", action="store_true", help="Enable AMS")
    parser.add_argument(
        "--ams-mapping", type=str, help="AMS slot mapping with zero-or-positive indexes, e.g., '1' or '0,1,2'"
    )
    parser.add_argument("--timelapse", action="store_true", help="Enable timelapse")
    parser.add_argument("--skip-bed-leveling", action="store_true", help="Skip bed leveling")
    parser.add_argument("--skip-flow-cali", action="store_true", help="Skip flow calibration")
    parser.add_argument("--threads", type=int, help="Limit OrcaSlicer CPU threads")


def get_global_parser():  # pragma: no cover -- cli helper
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Enable debug logging"
    )
    global_parser.add_argument("--sim", action="store_true", default=argparse.SUPPRESS, help="Enable simulation mode")
    global_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit JSON for commands that support it; may appear before the subcommand",
    )
    global_parser.add_argument(
        "--network-timeout",
        type=float,
        default=argparse.SUPPRESS,
        help="Timeout in seconds for general network communication",
    )
    global_parser.add_argument(
        "--slicer-timeout", type=float, default=argparse.SUPPRESS, help="Timeout in seconds for the slicing process"
    )
    global_parser.add_argument(
        "--command-timeout", type=float, default=argparse.SUPPRESS, help="Timeout in seconds for printer commands"
    )
    global_parser.add_argument(
        "--upload-timeout", type=float, default=argparse.SUPPRESS, help="Timeout in seconds for file uploads"
    )
    global_parser.add_argument(
        "--allow-private-ips",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow fetching URLs that resolve to private or local network IP addresses",
    )
    return global_parser


def build_parser():  # pragma: no cover -- argparse wiring; help smoke tests cover
    parser = JsonArgumentParser(description="Bambu Lab local printer control", parents=[get_global_parser()])
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    sub = parser.add_subparsers(dest="cmd", parser_class=JsonArgumentParser)

    p_status = sub.add_parser("status", parents=[get_global_parser()], help="Get printer status")
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable status summary with raw printer data under \x27printer\x27",
    )
    p_status.add_argument(
        "--wait", "--monitor", action="store_true", dest="monitor", help="Monitor print status until completion"
    )

    p_light = sub.add_parser("light", parents=[get_global_parser()], help="Control chamber light")
    p_light.add_argument("action", choices=["on", "off"])
    p_light.add_argument("--json", action="store_true", help="Emit machine-readable light summary")

    p_pause = sub.add_parser("pause", parents=[get_global_parser()], help="Pause current print")
    p_pause.add_argument("--json", action="store_true", help="Emit machine-readable pause summary")
    p_resume = sub.add_parser("resume", parents=[get_global_parser()], help="Resume paused print")
    p_resume.add_argument("--json", action="store_true", help="Emit machine-readable resume summary")

    p_stop = sub.add_parser("stop", parents=[get_global_parser()], help="Stop current print")
    p_stop.add_argument("--confirm", action="store_true", help="Confirm stop")

    p_upload = sub.add_parser("upload", parents=[get_global_parser()], help="Upload file to printer")
    p_upload.add_argument("file", help="Path to .3mf or .gcode file")
    p_upload.add_argument("--dry-run", action="store_true", help="Validate connectivity without uploading")

    sub.add_parser("files", parents=[get_global_parser()], help="List files on printer")

    p_print = sub.add_parser("print", parents=[get_global_parser()], help="Start printing a file on printer")
    p_print.add_argument("file", help="Filename on printer (e.g. model.3mf)")
    p_print.add_argument("--confirm", action="store_true", help="Confirm print start")
    p_print.add_argument("--dry-run", action="store_true", help="Validate file existence without printing")
    p_print.add_argument("--use-ams", action="store_true", help="Enable AMS")
    p_print.add_argument(
        "--ams-mapping", type=str, help="AMS slot mapping with zero-or-positive indexes, e.g., '1' or '0,1,2'"
    )
    p_print.add_argument("--timelapse", action="store_true", help="Enable timelapse")
    p_print.add_argument("--skip-bed-leveling", action="store_true", help="Skip bed leveling")
    p_print.add_argument("--skip-flow-cali", action="store_true", help="Skip flow calibration")

    p_job = sub.add_parser(
        "job",
        parents=[get_global_parser()],
        help="One-shot URL/local file workflow: download, slice, upload, optionally print",
    )
    _add_job_arguments(p_job)

    p_send = sub.add_parser(
        "send", parents=[get_global_parser()], help="Alias for job, with agent-friendly URL/local file workflow"
    )
    _add_job_arguments(p_send)

    p_slice = sub.add_parser("slice", parents=[get_global_parser()], help="Slice a model file into .3mf")
    p_slice.add_argument("file", help="Path to .stl, .step, .stp, or .obj file")
    p_slice.add_argument("--quality", default="standard", help="draft/standard/high (default: standard)")
    p_slice.add_argument("--filament", type=str, default="PLA Basic", help="Filament type (e.g. 'PLA Basic', 'PETG')")
    p_slice.add_argument("--infill", type=int, default=15, help="Infill density %% (default: 15)")
    p_slice.add_argument("--pattern", default="3dhoneycomb", help="Infill pattern (default: 3dhoneycomb)")
    p_slice.add_argument("--nozzle-temp", type=int, default=220, help="Nozzle temp °C (default: 220)")
    p_slice.add_argument("--bed-temp", type=int, default=60, help="Bed temp °C (default: 60)")
    p_slice.add_argument("--supports", action="store_true", help="Enable supports")
    p_slice.add_argument("--support-type", choices=["tree", "normal"], help="Support type: tree or normal")
    p_slice.add_argument("--support-interface-density", type=float, help="Support interface density %%")
    p_slice.add_argument(
        "--support-interface-pattern",
        choices=["rectilinear", "concentric", "honeycomb"],
        help="Support interface pattern",
    )
    p_slice.add_argument("--walls", type=int, help="Number of walls/perimeters")
    p_slice.add_argument(
        "--wall-type", choices=["normal", "classic", "archaic"], help="Wall type: normal (arachne) or classic"
    )
    p_slice.add_argument("--top-layers", type=int, help="Number of top layers")
    p_slice.add_argument("--bottom-layers", type=int, help="Number of bottom layers")
    p_slice.add_argument("--accel-wall", type=int, help="Inner wall acceleration (mm/s²)")
    p_slice.add_argument("--accel-wall-outer", type=int, help="Outer wall acceleration (mm/s²)")
    p_slice.add_argument("--accel-infill", type=int, help="Infill acceleration (mm/s²)")
    p_slice.add_argument("--accel-travel", type=int, help="Travel acceleration (mm/s²)")
    p_slice.add_argument("--accel-first-layer", type=int, help="First layer acceleration (mm/s²)")
    p_slice.add_argument("--copies", type=int, default=1, help="Number of copies to arrange on plate (default: 1)")
    p_slice.add_argument("--output", help="Output directory (default: same as input)")
    p_slice.add_argument("--threads", type=int, help="Limit OrcaSlicer CPU threads")

    p_gc = sub.add_parser("gcode", parents=[get_global_parser()], help="Send raw G-code to printer")
    p_gc.add_argument("code", help="G-code command (e.g. 'M104 S220')")

    p_dl = sub.add_parser("download", parents=[get_global_parser()], help="Download model/print file from URL")
    p_dl.add_argument("url", help="Printables page, simple HTML page, direct model/print URL, or ZIP URL")
    p_dl.add_argument("--name", help="Save as filename (default: from URL)")
    p_dl.add_argument("--output", help="Output directory (default: system temp dir)")
    p_dl.add_argument(
        "--max-download-mb",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_MB,
        help=f"Maximum download and ZIP extraction size in MB (default: {DEFAULT_MAX_DOWNLOAD_MB})",
    )

    p_del = sub.add_parser("delete", parents=[get_global_parser()], help="Delete a file from printer")
    p_del.add_argument("file", help="Filename on printer to delete")
    p_del.add_argument("--confirm", action="store_true", help="Confirm deletion")

    p_snap = sub.add_parser("snapshot", parents=[get_global_parser()], help="Capture camera snapshot")
    p_snap.add_argument("--output", help="Output file path (default: printer_snapshot.jpg)")

    p_doc = sub.add_parser(
        "doctor", parents=[get_global_parser()], help="Run health check and discover printer capabilities"
    )
    p_doc.add_argument("--output", help="Path to write printer_capabilities.json (default: system temp dir)")

    p_preflight = sub.add_parser(
        "preflight",
        parents=[get_global_parser()],
        help="Check local install/config readiness without contacting printer",
    )
    p_preflight.add_argument("--strict", action="store_true", help="Treat warnings as failures")

    p_config = sub.add_parser(
        "config", parents=[get_global_parser()], help="Show the effective config (redacted) or validate it locally"
    )
    p_config.add_argument(
        "action",
        choices=["show", "validate"],
        help="show: print config path and redacted contents; validate: run config checks",
    )
    p_config.add_argument("--strict", action="store_true", help="validate: treat warnings as failures")

    p_setup = sub.add_parser(
        "setup",
        parents=[get_global_parser()],
        help="Guided or non-interactive setup to discover printer and create config",
    )
    p_setup.add_argument("--printer-ip", help="Printer IP address or hostname for non-interactive setup")
    p_setup.add_argument("--serial", help="Printer serial number for non-interactive setup")
    p_setup.add_argument(
        "--access-code", help="Printer access code value (prefer --access-code-env to avoid shell history)"
    )
    p_setup.add_argument("--access-code-env", help="Environment variable containing the printer access code")
    p_setup.add_argument(
        "--access-code-file",
        help="Existing access-code file, or destination when paired with --access-code/--access-code-env",
    )
    p_setup.add_argument("--model", help="Printer model: P1P, P1S, X1C, X1, X1E, A1, A1M")
    p_setup.add_argument("--nozzle", help="Nozzle size: 0.2, 0.4, 0.6, 0.8")
    p_setup.add_argument("--orca-slicer", help="Path to OrcaSlicer executable")
    p_setup.add_argument("--profiles-dir", help="Path to OrcaSlicer BBL profiles directory")
    p_setup.add_argument("--cert-fingerprint", help="SHA-256 fingerprint to pin the printer TLS certificate")
    p_setup.add_argument("--insecure-tls", action="store_true", help="Disable TLS verification entirely (last resort)")
    p_setup.add_argument("--scan-timeout", type=float, help="Custom duration for local printer network scanning")
    p_setup.add_argument(
        "--migrate-access-code",
        action="store_true",
        help="Move inline access_code into access_code_file and update config.json",
    )

    return parser


def _json_mode_requested(args):  # pragma: no cover -- cli helper
    return bool(getattr(args, "json", False))


def _requires_printer_dns_check(args):  # pragma: no cover -- cli helper
    if bool(getattr(args, "sim", False)):
        return False
    if args.cmd not in PRINTER_NETWORK_COMMANDS:
        return False
    return not (args.cmd in ("job", "send") and bool(getattr(args, "dry_run", False)))


def _json_setup_should_be_noninteractive(args):  # pragma: no cover -- cli helper
    return (
        args.cmd == "setup"
        and bool(getattr(args, "json", False))
        and not _namespace_get(args, "migrate_access_code", False)
        and not _setup_args_provided(args)
        and not sys.stdin.isatty()
    )


def _setup_args_provided(args):  # pragma: no cover -- cli helper
    return any(
        _namespace_get(args, attr) is not None
        for attr in ("printer_ip", "serial", "access_code", "access_code_env", "access_code_file", "model", "nozzle")
    )


def _resolve_command(name):  # pragma: no cover -- cli helper
    """Look up the cmd_* handler for a command through bambu_cli.bambu so
    tests that patch bambu.cmd_* (or bambu.cmd_job) still take effect."""
    func_name = "cmd_job" if name in ("job", "send") else f"cmd_{name}"
    from bambu_cli import bambu

    return getattr(bambu, func_name, None)


def main():  # pragma: no cover -- process entry; handlers unit-tested
    from bambu_cli import bambu

    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "version", False):
        if bool(getattr(args, "json", False)):
            emit_json(
                {
                    "status": "ok",
                    "command": "version",
                    "version": bambu.VERSION,
                }
            )
        else:
            print(f"bambu-cli {bambu.VERSION}")
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

    bambu.load_config(exit_on_fail=False)

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
        if msg and not msg.startswith("Command failed (exit "):
            logger.error(msg)
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
        sys.exit(exc.exit_code)

    if _json_setup_should_be_noninteractive(args):
        try:
            bambu._cmd_setup_noninteractive(args)
        except BambuError as exc:
            _handle_bambu_error(exc, "setup")
        return

    # Global settings validation
    if _requires_printer_dns_check(args):
        printer_ip = _context.current_settings().printer_ip
        if printer_ip == "0.0.0.0":
            message = "Printer IP is not configured. Please run setup first."
            logger.error(message)
            emit_json_error(args, args.cmd or "main", EXIT_CONFIG_ERROR, message, failed_step="config")
            sys.exit(EXIT_CONFIG_ERROR)
        try:
            socket.getaddrinfo(printer_ip, None)
        except socket.gaierror:
            message = f"Invalid printer_ip or hostname in config: {printer_ip}"
            logger.error(message)
            emit_json_error(args, args.cmd or "main", EXIT_CONFIG_ERROR, message, failed_step="config")
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
            print("\nOperation cancelled by user.")
            sys.exit(EXIT_COMMAND_ERROR)
        except BambuError as exc:
            _handle_bambu_error(exc, args.cmd)
        except Exception as exc:
            logger.error(f"Uncaught exception: {exc}", exc_info=True)
            if _json_mode_requested(args) and not utils._JSON_EMITTED:
                emit_json_error(
                    args,
                    args.cmd,
                    EXIT_COMMAND_ERROR,
                    f"Unexpected error: {str(exc)}",
                )
            sys.exit(EXIT_COMMAND_ERROR)
    else:
        parser.print_help(sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)


if __name__ == "__main__":
    main()
