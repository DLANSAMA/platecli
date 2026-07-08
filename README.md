# bambu-cli — CLI for Bambu Lab Printers

[![CI](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/ci.yml)
[![Release Packaging](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/release.yml/badge.svg)](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Fully local 3D printing pipeline for Bambu Lab printers. Runs on **Linux, macOS, and Windows**. Download models from Printables, slice with OrcaSlicer, and print — all controlled via CLI by any AI agent or by hand. No cloud account needed.

**Supports:** P1P, P1S, X1C, X1E, A1, A1 Mini (any Bambu printer with LAN mode)

> **Disclaimer:** bambu-cli is an unofficial, community-developed tool. It is not affiliated with, endorsed by, or supported by Bambu Lab. "Bambu Lab" and product names are trademarks of their respective owners, used here only to describe compatibility. The printer protocols (MQTT/FTPS) are reverse-engineered; a firmware update may break functionality without warning — run `bambu-cli doctor` after printer updates.

## Installation

The examples below use the installed `bambu-cli` command.

```bash
pip install bambu-local-cli
```

> Note: the package is published on PyPI as `bambu-local-cli` (the `bambu-cli` name there belongs to an unrelated project). The installed command is still `bambu-cli`.

Or from source:

```bash
pip install .
```

## Features

- **Jobs & URL Support** — Use `job` when an agent or user gives either a website URL or a local file path. It handles everything in one shot.
- **Safe Extraction** — ZIP archives containing model files are fully supported. Existing files are kept safe by creating a numbered sibling such as `model-1.stl`. URL downloads and ZIP extraction have a 2048 MB safety limit, adjustable via `--max-download-mb`.
- **Modularity** — Run steps individually using `download`, `slice`, `upload`, or `print`.
- **Safety First** — The one-shot command will not start a print unless `--confirm` is present.
- **Diagnostics** — Network, FTPS, and MQTT health checking with `doctor` and `preflight`.

## Setup

Use the interactive `setup` command to create your config securely:

```bash
bambu-cli setup
```

Inspect or check the resulting config at any time:

```bash
bambu-cli config show       # print config path + contents (access code redacted)
bambu-cli config validate   # check config values without contacting the printer
```

## Usage

```bash
# Full workflow (download, slice, upload, and start print)
bambu-cli job "https://www.printables.com/model/12345-thing" --confirm --json
```

For programmatic checks, `bambu-cli --json --version` emits JSON version details.

### Monitoring a print

`bambu-cli status --monitor` (alias `--wait`) follows a print until it reaches a
terminal state (`FINISH`, `FAILED`, `STOP`, or `IDLE`). For a human it renders a
live progress bar; for an agent, add `--json` to stream **newline-delimited
JSON** (NDJSON) — one compact object per change as the print advances:

```bash
bambu-cli status --monitor --json
```
```json
{"event":"update","command":"status","gcode_state":"RUNNING","mc_percent":42,"layer_num":50,"total_layer_num":200,"mc_remaining_time":33,"nozzle_temper":220,"nozzle_target_temper":220,"bed_temper":60,"bed_target_temper":60,"gcode_file":"model.gcode"}
{"event":"terminal","command":"status","gcode_state":"FINISH","mc_percent":100,"layer_num":200,"total_layer_num":200,"mc_remaining_time":0,"nozzle_temper":38,"nozzle_target_temper":0,"bed_temper":31,"bed_target_temper":0,"gcode_file":"model.gcode"}
```

Each line is a self-contained JSON object, so an agent can consume the stream
incrementally and stop once it sees `"event":"terminal"`. Pair with `--sim` to
exercise the exact event shape without a printer.

### Global flags

| Flag | Description |
|------|-------------|
| `--json` | Emit JSON for commands that support it; may appear before the subcommand |
| `--max-download-mb` | Cap URL download and ZIP extraction size (default 2048 MB); accepted by `job`, `send`, and `download` |

### Slicing & AMS

`slice` accepts common mesh formats in the precedence order STL > STEP > OBJ > 3MF > G-code. When mapping filaments to AMS slots, mapping arguments take zero-or-positive slot indexes.

To decide that mapping, read what is actually loaded first: `bambu-cli status`
shows each AMS unit's trays (filament type, colour, and remaining %), and
`status --json` includes a normalized `ams` block agents can consume directly:

```json
"ams": {
  "active_tray": 1,
  "units": [
    {"id": 0, "humidity": 4, "temp": 28.5, "trays": [
      {"slot": 0, "type": "PLA",  "color": "F2F2F2", "remain": 80, "empty": false, "active": false},
      {"slot": 1, "type": "PETG", "color": "0A0AC8", "remain": 55, "empty": false, "active": true},
      {"slot": 2, "type": null,   "color": null,     "remain": null, "empty": true,  "active": false}
    ]}
  ]
}
```

`ams` is `null` on printers without an AMS. `active` marks the currently loaded
tray (absolute index `unit * 4 + slot`); feed the `slot` indexes to
`--ams-mapping` when printing with `--use-ams`.

## Project layout

- `bambu_cli/` — Runtime package used by installed command (`bambu-cli`).
- `scripts/bambu.py` — Compatibility wrapper for direct script usage without installing.
- `tests/` — Smoke and unit tests, including `ci_workflow_smoke.py`, `python_compat_smoke.py`, and `release_readiness_smoke.py`.

## Config Reference

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `printer_ip` | ✅ | — | Printer's LAN IP address |
| `serial` | ✅ | — | Printer serial number |
| `access_code_file` | ✅* | — | Path to file containing access code (recommended) |
| `access_code` | ✅* | — | Printer access code inline in config (**deprecated**; migrate with `bambu setup --migrate-access-code`) |

*Either `access_code_file` or `access_code` is required, but inline `access_code` is deprecated and will be removed in a future release. See the packaged [bambu_cli/README.md](bambu_cli/README.md) for the full key reference (`cert_fingerprint`, `orca_slicer`, `profiles_dir`, etc.).

## License

MIT — Use freely, modify as needed.
