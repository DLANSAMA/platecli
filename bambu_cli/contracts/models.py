"""The published ``--json`` contracts, one dataclass per schema.

These generate ``docs/schemas/*.json``. Edit a model, run
``python scripts/gen_schemas.py``, commit both — CI fails if they disagree.

Field order here is the key order in the emitted payload, so keep ``status``
and ``command`` first: agents pattern-match on those.

``Literal`` pins a value the command always emits (it becomes ``const`` in the
schema, which is what lets a consumer dispatch on ``status``). A field typed
``X | None`` with a ``None`` default is optional and omitted when unset unless
it is named in ``keep_none``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from bambu_cli.contracts.base import Contract, spec

# ---------------------------------------------------------------------------
# Nested structures. These are not published on their own; they are inlined
# into the parent schema so each file stays self-contained.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteFile:
    """One entry in the `files` listing."""

    name: str
    path: str


@dataclass(frozen=True)
class PreflightCheck:
    """One `preflight` result row."""

    status: str
    name: str
    message: str
    detail: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProcessSettings:
    """The `slice --list-settings` process group: key count and an example of each."""

    count: int
    settings: dict[str, Any] = spec(
        required=True,
        default_factory=dict,
        description="Map of process setting key to a representative/example value.",
    )


@dataclass(frozen=True)
class FilamentSettings:
    """The `slice --list-settings` filament group: key count and an example of each."""

    count: int
    settings: dict[str, Any] = spec(
        required=True,
        default_factory=dict,
        description="Map of filament setting key to a representative/example value.",
    )


@dataclass(frozen=True)
class AmsTray:
    slot: float | None = None
    active: bool | None = None
    empty: bool | None = None


@dataclass(frozen=True)
class AmsUnit:
    id: float | None = None
    humidity: float | None = None
    temp: float | None = None
    trays: list[AmsTray] | None = None


@dataclass(frozen=True)
class AmsState:
    units: list[AmsUnit] | None = None


@dataclass(frozen=True)
class PrinterState:
    """Normalised printer state under `status.printer`."""

    gcode_state: str | None = None
    mc_percent: float | None = None
    bed_temper: float | None = None
    bed_target_temper: float | None = None
    nozzle_temper: float | None = None
    nozzle_target_temper: float | None = None
    cooling_fan_speed: float | None = None
    wifi_signal: str | None = None
    sw_ver: str | None = None
    hw_ver: str | None = None
    ams: AmsState | None = spec(default=None, description="Normalised AMS state (present when AMS is attached).")


# ---------------------------------------------------------------------------
# Shared envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OkEnvelope(Contract):
    """Minimum shape every successful command shares."""

    schema_name: ClassVar[str] = "ok_envelope"
    schema_title: ClassVar[str] = "platecli JSON ok envelope"

    status: str
    command: str = spec(required=True, min_length=1)


@dataclass(frozen=True)
class ErrorEnvelope(Contract):
    """Minimum shape every failed command shares.

    ``next_command`` is the recovery hint agents follow; ``detail`` carries the
    failing sub-command's own error payload when a pipeline stage failed.
    """

    schema_name: ClassVar[str] = "error_envelope"
    schema_title: ClassVar[str] = "platecli JSON error envelope"
    keep_none: ClassVar[frozenset[str]] = frozenset({"next_command"})

    status: Literal["error"]
    command: str = spec(required=True, min_length=1)
    exit_code: int = spec(required=True)
    error: str = spec(required=True)
    failed_step: str | None = None
    printer_error_code: int | None = None
    printer_error_code_hex: str | None = None
    next_command: list[str] | None = None
    detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Printer commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Status(Contract):
    schema_name: ClassVar[str] = "status"
    schema_title: ClassVar[str] = "platecli status success envelope"
    schema_description: ClassVar[str] = (
        "JSON output of `plate status --json`. Guaranteed printer fields live under `printer` "
        "(never flattened onto the envelope). Firmware extras stay on `printer` as additional "
        "properties. `ams` is the normalised tray view for --ams-mapping."
    )
    keep_none: ClassVar[frozenset[str]] = frozenset({"ams"})

    status: Literal["ok"]
    command: Literal["status"]
    printer: PrinterState | None = spec(
        default=None,
        required=True,
        description=(
            "Complete printer state. Merged from the MQTT report topic and guaranteed to be a full "
            "snapshot, never a partial delta; the command fails with exit code 6 rather than emitting "
            "an incomplete object. Extra firmware keys stay here, not on the envelope."
        ),
        requires_keys=("gcode_state", "mc_percent", "bed_temper", "nozzle_temper"),
    )
    ams: dict[str, Any] | None = spec(
        default=None,
        description="Normalised AMS trays for --ams-mapping; null when the printer has no AMS.",
    )


@dataclass(frozen=True)
class StatusEvent(Contract):
    """One NDJSON line from ``status --monitor --json``."""

    schema_name: ClassVar[str] = "status_event"
    schema_title: ClassVar[str] = "platecli status --monitor NDJSON event"

    event: Literal["update", "terminal"]
    command: Literal["status"]
    gcode_state: str
    mc_percent: int


@dataclass(frozen=True)
class Light(Contract):
    schema_name: ClassVar[str] = "light"
    schema_title: ClassVar[str] = "platecli light success envelope"

    status: Literal["light_changed"]
    command: Literal["light"]
    action: Literal["on", "off"]
    changed: bool


@dataclass(frozen=True)
class Pause(Contract):
    schema_name: ClassVar[str] = "pause"
    schema_title: ClassVar[str] = "platecli pause success or confirmation envelope"

    status: Literal["paused", "confirmation_required"]
    command: Literal["pause"]
    paused: bool
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Resume(Contract):
    schema_name: ClassVar[str] = "resume"
    schema_title: ClassVar[str] = "platecli resume success or confirmation envelope"

    status: Literal["resumed", "confirmation_required"]
    command: Literal["resume"]
    resumed: bool
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Stop(Contract):
    schema_name: ClassVar[str] = "stop"
    schema_title: ClassVar[str] = "platecli stop success or confirmation envelope"

    status: Literal["stopped", "confirmation_required"]
    command: Literal["stop"]
    stopped: bool
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Gcode(Contract):
    schema_name: ClassVar[str] = "gcode"
    schema_title: ClassVar[str] = "platecli gcode success or confirmation envelope"

    status: str
    command: Literal["gcode"]
    gcode: str
    sent: bool
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Files(Contract):
    schema_name: ClassVar[str] = "files"
    schema_title: ClassVar[str] = "platecli files listing envelope"

    status: Literal["ok"]
    command: Literal["files"]
    count: int = spec(required=True, minimum=0)
    files: list[RemoteFile] = spec(required=True, default_factory=list)


@dataclass(frozen=True)
class Delete(Contract):
    schema_name: ClassVar[str] = "delete"
    schema_title: ClassVar[str] = "platecli delete success or confirmation envelope"

    status: str
    command: Literal["delete"]
    file: str
    deleted: bool
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Upload(Contract):
    schema_name: ClassVar[str] = "upload"
    schema_title: ClassVar[str] = "platecli upload success or dry-run envelope"

    status: Literal["uploaded", "dry_run_ok"]
    command: Literal["upload"]
    file: str
    remote_name: str
    bytes: int = spec(required=True, minimum=0)
    uploaded: bool = spec(required=True)
    size_verified: bool | None = spec(
        default=None,
        description="False when the printer did not report SIZE after STOR; True when SIZE matched.",
    )


@dataclass(frozen=True)
class Print(Contract):
    schema_name: ClassVar[str] = "print"
    schema_title: ClassVar[str] = "platecli print success or confirmation envelope"

    status: str
    command: Literal["print"]
    file: str
    printed: bool | None = None
    dry_run: bool | None = None
    next_command: list[str] | None = None


@dataclass(frozen=True)
class Snapshot(Contract):
    schema_name: ClassVar[str] = "snapshot"
    schema_title: ClassVar[str] = "platecli snapshot success envelope"

    status: Literal["saved"]
    command: Literal["snapshot"]
    output: str = spec(required=True, min_length=1)
    size_bytes: int = spec(required=True)
    captured_at: str = spec(
        required=True,
        description="ISO-8601 UTC timestamp of capture (e.g. 2026-07-24T19:15:30Z)",
    )
    sha256: str = spec(
        required=True,
        description="Hex SHA-256 digest of the captured JPEG bytes; use to verify a capture is new",
    )
    method: str | None = None
    camera_image: str | None = None
    docker_container: str | None = None


# ---------------------------------------------------------------------------
# Local commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Download(Contract):
    schema_name: ClassVar[str] = "download"
    schema_title: ClassVar[str] = "platecli download success envelope"
    keep_none: ClassVar[frozenset[str]] = frozenset({"normalized_source"})

    status: Literal["downloaded"]
    command: Literal["download"]
    source: str
    normalized_source: str | None = None
    download_url: str = spec(required=True, default="")
    path: str = spec(required=True, min_length=1, default="")
    filename: str = spec(required=True, min_length=1, default="")
    bytes: int = spec(required=True, default=0)
    archive_entry: str | None = None
    size_verified: bool | None = spec(
        default=None,
        description="Set on FTPS printer downloads when SIZE was checked; omitted for HTTP downloads.",
    )


@dataclass(frozen=True)
class Slice(Contract):
    schema_name: ClassVar[str] = "slice"
    schema_title: ClassVar[str] = "platecli slice success envelope"

    status: Literal["sliced"]
    command: Literal["slice"]
    file: str = spec(required=True, min_length=1)
    path: str = spec(required=True, min_length=1)
    filename: str = spec(required=True, min_length=1)
    bytes: int = spec(required=True)
    step_converted: bool = spec(required=True)


@dataclass(frozen=True)
class SliceListSettings(Contract):
    """``slice --list-settings``: the full OrcaSlicer key surface."""

    schema_name: ClassVar[str] = "slice_list_settings"
    schema_title: ClassVar[str] = "platecli slice --list-settings result envelope"
    schema_description: ClassVar[str] = (
        "Discovery output listing every settable OrcaSlicer process/filament setting. Agents read this to learn the override vocabulary, then drive it via --set / --set-filament / --settings-json."
    )

    status: Literal["ok"]
    command: Literal["slice"]
    action: Literal["list_settings"]
    profiles_dir: str | None = None
    process: ProcessSettings | None = spec(required=True, default=None)
    filament: FilamentSettings | None = spec(required=True, default=None)


@dataclass(frozen=True)
class Version(Contract):
    """``--version``. The one strict schema: nothing else may appear."""

    schema_name: ClassVar[str] = "version"
    schema_title: ClassVar[str] = "platecli --version envelope"
    additional_properties: ClassVar[bool] = False

    status: Literal["ok"]
    command: Literal["version"]
    version: str = spec(required=True, min_length=1)


# ---------------------------------------------------------------------------
# Setup / diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Setup(Contract):
    """``setup`` reports *whether* things are configured, never their values."""

    schema_name: ClassVar[str] = "setup"
    schema_title: ClassVar[str] = "platecli setup summary envelope"
    schema_description: ClassVar[str] = (
        "Reports whether each setting is configured rather than its value, so the summary stays safe to paste into a bug report. The access code itself is never included; access_code_storage says only where it lives."
    )
    keep_none: ClassVar[frozenset[str]] = frozenset({"model", "nozzle"})

    status: Literal["configured"]
    command: Literal["setup"]
    config_path: str
    printer_ip_configured: bool
    serial_configured: bool
    access_code_storage: Literal["file", "inline"]
    model: str | None = None
    nozzle: str | None = None
    orca_slicer_configured: bool = spec(required=True, default=False)
    profiles_dir_configured: bool = spec(required=True, default=False)
    cert_fingerprint_configured: bool = spec(required=True, default=False)
    insecure_tls: bool = spec(required=True, default=False)
    access_code_file: str | None = None


@dataclass(frozen=True)
class ConfigCmd(Contract):
    schema_name: ClassVar[str] = "config_cmd"
    schema_title: ClassVar[str] = "platecli config show/validate envelope"

    status: str
    command: Literal["config"]
    action: Literal["show", "validate"]
    config_path: str | None = None
    config: dict[str, Any] | None = None
    exit_code: int | None = None
    ok: bool | None = None
    errors: int | None = None
    warnings: int | None = None
    strict: bool | None = None
    checks: list[Any] | None = None


@dataclass(frozen=True)
class Preflight(Contract):
    schema_name: ClassVar[str] = "preflight"
    schema_title: ClassVar[str] = "platecli preflight result envelope"

    status: str
    command: Literal["preflight"]
    checks: list[PreflightCheck] = spec(required=True, default_factory=list)


@dataclass(frozen=True)
class MigrateAccessCode(Contract):
    schema_name: ClassVar[str] = "migrate_access_code"
    schema_title: ClassVar[str] = "platecli setup --migrate-access-code envelope"
    schema_description: ClassVar[str] = (
        "Result of moving an inline access_code out of config.json into a separate secret file. "
        "Never includes the access code value itself."
    )

    status: Literal["migrated", "noop", "error"]
    command: Literal["migrate-access-code"]
    config_path: str | None = None
    access_code_file: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Doctor(Contract):
    schema_name: ClassVar[str] = "doctor"
    schema_title: ClassVar[str] = "platecli doctor result envelope"
    keep_none: ClassVar[frozenset[str]] = frozenset({"certificate_fingerprint"})

    status: str
    command: Literal["doctor"]
    checks: list[Any] | None = None
    certificate_fingerprint: str | None = None
    printer_reachable: bool | None = None


# ---------------------------------------------------------------------------
# Orchestrated / interactive
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobOk(Contract):
    schema_name: ClassVar[str] = "job_ok"
    schema_title: ClassVar[str] = "platecli job/send success or dry-run envelope"

    status: str
    command: str = spec(required=True, min_length=1)
    steps: dict[str, Any] | None = None
    source: str | None = None
    local_path: str | None = None
    remote_path: str | None = None
    size_verified: bool | None = spec(
        default=None,
        description="False when the printer omitted FTPS SIZE after upload.",
    )
    print_started: bool | None = None
    dry_run: bool | None = None
    copies_ignored: bool | None = None


@dataclass(frozen=True)
class JobError(Contract):
    """``job``/``send`` failure: the error envelope plus pipeline progress.

    The extra fields say how far the pipeline got before failing, so an agent
    can resume rather than restart.
    """

    schema_name: ClassVar[str] = "job_error"
    schema_title: ClassVar[str] = "platecli job/send error envelope (summary + error fields)"
    keep_none: ClassVar[frozenset[str]] = frozenset(
        {
            "source",
            "normalized_source",
            "downloaded_path",
            "extracted_path",
            "archive_entry",
            "printable_path",
            "remote_name",
            "workdir",
            "next_command",
        }
    )

    status: Literal["error"]
    command: str = spec(required=True, min_length=1)
    exit_code: int = spec(required=True)
    error: str = spec(required=True)
    failed_step: str = spec(required=True, min_length=1)
    printer_error_code: int | None = None
    printer_error_code_hex: str | None = None
    source: str | None = None
    normalized_source: str | None = None
    downloaded_path: str | None = None
    extracted_path: str | None = None
    archive_entry: str | None = None
    printable_path: str | None = None
    remote_name: str | None = None
    printed: bool | None = None
    uploaded: bool | None = None
    dry_run: bool | None = None
    upload_only: bool | None = None
    workdir: str | None = None
    next_command: list[str] | None = None
    would_download: bool | None = None
    would_extract: bool | None = None
    would_slice: bool | None = None
    would_upload: bool | None = None
    would_print: bool | None = None
    copies_ignored: bool | None = None


@dataclass(frozen=True)
class Tui(Contract):
    """``tui`` is a full-screen Textual UI: ``--json`` only ever reports refusal.

    Same shape as :class:`Go` — both are human-only front-ends with no machine
    contract (AGENTS.md), so the only payload either can emit is the refusal.
    """

    schema_name: ClassVar[str] = "tui"
    schema_title: ClassVar[str] = "platecli tui error envelope (interactive command; --json always errors)"

    status: Literal["error"]
    command: Literal["tui"]
    exit_code: Literal[5]
    error: str = spec(required=True, min_length=1)
    failed_step: Literal["parse"] = spec(required=True, default="parse")


@dataclass(frozen=True)
class Go(Contract):
    """``go`` is an interactive wizard: ``--json`` only ever reports refusal."""

    schema_name: ClassVar[str] = "go"
    schema_title: ClassVar[str] = "platecli go error envelope (interactive command; --json always errors)"

    status: Literal["error"]
    command: Literal["go"]
    exit_code: Literal[5]
    error: str = spec(required=True, min_length=1)
    failed_step: Literal["parse"] = spec(required=True, default="parse")
