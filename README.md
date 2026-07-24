<div align="center">

# platecli

### Print from your terminal — no cloud required

[![CI](https://github.com/DLANSAMA/platecli/actions/workflows/ci.yml/badge.svg)](https://github.com/DLANSAMA/platecli/actions/workflows/ci.yml)
[![Release Packaging](https://github.com/DLANSAMA/platecli/actions/workflows/release.yml/badge.svg)](https://github.com/DLANSAMA/platecli/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/platecli)](https://pypi.org/project/platecli/)
[![Python versions](https://img.shields.io/pypi/pyversions/platecli)](https://pypi.org/project/platecli/)
[![Downloads](https://static.pepy.tech/badge/platecli)](https://pepy.tech/projects/platecli)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Install](#install) · [Try it in 30 seconds](#try-it-in-30-seconds) · [Print something](#print-something) · [User guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md) · [For AI agents](#built-for-ai-agents)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/demo-dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/demo-light.gif">
  <img alt="platecli demo: live printer status and slicing from the terminal" src="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/demo-dark.gif">
</picture>

Paste a Printables link, get a physical print. `plate` downloads the model, slices it with OrcaSlicer, and sends it to your Bambu Lab printer — one command, entirely on your local network. No cloud account, no telemetry. Runs on **Linux, macOS, and Windows**, driven by hand or by AI agents.

```text
model URL or file  →  download  →  slice (OrcaSlicer)  →  upload  →  print
                        one command:  plate job <url> --confirm
```

**Supports:** P1P, P1S, X1C, X1E, A1, A1 Mini (any Bambu printer with LAN mode)

## Install

```bash
pipx install platecli
# or
uv tool install platecli
# or
pip install platecli
```

<sub>Previously published on PyPI as `bambu-local-cli` (yanked). The project is now `platecli`; the installed command is `plate`.</sub>

## Try it in 30 seconds

No printer needed — simulation mode fakes one so you can kick the tires right away:

```bash
plate --sim status
```

```
🖨️  Bambu Printer Status
   State: IDLE
   Bed: 25°C / 0°C
   Nozzle: 25°C / 0°C
   Fan: 0 | WiFi: -42dBm
   AMS:
     Unit 0 (humidity 5, 26.0°C)
       ▶ Slot 0: PLA #F2F2F2 | 90%
         Slot 1: PETG #0A0AC8 | 60%
         Slot 2: empty
         Slot 3: TPU #000000 | 45%
```

## Print something

Enable LAN mode on your printer, grab the IP, serial, and access code from its touchscreen, then let the interactive setup walk you through the rest:

```bash
plate setup
plate doctor    # optional: verify the connection end to end
```

Now go from a link on the internet to plastic on the bed:

```bash
plate job "https://www.printables.com/model/12345-thing" --confirm
```

`--confirm` is required for anything that physically prints (or stops, deletes, or sends raw G-code) — leave it off and nothing on the printer moves.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-light.gif">
  <img alt="plate doctor: config, MQTT, and FTPS health checks with TLS-pin verification against a real printer" src="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
</picture>

## Why platecli

- **One command, whole pipeline** — `plate job <url>` downloads, slices, uploads, and prints in one shot; or run `download` / `slice` / `upload` / `print` individually.
- **Fully local & private** — talks straight to the printer over your LAN; no Bambu cloud account, ever.
- **Safe by default** — nothing physical happens without `--confirm`, so a typo (or an over-eager AI agent) can't start a print.
- **AI-agent ready** — every command speaks `--json` with published schemas, plus a `--sim` mode for hardware-free automation.
- **Watch it live** — `plate status --monitor` follows a print with a live progress bar until it finishes.
- **Fixes itself findable** — `plate doctor` checks network, FTPS, and MQTT health and tells you exactly what's wrong.
- **Hardened where it counts** — TLS certificate pinning, SSRF-guarded downloads, and size-capped ZIP extraction.

## Built for AI agents

Every command emits machine-readable `--json` output backed by published [JSON Schemas](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/), `--sim` provides a full fake printer for development without hardware, and the `--confirm` gate makes accidental physical actions impossible by default. See the [user guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md) and [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md) for the JSON contracts and stability policy.

## Documentation

- **[User guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md)** — full setup, config reference, slicing & AMS mapping, print monitoring, and every flag
- [AGENTS.md](https://github.com/DLANSAMA/platecli/blob/main/AGENTS.md) — architecture and safety notes for agents and automation
- [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md) — JSON contracts + stability policy
- [docs/schemas/](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/) — machine-checkable JSON Schema files
- [SECURITY.md](https://github.com/DLANSAMA/platecli/blob/main/SECURITY.md) — threat model, reporting, known limitations
- [CHANGELOG.md](https://github.com/DLANSAMA/platecli/blob/main/CHANGELOG.md) — release notes
- [CONTRIBUTING.md](https://github.com/DLANSAMA/platecli/blob/main/CONTRIBUTING.md) — dev setup, tests, releases

## Status & disclaimer

**Status:** Beta (`0.2.0`). Pre-1.0 — APIs and config keys follow the stability policy in [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md).

> **Disclaimer:** platecli is an unofficial, community-developed tool. It is not affiliated with, endorsed by, or supported by Bambu Lab. "Bambu Lab" and product names are trademarks of their respective owners, used here only to describe compatibility. The printer protocols (MQTT/FTPS) are reverse-engineered; a firmware update may break functionality without warning — run `plate doctor` after printer updates.

## License

MIT — Use freely, modify as needed.

---

<div align="center">
<sub>⭐ If platecli fits your workflow, a star helps other makers find it.</sub>
</div>
