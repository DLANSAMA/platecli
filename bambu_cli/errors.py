"""Structured exception hierarchy for the CLI's JSON error contract.

These exceptions let command implementations signal a failure with enough
structure (exit code, failed step, machine-readable detail, suggested next
command) that ``cli.main()`` can translate them directly into the standard
``--json`` error payload without each call site having to call
``emit_json_error`` itself.

This module only introduces the mechanism; existing code is not yet
converted to raise these exceptions.
"""

from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR

__all__ = [
    "BambuError",
    "ConfigError",
    "PrinterConnectionError",
    "AuthError",
    "UploadError",
    "DownloadRejected",
    "SliceError",
]


class BambuError(Exception):
    """Base class for structured CLI failures; carries JSON-contract fields.

    Attributes:
        detail: Extra machine-readable context to include in the JSON payload.
        next_command: An optional suggested follow-up command for the user.
        exit_code: Process exit code to use when this error escapes ``main()``.
        failed_step: Which pipeline stage failed (e.g. "config", "connect").
    """

    exit_code = EXIT_COMMAND_ERROR
    failed_step = None

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
    """Raised when the printer rejects or aborts a file download."""

    failed_step = "download"


class SliceError(BambuError):
    """Raised when slicing a model with OrcaSlicer fails."""

    failed_step = "slice"
