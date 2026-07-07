# bambu-cli — CLI for Bambu Lab Printers

[![CI](https://github.com/DLANSAMA/bambu-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/DLANSAMA/bambu-cli/actions/workflows/ci.yml)
[![Release Packaging](https://github.com/DLANSAMA/bambu-cli/actions/workflows/release.yml/badge.svg)](https://github.com/DLANSAMA/bambu-cli/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Fully local 3D printing pipeline for Bambu Lab printers. Runs on **Linux, macOS, and Windows**. Download models from Printables, slice with OrcaSlicer, and print — all controlled via CLI by any AI agent or by hand. No cloud account needed.

**Supports:** P1P, P1S, X1C, X1E, A1, A1 Mini (any Bambu printer with LAN mode)

> **Disclaimer:** bambu-cli is an unofficial, community-developed tool. It is not affiliated with, endorsed by, or supported by Bambu Lab. "Bambu Lab" and product names are trademarks of their respective owners, used here only to describe compatibility.

## Installation

The examples below use the installed `bambu-cli` command.

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

## Usage

```bash
# Full workflow (download, slice, upload, and start print)
bambu-cli job "https://www.printables.com/model/12345-thing" --confirm --json
```

For programmatic checks, `bambu-cli --json --version` emits JSON version details.

### Global flags

| Flag | Description |
|------|-------------|
| `--json` | Emit JSON for commands that support it; may appear before the subcommand |
| `--max-download-mb` | Cap URL download and ZIP extraction size (default 2048 MB); accepted by `job`, `send`, and `download` |

### Slicing & AMS

`slice` accepts common mesh formats in the precedence order STL > STEP > OBJ > 3MF > G-code. When mapping filaments to AMS slots, mapping arguments take zero-or-positive slot indexes.

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
| `access_code` | ✅* | — | Printer access code inline in config |

*Either `access_code_file` or `access_code` is required. See the packaged [bambu_cli/README.md](bambu_cli/README.md) for the full key reference (`cert_fingerprint`, `orca_slicer`, `profiles_dir`, etc.).

## License

MIT — Use freely, modify as needed.
