# platecli API Reference

The `plate` command provides structured JSON output for programmatic integration
(AI agents, scripts, CI). This document is the **human** contract; machine-checkable
schemas live in [`docs/schemas/`](schemas/).

## Enabling JSON output

Pass the `--json` global flag to any command to receive machine-readable JSON on
standard output (`stdout`). Log messages and warnings go to standard error
(`stderr`), so you can safely pipe `stdout` to `jq` or an agent context window.

`--json` may appear **before or after** the subcommand:

```bash
plate status --json
plate --json --version
# or without installing:
python3 scripts/bambu.py status --json
```

## Standard envelopes

### Success

```json
{
  "status": "ok",
  "command": "<command>"
}
```

Some commands use a more specific `status` string (`"sliced"`, `"downloaded"`,
`"saved"`, `"sent"`, `"confirmation_required"`, …). Schema files document the
allowed values per command.

### Error

Shared shape: [`schemas/error_envelope.json`](schemas/error_envelope.json).

```json
{
  "status": "error",
  "command": "slice",
  "error": "Timeout waiting for OrcaSlicer to finish.",
  "exit_code": 6,
  "failed_step": "slicer"
}
```

Job failures may use the superset [`schemas/job_error.json`](schemas/job_error.json)
(includes summary fields such as `would_slice`, `remote_name`, `printed`,
`next_command`, `recovery_hint`).

Path fields under the current home directory are compacted to `~` in agent JSON.

## Exit codes

Stable for a given major version (`bambu_cli.constants`):

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | `EXIT_SUCCESS` | Success |
| 1 | `EXIT_CONFIG_ERROR` | Missing/invalid config or required tool |
| 2 | `EXIT_NETWORK_ERROR` | Connectivity / MQTT / FTPS failure |
| 3 | `EXIT_FILE_ERROR` | Local file I/O |
| 4 | `EXIT_PRINTER_ERROR` | Printer reported error |
| 5 | `EXIT_COMMAND_ERROR` | Invalid usage / command refused |
| 6 | `EXIT_TIMEOUT` | Operation timed out |

Domain code raises `BambuError` / `abort()`; only `cli.main()` calls `sys.exit`.

## Schema inventory

| Schema file | Covers |
|-------------|--------|
| [`ok_envelope.json`](schemas/ok_envelope.json) | Generic success envelope |
| [`error_envelope.json`](schemas/error_envelope.json) | Generic error envelope |
| [`version.json`](schemas/version.json) | `--json --version` |
| [`status.json`](schemas/status.json) | `status --json` snapshot |
| [`status_event.json`](schemas/status_event.json) | `status --monitor --json` NDJSON events |
| [`doctor.json`](schemas/doctor.json) | `doctor` |
| [`preflight.json`](schemas/preflight.json) | `preflight` |
| [`config_cmd.json`](schemas/config_cmd.json) | `config show` / `config validate` |
| [`download.json`](schemas/download.json) | `download` success |
| [`slice.json`](schemas/slice.json) | `slice` success |
| [`slice_list_settings.json`](schemas/slice_list_settings.json) | `slice --list-settings` |
| [`job_ok.json`](schemas/job_ok.json) | `job` / `send` success / dry-run |
| [`job_error.json`](schemas/job_error.json) | `job` / `send` failure |
| [`print.json`](schemas/print.json) | `print` (incl. confirmation_required) |
| [`gcode.json`](schemas/gcode.json) | `gcode` |
| [`delete.json`](schemas/delete.json) | `delete` |
| [`light.json`](schemas/light.json) | `light` |
| [`pause.json`](schemas/pause.json) | `pause` |
| [`resume.json`](schemas/resume.json) | `resume` |
| [`snapshot.json`](schemas/snapshot.json) | `snapshot` |

**Not yet dedicated schema files** (may still emit JSON; contract coverage is
lighter or via envelopes): one-shot `status` success (beyond envelope + AMS
fields documented below), `upload`, `files`, `stop`, `setup`. Tracked in
[test-backlog.md](test-backlog.md).

Contract tests: `tests/contracts/test_schema_validation.py` and
`tests/test_json_contracts.py`.

## Command payloads

### `version`

```json
{"status": "ok", "command": "version", "version": "0.1.0"}
```

Schema: [`version.json`](schemas/version.json).

### `status`

One-shot query returns printer state, temperatures, and a normalized AMS block:

```json
{
  "status": "ok",
  "command": "status",
  "printer": { "...raw printer map..." },
  "gcode_state": "IDLE",
  "mc_percent": 0,
  "ams": null
}
```

`ams` is either `null` (no AMS) or:

```json
{
  "active_tray": 1,
  "units": [
    {
      "id": 0,
      "humidity": 4,
      "temp": 28.5,
      "trays": [
        {"slot": 0, "type": "PLA", "color": "F2F2F2", "remain": 80, "empty": false, "active": false}
      ]
    }
  ]
}
```

Top-level keys also mirror common fields from the raw printer map for convenience.

### `status --monitor` (NDJSON)

Streams one JSON object per line until a terminal state. Schema:
[`status_event.json`](schemas/status_event.json).

```json
{"event":"update","command":"status","gcode_state":"RUNNING","mc_percent":42}
{"event":"terminal","command":"status","gcode_state":"FINISH","mc_percent":100}
```

Use `--sim` to exercise the shape without a printer.

### `doctor`

Runs connectivity checks and reports the live TLS certificate fingerprint.
Schema: [`doctor.json`](schemas/doctor.json). Serial and other secrets are redacted.

```json
{
  "status": "ok",
  "command": "doctor",
  "ok": true,
  "output": "/tmp/printer_capabilities.json",
  "printer_ip": "<redacted>",
  "certificate_fingerprint": "0123456789abcdef...",
  "capabilities": { }
}
```

