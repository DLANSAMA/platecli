"""The argparse tree: every subcommand, flag, and default `plate` accepts.

Split out of ``bambu_cli.cli`` so that code which needs to *build a namespace*
does not have to import the entrypoint. The interactive wizard and TUI both
construct a `job` namespace from real parser defaults rather than hand-rolling
one; importing ``cli`` to do that dragged the whole entry module — and its
process-terminating ``main()`` — into the domain layer (audit item A1).

``build_parser()`` remains the single source of truth for the command set:
``scripts/cli_help_smoke.py`` and ``tests/ci_workflow_smoke.py`` both walk this
tree instead of hand-maintaining a parallel list. Add a subcommand here and
those follow automatically.

Nothing in this module terminates the process: the parsers raise or call
argparse's own ``self.exit`` so ``bambu_cli.cli`` stays the only module holding
``sys.exit`` (a blocking CI grep).
"""

import argparse
import sys

from bambu_cli.constants import DEFAULT_MAX_DOWNLOAD_MB, EXIT_COMMAND_ERROR
from bambu_cli.utils import emit_json


class _SilentArgumentParser(argparse.ArgumentParser):
    """A parser whose error()/exit() never terminate the process.

    Used by the JSON-mode / command-guessing helpers below, which parse argv a
    second time purely to introspect it. The stock argparse error() calls
    exit(2) on a type-conversion failure (e.g. `--network-timeout abc`), which
    would bypass JsonArgumentParser.error before it can emit the JSON envelope
    and would classify the failure as exit 2 (EXIT_NETWORK_ERROR). Swallowing
    those aborts lets the real JsonArgumentParser own the error path.
    """

    def error(self, message):  # pragma: no cover -- exercised via helpers below
        raise _SilentParseError(message)

    def exit(self, status=0, message=None):  # pragma: no cover -- see above
        raise _SilentParseError(message or "")


class _SilentParseError(Exception):
    pass


