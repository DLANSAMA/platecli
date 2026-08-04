"""Typed contracts for every ``--json`` payload platecli emits.

``docs/schemas/*.json`` is generated from these models by
``scripts/gen_schemas.py``; CI regenerates and diffs, so a schema can never
drift from the code that produces it. Change a payload here, regenerate, commit
both.

Serialization stays in ``bambu_cli.utils.emit_json`` — it applies credential
redaction and home-directory compaction to every emitted string, and nothing
here may bypass it. See ``base.py`` for why these are stdlib dataclasses rather
than pydantic models.
"""

from bambu_cli.contracts.base import Contract, all_contracts
from bambu_cli.contracts.models import (
    AmsState,
    AmsTray,
    AmsUnit,
    ConfigCmd,
    Delete,
    Doctor,
    Download,
    ErrorEnvelope,
    FilamentSettings,
    Files,
    Gcode,
    Go,
    JobError,
    JobOk,
    Light,
    OkEnvelope,
    Pause,
    Preflight,
    PreflightCheck,
    Print,
    PrinterState,
    ProcessSettings,
    RemoteFile,
    Resume,
    Setup,
    Slice,
    SliceListSettings,
    Snapshot,
    Status,
    StatusEvent,
    Stop,
    Upload,
    Version,
)

__all__ = [
    "AmsState",
    "AmsTray",
    "AmsUnit",
    "ConfigCmd",
    "Contract",
    "Delete",
    "Doctor",
    "Download",
    "ErrorEnvelope",
    "FilamentSettings",
    "Files",
    "Gcode",
    "Go",
    "JobError",
    "JobOk",
    "Light",
    "OkEnvelope",
    "Pause",
    "Preflight",
    "PreflightCheck",
    "Print",
    "PrinterState",
    "ProcessSettings",
    "RemoteFile",
    "Resume",
    "Setup",
    "Slice",
    "SliceListSettings",
    "Snapshot",
    "Status",
    "StatusEvent",
    "Stop",
    "Upload",
    "Version",
    "all_contracts",
]
