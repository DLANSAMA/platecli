"""Structured exception hierarchy for the CLI's JSON error contract.

Command implementations raise these instead of calling ``sys.exit``. ``cli.main()``
translates them into process exit codes and the standard ``--json`` error payload.
"""

from __future__ import annotations

from typing import NoReturn

from bambu_cli.constants import (
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_FILE_ERROR,
    EXIT_NETWORK_ERROR,
    EXIT_PRINTER_ERROR,
    EXIT_TIMEOUT,
)

__all__ = [
    "BambuError",
    "ConfigError",
    "PrinterConnectionError",
    "AuthError",
    "UploadError",
    "DownloadRejected",
    "SliceError",
    "NetworkError",
    "FileError",
    "TimeoutError",
    "PrinterError",
    "abort",
]


class BambuError(Exception):
    """Base class for structured CLI failures; carries JSON-contract fields.

    Attributes:
        detail: Extra machine-readable context to include in the JSON payload.
        next_command: An optional suggested follow-up command for the user.
        exit_code: Process exit code to use when this error escapes ``main()``.
        failed_step: Which pipeline stage failed (e.g. "config", "connect").
    """

    exit_code: int = EXIT_COMMAND_ERROR
    failed_step: str | None = None

    def __init__(self, message, *, detail=None, next_command=None, exit_code=None, failed_step=None):
        super().__init__(message)
        self.detail = detail or {}
        self.next_command = next_command
        if exit_code is not None:
            self.exit_code = exit_code
        if failed_step is not None:
            self.failed_step = failed_step


class ConfigError(BambuError):
    """Raised when the CLI configuration is missing or invalid."""

    exit_code = EXIT_CONFIG_ERROR
    failed_step = "config"


class PrinterConnectionError(BambuError):
    """Raised when the CLI fails to connect to the printer."""

    failed_step = "connect"


class AuthError(PrinterConnectionError):
    """Raised when the printer rejects credentials; not retryable."""

    failed_step = "connect"


class UploadError(BambuError):
    """Raised when uploading a file to the printer fails."""

    failed_step = "upload"


class DownloadRejected(BambuError):
    """Raised when a download is rejected (SSRF, size, type, archive)."""

    failed_step = "download"


class SliceError(BambuError):
    """Raised when slicing a model with OrcaSlicer fails."""

    failed_step = "slice"


class NetworkError(BambuError):
    """Raised on MQTT/FTPS/network failures."""

    exit_code = EXIT_NETWORK_ERROR
    failed_step = "network"


class FileError(BambuError):
    """Raised on local or remote file validation failures."""

    exit_code = EXIT_FILE_ERROR
    failed_step = "file"


class TimeoutError(BambuError):  # noqa: A001 — deliberate domain name, not builtin shadow for callers
    """Raised when a printer or slicer operation times out."""

    exit_code = EXIT_TIMEOUT
    failed_step = "timeout"


class PrinterError(BambuError):
    """Raised when the printer reports a job/device error."""

    exit_code = EXIT_PRINTER_ERROR
    failed_step = "printer"


_EXIT_TO_EXC = {
    EXIT_CONFIG_ERROR: ConfigError,
    EXIT_NETWORK_ERROR: NetworkError,
    EXIT_FILE_ERROR: FileError,
    EXIT_PRINTER_ERROR: PrinterError,
    EXIT_COMMAND_ERROR: BambuError,
    EXIT_TIMEOUT: TimeoutError,
}


def abort(
    message: str = "",
    *,
    exit_code: int = EXIT_COMMAND_ERROR,
    failed_step=None,
    detail=None,
    next_command=None,
) -> NoReturn:
    """Raise the appropriate structured error for ``exit_code`` (domain code never calls ``sys.exit``)."""
    cls = _EXIT_TO_EXC.get(exit_code, BambuError)
    raise cls(
        message or f"Command failed (exit {exit_code})",
        exit_code=exit_code,
        failed_step=failed_step,
        detail=detail,
        next_command=next_command,
    )