def _argv_json_requested(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    # Textual scan first: even a parse that later fails on a bad typed flag must
    # still route through the JSON envelope when --json is present.
    if "--json" in argv:
        return True
    parser = _SilentArgumentParser(add_help=False, parents=[get_global_parser()])
    try:
        args, _ = parser.parse_known_args(argv)
    except _SilentParseError:
        return False
    return getattr(args, "json", False)


def _guess_command_from_argv(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = _SilentArgumentParser(add_help=False, parents=[get_global_parser()])
    parser.add_argument("command", nargs="?")
    try:
        args, _ = parser.parse_known_args(argv)
    except _SilentParseError:
        return "main"
    return args.command or "main"


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse parser that keeps --json calls machine-readable on parse errors."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("conflict_handler", "resolve")
        super().__init__(*args, **kwargs)

    def error(self, message):
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


def _add_slice_override_args(parser):
    """Generic OrcaSlicer setting overrides shared by `slice` and `job`/`send`.

    These reach the full profile surface (see `slice --list-settings`) without a
    per-setting flag; the named tuning flags above are ergonomic shortcuts.
    """
    parser.add_argument(
        "--set",
        dest="set_process",
        action="append",
        metavar="KEY=VALUE",
        help="Override any OrcaSlicer process setting (repeatable); see 'slice --list-settings'.",
    )
    parser.add_argument(
        "--set-filament",
        dest="set_filament",
        action="append",
        metavar="KEY=VALUE",
        help="Override any OrcaSlicer filament setting (repeatable).",
    )
    parser.add_argument(
        "--settings-json",
        dest="settings_json",
        metavar="JSON",
        help='Bulk overrides as JSON: {"process": {...}, "filament": {...}} (agent-friendly).',
    )
    parser.add_argument("--layer-height", dest="layer_height", type=float, metavar="MM", help="Layer height in mm.")
    parser.add_argument(
        "--first-layer-height", dest="first_layer_height", type=float, metavar="MM", help="First-layer height in mm."
    )
    parser.add_argument(
        "--brim", dest="brim", type=float, metavar="MM", help="Brim width in mm (0 disables; >0 adds an outer brim)."
    )
    parser.add_argument(
        "--speed",
        dest="speed",
        type=float,
        metavar="MM/S",
        help="Main print speed (mm/s): sets outer/inner wall and sparse-infill speed.",
    )
    parser.add_argument(
        "--seam-position",
        dest="seam_position",
        choices=["nearest", "aligned", "back", "random"],
        help="Where to place layer seams.",
    )
    parser.add_argument(
        "--ironing",
        dest="ironing",
        choices=["none", "top", "topmost", "solid"],
        help="Ironing pass for smoother top surfaces (default none).",
    )
    parser.add_argument(
        "--support-threshold",
        dest="support_threshold",
        type=float,
        metavar="DEG",
        help="Support overhang threshold angle in degrees.",
    )
    parser.add_argument(
        "--fan-speed", dest="fan_speed", type=float, metavar="PCT", help="Maximum part-cooling fan speed (0-100%%)."
    )
    parser.add_argument(
        "--flow-ratio", dest="flow_ratio", type=float, metavar="RATIO", help="Filament flow ratio (e.g. 0.98)."
    )


def _add_job_arguments(parser):
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
    _add_slice_override_args(parser)


def get_global_parser():
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


def build_parser():
    parser = JsonArgumentParser(
        description="Bambu Lab local printer control",
        epilog="Tip: 'plate go' walks you through printing from a URL.",
        parents=[get_global_parser()],
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    sub = parser.add_subparsers(dest="cmd", parser_class=JsonArgumentParser)

    p_status = sub.add_parser("status", parents=[get_global_parser()], help="Get printer status")
    p_status.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit machine-readable status summary with raw printer data under \x27printer\x27",
    )
    p_status.add_argument(
        "--wait", "--monitor", action="store_true", dest="monitor", help="Monitor print status until completion"
    )

    p_light = sub.add_parser("light", parents=[get_global_parser()], help="Control chamber light")
    p_light.add_argument("action", choices=["on", "off"])
    p_light.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable light summary"
    )

    p_pause = sub.add_parser("pause", parents=[get_global_parser()], help="Pause current print")
    p_pause.add_argument("--confirm", action="store_true", help="Confirm pausing the running print")
    p_pause.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable pause summary"
    )
    p_resume = sub.add_parser("resume", parents=[get_global_parser()], help="Resume paused print")
    p_resume.add_argument("--confirm", action="store_true", help="Confirm resuming the paused print")
    p_resume.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable resume summary"
    )

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

    sub.add_parser(
        "tui",
        parents=[get_global_parser()],
        help="Live full-screen view of the printer (needs the 'tui' extra)",
    )

    p_go = sub.add_parser(
        "go",
        parents=[get_global_parser()],
        help="Interactive guided print: URL in, plastic out — no slicer knowledge needed",
    )
    p_go.add_argument("source", nargs="?", help="Model URL or local file (skips the first prompt)")

    p_slice = sub.add_parser("slice", parents=[get_global_parser()], help="Slice a model file into .3mf")
    p_slice.add_argument("file", nargs="?", help="Path to .stl, .step, .stp, or .obj file")
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
    _add_slice_override_args(p_slice)
    p_slice.add_argument(
        "--list-settings",
        dest="list_settings",
        action="store_true",
        help="List every settable OrcaSlicer process/filament setting and exit (pair with --json for agents).",
    )

    p_gc = sub.add_parser("gcode", parents=[get_global_parser()], help="Send raw G-code to printer")
    p_gc.add_argument("code", help="G-code command (e.g. 'M104 S220')")
    p_gc.add_argument("--confirm", action="store_true", help="Confirm sending raw G-code to the printer")

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
    p_snap.add_argument(
        "--unique",
        action="store_true",
        help=(
            "Use a unique timestamped filename so repeated captures never overwrite/confuse. "
            "Without --output: saves as printer_snapshot_<UTC>Z.jpg. "
            "With --output: inserts the timestamp before the file extension."
        ),
    )

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
    p_setup.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing access_code_file whose contents differ (default: refuse)",
    )

    return parser
