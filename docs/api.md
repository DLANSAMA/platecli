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

## Global API Flags

In addition to `--json`, several flags provide strict API guarantees for programmatic environments:

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
