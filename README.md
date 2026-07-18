# bambu-cli — CLI for Bambu Lab Printers

[![CI](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/ci.yml)
[![Release Packaging](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/release.yml/badge.svg)](https://github.com/DLANSAMA/bambu-local-cli/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Fully local 3D printing pipeline for Bambu Lab printers. Runs on **Linux, macOS, and Windows**. Download models from Printables, slice with OrcaSlicer, and print — all controlled via CLI by any AI agent or by hand. No cloud account needed.

**Supports:** P1P, P1S, X1C, X1E, A1, A1 Mini (any Bambu printer with LAN mode)

> **Disclaimer:** bambu-cli is an unofficial, community-developed tool. It is not affiliated with, endorsed by, or supported by Bambu Lab. "Bambu Lab" and product names are trademarks of their respective owners, used here only to describe compatibility. The printer protocols (MQTT/FTPS) are reverse-engineered; a firmware update may break functionality without warning — run `bambu-cli doctor` after printer updates.

**Status:** Beta (`0.1.0`). Pre-1.0 — APIs and config keys follow the stability policy in [docs/api.md](docs/api.md).

## Installation

The examples below use the installed `bambu-cli` command.

```bash
pip install bambu-local-cli
```

> Note: the package is published on PyPI as `bambu-local-cli` (the `bambu-cli` name there belongs to an unrelated project). The installed command is still `bambu-cli`.

Or from source:

```bash
pip install .
# or: uv sync
```

## Features

- **Jobs & URL support** — Use `job` when an agent or user gives either a website URL or a local file path. It handles everything in one shot.
- **Safe extraction** — ZIP archives containing model files are fully supported. Existing files are kept safe by creating a numbered sibling such as `model-1.stl`. URL downloads and ZIP extraction have a 2048 MB safety limit, adjustable via `--max-download-mb`.
- **Modularity** — Run steps individually using `download`, `slice`, `upload`, or `print`.
- **Safety first** — One-shot and print flows will not start a physical print unless `--confirm` is present. Stop, delete, and raw gcode also require `--confirm`.
- **TLS pinning** — Pin the printer’s self-signed cert with `cert_fingerprint` (setup/doctor can capture it). Prefer this over `insecure_tls`.
- **SSRF-hardened downloads** — Private/loopback targets are refused unless you pass `--allow-private-ips` for that invocation.
- **Diagnostics** — Network, FTPS, and MQTT health checking with `doctor` and `preflight`.
- **Agent JSON** — Structured `--json` output with published schemas under `docs/schemas/`.

## Setup

Use the interactive `setup` command to create your config securely:

```bash
bambu-cli setup
```

Inspect or check the resulting config at any time:

```bash
bambu-cli config show       # print config path + contents (access code redacted)
bambu-cli config validate   # check config values without contacting the printer
bambu-cli doctor            # connectivity + live cert fingerprint
```

### OrcaSlicer

Slicing shells out to OrcaSlicer, so `bambu-cli` needs to know where its binary
and bundled `profiles/BBL` directory live. Setup auto-detects the usual install
locations per platform — the macOS app bundle, the Windows `Program Files`
install, and on Linux a `$PATH` binary (`orca-slicer`), a Flatpak export, or an
AppImage under `~/Applications` or `~/tools`. If detection misses your install,
set `orca_slicer` and `profiles_dir` in `config.json`; when a configured path is
wrong, `config validate` (and slicing) will point you at a working OrcaSlicer it
found instead of failing with a generic error.

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
exercise the exact event shape without a printer. Schema: [`docs/schemas/status_event.json`](docs/schemas/status_event.json).

### Global flags

| Flag | Description |
|------|-------------|
| `--json` | Emit JSON for commands that support it; may appear before or after the subcommand |
| `--sim` | Simulation mode (no real printer) |
| `--max-download-mb` | Cap URL download and ZIP extraction size (default 2048 MB); accepted by `job`, `send`, and `download` |
| `--allow-private-ips` | Allow downloads that resolve to private/loopback addresses (default: deny). CLI-only, not sticky config |
| `--network-timeout` / `--slicer-timeout` / `--command-timeout` / `--upload-timeout` | Bound long operations (see [docs/api.md](docs/api.md)) |

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

## Config reference

Config file location is platform-standard under the user config directory
(e.g. `~/.config/bambu/config.json` on Linux). Create/edit via `bambu-cli setup`
or manually.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `printer_ip` | ✅ | — | Printer's LAN IP address |
| `serial` | ✅ | — | Printer serial number |
| `access_code_file` | ✅* | — | Path to file containing access code (**recommended**) |
| `access_code` | ✅* | — | Inline access code (**deprecated**; migrate with `bambu-cli setup --migrate-access-code`) |
| `cert_fingerprint` | recommended | — | SHA-256 of the printer TLS cert (no separators or colon form both accepted) |
| `insecure_tls` | no | `false` | Disable TLS verification (last resort; CLI warns when true) |
| `username` | no | `bblp` | MQTT username |
| `mqtt_port` | no | `8883` | MQTTS port |
| `model` / `printer_model` | no | `P1P` | Printer model token for slicing |
| `nozzle` / `nozzle_size` | no | `0.4` | Nozzle diameter string |
| `orca_slicer` | for slice | auto-detect | Path to OrcaSlicer binary |
| `profiles_dir` | for slice | auto-detect | Path to OrcaSlicer `profiles/BBL` directory |
| `camera_image` | no | `bambu_p1_streamer` | Docker image for X1-style streamer fallback |
| `camera_container_name` | no | `bambu_camera` | Docker container name |
| `camera_port` | no | `127.0.0.1:1985:1984` | Docker publish mapping; loopback-only by default. Set to `0.0.0.0:1985:1984` to expose on the LAN (see [SECURITY.md](SECURITY.md)) |
| `camera_stream_url` | no | derived | Must be localhost if set; used for Docker frame fetch |
| Timeouts | no | package defaults | Optional `network_timeout`, `slicer_timeout`, `command_timeout`, `upload_timeout` (seconds) |

\* Either `access_code_file` or `access_code` is required. Inline `access_code` is deprecated and will be removed in a future release.

`allow_private_ips` is **not** a config key — use the CLI flag `--allow-private-ips` per invocation.

## Project layout

- `bambu_cli/` — Runtime package used by the installed command (`bambu-cli`).
- `scripts/bambu.py` — Compatibility wrapper for direct script usage without installing.
- `tests/` — Unit, contract, security-marker, and smoke tests.
- `docs/` — API, schemas, quality roadmap, test backlog, mutation baseline, live smoke.

## Documentation

### Ships with the PyPI sdist (and on GitHub)

| Doc | Audience |
|-----|----------|
| [AGENTS.md](AGENTS.md) | Agents and automation (architecture, safety) |
| [docs/api.md](docs/api.md) | JSON contracts + stability policy |
| [docs/schemas/](docs/schemas/) | Machine-checkable JSON Schema files |
| [SECURITY.md](SECURITY.md) | Threat model, reporting, known limitations |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

Wheels contain **runtime code only** (no docs).

### GitHub / contributor only (not in PyPI packages)

| Doc | Audience |
|-----|----------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, tests, releases |
| [docs/quality-roadmap.md](docs/quality-roadmap.md) | Quality scoreboard and phased plan |
| [docs/test-backlog.md](docs/test-backlog.md) | Remaining test / coverage gaps |
| [docs/live-printer-smoke.md](docs/live-printer-smoke.md) | Opt-in real-printer harness |
| [docs/mutation-baseline.md](docs/mutation-baseline.md) | Mutation testing scope and floor |

## License

MIT — Use freely, modify as needed.
