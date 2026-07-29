"""argparse / argument-coercion helpers shared by the CLI and domain modules.

Extracted from ``bambu_cli.cli`` (roadmap B.4). These read values off an
``argparse.Namespace`` (or a stand-in) and normalize exit codes for
machine-readable summaries. They never terminate the process; domain callers
raise ``BambuError`` / ``abort`` instead.
"""

from .constants import EXIT_COMMAND_ERROR, EXIT_SUCCESS

__all__ = [
    "namespace_get",
    "exit_code_from_system_exit",
    "setup_args_provided",
]


def namespace_get(args, name, default=None):
    """Read argparse.Namespace values without treating MagicMock attributes as set."""
    try:
        return vars(args).get(name, default)
    except TypeError:
        return default


def exit_code_from_system_exit(exc, default=EXIT_COMMAND_ERROR):
    """Normalize SystemExit / BambuError codes for machine-readable summaries."""
    code = getattr(exc, "exit_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if code is None:
        return EXIT_SUCCESS
    return default


def setup_args_provided(args):
    return any(
        namespace_get(args, attr) is not None
        for attr in ("printer_ip", "serial", "access_code", "access_code_env", "access_code_file", "model", "nozzle")
    )