In interactive TTY (not `--json`), doctor may offer to write `cert_fingerprint`
into config when none is pinned. That prompt never runs in JSON or non-TTY mode.

### `preflight`

Local config/health checks without requiring a full live print path.
Schema: [`preflight.json`](schemas/preflight.json). Supports `--strict`.

### `slice`

Success after OrcaSlicer produces a valid `.gcode.3mf` (schema: [`slice.json`](schemas/slice.json)):

```json
{
  "status": "sliced",
  "command": "slice",
  "file": "~/models/cube.stl",
  "path": "~/models/cube.gcode.3mf",
  "filename": "cube.gcode.3mf",
  "bytes": 4096,
  "step_converted": false
}
```

Errors use [`error_envelope.json`](schemas/error_envelope.json). Missing profiles
errors may include `profiles_dir` and `detected_profiles_dir`.

`slice --list-settings [--json]`: [`slice_list_settings.json`](schemas/slice_list_settings.json).

### `download`

Schema: [`download.json`](schemas/download.json).

```json
{
  "status": "downloaded",
  "command": "download",
  "source": "https://example.com/model.stl",
  "normalized_source": null,
  "download_url": "https://example.com/model.stl",
  "path": "/tmp/model.stl",
  "filename": "model.stl",
  "bytes": 1024
}
```

Archive downloads may also include `archive_entry`. Errors redact `source` /
`download_url`. Private IPs are refused unless `--allow-private-ips`.

### `config`

`config show` and `config validate` share [`config_cmd.json`](schemas/config_cmd.json)
(named `config_cmd` so the schema is not blocked by a `config.json` gitignore).

Show (secrets redacted):

```json
{
  "status": "ok",
  "command": "config",
  "action": "show",
  "config_path": "~/.config/bambu/config.json",
  "config": {
    "printer_ip": "192.168.1.10",
    "access_code": "<redacted>"
  }
}
```

Validate includes `checks[]`, `ok`, `errors`, `warnings`, `exit_code`, and `strict`.

### `job` / `send`

`send` is an alias of `job`.  
Success / dry-run: [`job_ok.json`](schemas/job_ok.json).  
Failure: [`job_error.json`](schemas/job_error.json).

Print start requires `--confirm`; without it the job may upload but will not print.

### `gcode`

Without `--confirm` (schema: [`gcode.json`](schemas/gcode.json)):

```json
{
  "status": "confirmation_required",
  "command": "gcode",
  "gcode": "G28",
  "sent": false,
  "next_command": ["gcode", "G28", "--confirm", "--json"]
}
```

After send: `"status": "sent", "sent": true`.

### `print`

Without `--confirm` (schema: [`print.json`](schemas/print.json)):

```json
{
  "status": "confirmation_required",
  "command": "print",
  "file": "cube.gcode.3mf",
  "printed": false,
  "next_command": ["print", "cube.gcode.3mf", "--confirm", "--json"]
}
```

### `delete`

Without `--confirm` (schema: [`delete.json`](schemas/delete.json)):
`"status": "confirmation_required"`, `"deleted": false`.

### `stop`

Requires `--confirm` (same intent pattern as delete/print). Dedicated schema file
not yet published; treat as confirmation + ok/error envelopes until added.

### `light` / `pause` / `resume`

- [`light.json`](schemas/light.json): `"status": "light_changed"`, `"action": "on"|"off"`, `"changed": true`
- [`pause.json`](schemas/pause.json): `"status": "paused"`, `"paused": true`
- [`resume.json`](schemas/resume.json): `"status": "resumed"`, `"resumed": true`

**Note:** `pause` and `resume` do **not** require `--confirm` today (unlike stop/print).
See [SECURITY.md](../SECURITY.md).

### `snapshot`

Schema: [`snapshot.json`](schemas/snapshot.json) — `"status": "saved"`, `"output"`,
`"size_bytes"`, `"captured_at"` (ISO-8601 UTC), `"sha256"` (hex digest of JPEG bytes),
plus `method` (`direct`) or Docker-related fields when the streamer path is used.
Use `--unique` to get a timestamped filename so repeated captures never overwrite/confuse.
Agents should compare `sha256` / `captured_at` to verify a new frame was captured before
sending the image to a user.

## Global API flags

- **Timeouts** (also optional config keys):
  - `--network-timeout <seconds>`
  - `--slicer-timeout <seconds>`
  - `--command-timeout <seconds>`
  - `--upload-timeout <seconds>`
- **Security & confinement**:
  - `--allow-private-ips`: opt in to private/LAN model fetches (default deny). Not sticky config.
  - `--max-download-mb`: size cap for URL download and ZIP members (default 2048).
- **Simulation**:
  - `--sim`: no real printer traffic for supported paths.

## Stability policy (1.0 intent)

JSON fields documented here and validated under `docs/schemas/` are part of the
agent contract. Fields may be **added** in minor releases. Fields may be
**removed or renamed** only after a deprecation window of at least one minor
release (or a major version bump). Exit codes in `bambu_cli.constants` are
stable for a given major version.

Config keys: prefer additive changes; deprecate with a warning (as with inline
`access_code`) before removal.

Machine-checkable schemas live in `docs/schemas/`. Contract tests under
`tests/contracts/` load those schemas against live CLI output.

## Related docs

- [AGENTS.md](../AGENTS.md) — agent architecture and safety
- [SECURITY.md](../SECURITY.md) — threat model
- [quality-roadmap.md](quality-roadmap.md) — quality scoreboard
- [test-backlog.md](test-backlog.md) — remaining schema/coverage gaps
