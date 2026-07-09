# bambu-cli API Reference

The `bambu-cli` provides structured JSON output for programmatic integration (e.g., via AI agents, scripts, or continuous integration systems). 

## Enabling JSON Output

Pass the `--json` global flag to any command to receive machine-readable JSON on standard output (`stdout`). Log messages, warnings, and simulated outputs will continue to be emitted to standard error (`stderr`), ensuring that you can always safely pipe `stdout` to a JSON parser like `jq` or an agent context window.

```bash
bambu-cli status --json
# or without installing:
python3 scripts/bambu.py status --json
```

## Standard JSON Schema

All JSON responses follow a base structure:

```json
{
  "status": "ok",           // "ok" or "error"
  "command": "<command>"    // The command that was executed (e.g., "status", "doctor")
  // ...command-specific payload fields
}
```

If an error occurs, the output will typically include an `error` key with the message:

```json
{
  "status": "error",
  "command": "slice",
  "error": "Timeout waiting for OrcaSlicer to finish."
}
```

## Command Payloads

### `status`

Returns current printer states, temperatures, and hardware versions.

```json
{
  "status": "ok",
  "command": "status",
  "printer": {
    "gcode_state": "IDLE",
    "mc_percent": 0,
    "hw_ver": "P1P",
    "sw_ver": "01.00.00.00",
    "bed_temper": 25,
    "nozzle_temper": 25
  },
  "gcode_state": "IDLE",
  "mc_percent": 0,
  "hw_ver": "P1P",
  "sw_ver": "01.00.00.00",
  "bed_temper": 25,
  "nozzle_temper": 25
}
```

### `doctor`

Runs a network and connectivity health check, discovers printer capabilities, and outputs the detected TLS certificate fingerprint.

```json
{
  "status": "ok",
  "command": "doctor",
  "ok": true,
  "output": "/tmp/printer_capabilities.json",
  "printer_ip": "<redacted>",
  "certificate_fingerprint": "0123456789abcdef...",
  "capabilities": {
    "model": "P1P",
    "firmware": "01.00.00.00",
    "serial": "<redacted>",
    "capabilities": {
      "ams": false,
      "chamber_light": true,
      "camera_snapshot": true,
      "camera_snapshot_note": "snapshot uses the optional BambuP1Streamer container..."
    }
  }
}
```

Machine-checkable schema: [`docs/schemas/doctor.json`](schemas/doctor.json).

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

Errors use the shared [`error_envelope.json`](schemas/error_envelope.json) (`failed_step` often `slicer` / validate).

### `download`

Success after a model file lands on disk (schema: [`download.json`](schemas/download.json)):

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

Archive downloads may also include `archive_entry`. Errors use [`error_envelope.json`](schemas/error_envelope.json) with redacted `source` / `download_url`.

### `config`

`config show` and `config validate` share [`config_cmd.json`](schemas/config_cmd.json)
(named `config_cmd` so the schema is not blocked by the repo's `config.json` gitignore).

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

Success / dry-run: [`job_ok.json`](schemas/job_ok.json).  
Failure: [`job_error.json`](schemas/job_error.json) (superset of [`error_envelope.json`](schemas/error_envelope.json) with the job summary fields such as `would_slice`, `remote_name`, `printed`).

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

Without `--confirm` (schema: [`delete.json`](schemas/delete.json)): `"status": "confirmation_required"`, `"deleted": false`.

### `light` / `pause` / `resume`

- [`light.json`](schemas/light.json): `"status": "light_changed"`, `"action": "on"|"off"`, `"changed": true`
- [`pause.json`](schemas/pause.json): `"status": "paused"`, `"paused": true`
- [`resume.json`](schemas/resume.json): `"status": "resumed"`, `"resumed": true`

### `snapshot`

Schema: [`snapshot.json`](schemas/snapshot.json) — `"status": "saved"`, `"output"`, `"size_bytes"`, plus `method` (direct) or Docker fields.

## Global API Flags

- **Timeouts**: Define strict boundaries for CI/CD or Agent pipelines.
  - `--network-timeout <seconds>`: Global network resolution timeout.
  - `--slicer-timeout <seconds>`: Timeout for OrcaSlicer execution.
  - `--command-timeout <seconds>`: MQTT command acknowledgment timeout.
  - `--upload-timeout <seconds>`: FTPS file upload timeout.
- **Security & Confinement**:
  - `--allow-private-ips`: By default, the CLI prevents fetching models from private/local IPs (SSRF protection). This flag explicitly overrides the safeguard.


## Stability policy (1.0 intent)

JSON fields documented here and validated under `docs/schemas/` are part of the
agent contract. Fields may be **added** in minor releases. Fields may be
**removed or renamed** only after a deprecation window of at least one minor
release (or a major version bump). Exit codes in `bambu_cli.constants` are
stable for a given major version.

Machine-checkable schemas live in `docs/schemas/`. Contract tests under
`tests/contracts/` load those schemas against live CLI output.
