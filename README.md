<div align="center">

# platecli

### Print from your terminal — no cloud required

[![CI](https://github.com/DLANSAMA/platecli/actions/workflows/ci.yml/badge.svg)](https://github.com/DLANSAMA/platecli/actions/workflows/ci.yml)
[![Release Packaging](https://github.com/DLANSAMA/platecli/actions/workflows/release.yml/badge.svg)](https://github.com/DLANSAMA/platecli/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/platecli)](https://pypi.org/project/platecli/)
[![Python versions](https://img.shields.io/pypi/pyversions/platecli)](https://pypi.org/project/platecli/)
[![Downloads](https://static.pepy.tech/badge/platecli)](https://pepy.tech/projects/platecli)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Install](#install) · [Try it in 30 seconds](#try-it-in-30-seconds) · [Print something](#print-something) · [User guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md) · [Troubleshooting](https://github.com/DLANSAMA/platecli/blob/main/docs/troubleshooting.md) · [For AI agents](#built-for-ai-agents)

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

**Supports:** any Bambu Lab printer with LAN mode — P1P, P1S, X1C, X1E, A1, A1 Mini. **Hardware-tested on the P1 series (P1P/P1S) only.** The rest speak the same LAN protocols and are expected to work, but are unverified on real hardware — treat them as best-effort and please [open an issue](https://github.com/DLANSAMA/platecli/issues) with what you hit. One caveat: `plate snapshot` grabs the camera directly (no extra software) on P1/A1-class printers. X1-series cameras need a locally-running Docker streamer, and that path is opt-in (`camera_allow_streamer` or `--allow-camera-streamer`) because the streamer does not honour `cert_fingerprint`.

## Install

**Requirements:** Python 3.10+, and [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/releases) installed locally if you want to slice. `plate slice` and `plate job` shell out to the OrcaSlicer binary; `download`, `status`, `upload`, and `print` do not need it. `plate setup` auto-detects the usual install locations (macOS app bundle, Windows Program Files, and on Linux a `$PATH` binary, Flatpak export, or AppImage), and `plate preflight` (or `plate config validate`) tells you if it can't find one.

Fastest way to get OrcaSlicer, if you don't have it:

```bash
winget install --id SoftFever.OrcaSlicer          # Windows
brew install --cask orcaslicer                    # macOS
flatpak install -y flathub com.orcaslicer.OrcaSlicer   # Linux
```

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

<sub>Timestamps and log-level prefixes trimmed for brevity.</sub>

## Print something

Enable LAN mode on your printer, grab the IP, serial, and access code from its touchscreen, then let the interactive setup walk you through the rest:

```bash
plate setup
plate doctor    # optional: verify the connection end to end
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-light.gif">
  <img alt="plate doctor: config, MQTT, and FTPS health checks with TLS-pin verification against a real printer" src="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
</picture>

Now go from a link on the internet to plastic on the bed:

```bash
plate job "https://www.printables.com/model/3161-3d-benchy" --confirm
```

`--confirm` is required for anything that moves the printer or destroys data on it: `print`, `stop`, `pause`, `resume`, `gcode`, and `delete`. Leave it off and the command refuses with exit code `5` — nothing on the printer moves. For `job` / `send`, omitting `--confirm` still runs the download → slice → upload pipeline and exits `0` with `"status": "uploaded_not_printed"`; only the print step is withheld. (`light` is exempt; an LED is not a physical action.)

### Prefer a guided walk-through?

If you'd rather not think about flags, run the wizard — or just type `plate` on its own:

```bash
plate go     # or: plate go "https://www.printables.com/model/3161-3d-benchy"
plate        # bare `plate` on a terminal launches the same wizard
```

It walks you from a model URL (or local file) to a running print without touching a slicer: paste a source, confirm the printer, pick a material and quality preset, answer one supports question, then see a time and filament preview before a final confirm. If your printer has an AMS, the material step defaults to whatever filament is loaded. It drives the same `download` → `slice` → `job` pipeline as `plate job`, so the result is identical — it just asks the questions for you. `plate go` needs an interactive terminal; for scripts and agents, use `plate job <url> --confirm`.

### Watch the printer while it works

```bash
pip install 'platecli[tui]'          # or: pipx install 'platecli[tui]'
                                     # or: uv tool install 'platecli[tui]'
plate tui    # or: plate tui --sim to explore it without a printer
```

<sub>Install the extra the same way you installed `plate` — a `pip install` into your
shell's Python does not reach a `pipx` / `uv tool` environment. Already installed
without it? `pipx install --force 'platecli[tui]'` (or `pipx inject platecli textual`).</sub>

`plate tui` is a live view of your printer: state, temperatures, layer and progress, and the AMS trays, on one screen that keeps updating — plus a job monitor that follows a running print to completion. You can start a print from it too, through the same prepare-and-confirm flow the wizard uses, so you never have to leave the screen.

It is a front-end, not new machinery: it slices and builds the `job` request through the same shared code `plate go` runs, so the two cannot drift. Every safety rule holds — a print only ever starts from the confirm dialog, cancelling keeps the sliced file, and leaving the monitor never stops a print. Textual is an optional extra and never a runtime dependency, so `plate go` keeps working with nothing extra installed on SSH, dumb terminals, and with screen readers.

<p align="center">
  <img alt="plate tui: a live printer dashboard — state, temperatures, progress and AMS trays — and the two-column prepare screen it starts prints from" src="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/tui.gif">
</p>

## Why platecli

- **One command, whole pipeline** — `plate job <url>` downloads, slices, uploads, and prints in one shot; or run `download` / `slice` / `upload` / `print` individually.
- **Fully local & private** — talks straight to the printer over your LAN; no Bambu cloud account, ever.
- **Deliberate-action gate** — physical commands refuse without `--confirm` (exit `5`), so a typo, a truncated argument list, or a replayed read-only command can't start a print. It is a gate against *accidents*, not an authorization boundary: `plate` cannot tell your `--confirm` from an agent's, so anything you let run `plate` can pass the flag. Sandbox agents accordingly.
- **AI-agent ready** — every command speaks `--json` with published schemas, plus a `--sim` mode for hardware-free automation.
- **Watch it live** — `plate status --monitor` follows a print with a live progress bar until it finishes, or run the full-screen `plate tui` (optional `[tui]` extra) for a dashboard you can also start a print from.
- **Fixes itself findable** — `plate doctor` checks network, FTPS, and MQTT health and tells you exactly what's wrong.
- **Hardened where it counts** — TLS certificate pinning, SSRF-guarded downloads, and size-capped ZIP extraction.

## How it compares

The cloud-free Bambu ecosystem is in good shape, and for many people one of
these is the better answer:

| Project | What it is | Reach for it when |
|---|---|---|
| [Bambuddy](https://github.com/maziggy/bambuddy) (~2.6k ★) | Self-hosted web command center, from a single printer up to a print farm | You want a polished dashboard, a print farm, or a full cloud replacement |
| [ha-bambulab](https://github.com/greghesp/ha-bambulab) (~2.3k ★) | The Home Assistant integration — sensors, cameras, automations | Your printer should be part of your smart home |
| [bambulabs_api](https://github.com/BambuTools/bambulabs_api) (~320 ★) / [pybambu](https://github.com/greghesp/pybambu) (~60 ★) | Maintained Python libraries for the printer protocols | You're writing your own application, not running a tool |
| [davglass/bambu-cli](https://github.com/davglass/bambu-cli) (~95 ★, archived) | A Node.js CLI with a rich command set (unrelated project, same name) | You live in Node and are happy with a project that is no longer maintained |
| **platecli** | This — a Python CLI for the whole pipeline | You want one command from a model link to a finished print, scriptable and cloud-free |

platecli's own emphasis is the end-to-end pipeline as a single command:
`plate job <url> --confirm` runs download → slice (OrcaSlicer) → upload → print,
with no account, no daemon, and no web UI. Every command speaks `--json` against
[published JSON Schemas](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/),
and `--sim` gives you a complete fake printer so scripts and AI agents can be
developed and tested with no hardware at all. If you want a dashboard or a
smart-home surface, the projects above are the better fit; if you want a pipeline
you can put in a shell script or hand to an agent, use this.

## Built for AI agents

Every command emits machine-readable `--json` output backed by published [JSON Schemas](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/), `--sim` provides a canned printer (not a protocol test) for development without hardware, and the `--confirm` gate means physical actions never happen by accident. Two commands are deliberately human-only — the `go` wizard and the `tui` full-screen UI refuse `--json` and a non-TTY stdin with exit `5`; `plate job <url> --confirm` is the machine path that does the same work. See the [user guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md) and [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md) for the JSON contracts and stability policy.

## Documentation

- **[User guide](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md)** — full setup, config reference, slicing & AMS mapping, print monitoring, and every flag
- **[Troubleshooting](https://github.com/DLANSAMA/platecli/blob/main/docs/troubleshooting.md)** — keyed by the error message you actually saw: access codes, LAN mode, cert pins, FTPS, OrcaSlicer, camera
- [AGENTS.md](https://github.com/DLANSAMA/platecli/blob/main/AGENTS.md) — architecture and safety notes for agents and automation
- [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md) — JSON contracts + stability policy
- [docs/schemas/](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/) — machine-checkable JSON Schema files
- [SECURITY.md](https://github.com/DLANSAMA/platecli/blob/main/SECURITY.md) — threat model, reporting, known limitations
- [CHANGELOG.md](https://github.com/DLANSAMA/platecli/blob/main/CHANGELOG.md) — release notes
- [CONTRIBUTING.md](https://github.com/DLANSAMA/platecli/blob/main/CONTRIBUTING.md) — dev setup, tests, releases
- [Discussions](https://github.com/DLANSAMA/platecli/discussions) — questions, show-and-tell, and community conversation

## Before you print unattended

`plate` can start a print with nobody standing at the machine, which is exactly the point — and exactly the risk. An FDM printer is a hot, moving appliance: a failed print can jam, spaghetti, damage the hotend, or in rare cases start a fire. Keep the printer in view of a person or a camera, don't kick off long jobs overnight or in an empty house, and leave your printer's own firmware safety features on. `plate` uploads a job and starts it; it does not watch the plate for failures and will not stop a print that is going wrong. What the machine does is your responsibility.

## Support & expectations

platecli is maintained by one person in their spare time. Bug reports and pull requests are genuinely welcome — [open an issue](https://github.com/DLANSAMA/platecli/issues) with your `plate doctor` output attached and I'll get to it when I can. There is no response-time guarantee, and feature requests may sit or be declined to keep the tool small and local-only. If you need something faster than that, fork it — it's MIT.

## Status & disclaimer

**Status:** Beta, pre-1.0 — APIs and config keys follow the stability policy in [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md). The current release is whatever the [PyPI badge](https://pypi.org/project/platecli/) at the top of this page shows; `plate --version` reports the copy you have installed.

> **Disclaimer:** platecli is an unofficial, community-developed tool. It is not affiliated with, endorsed by, or supported by Bambu Lab. "Bambu Lab" and product names are trademarks of their respective owners, used here only to describe compatibility. The printer protocols (MQTT/FTPS) are reverse-engineered; a firmware update may break functionality without warning — run `plate doctor` after printer updates.

## License

MIT — Use freely, modify as needed.

---

<div align="center">
<sub>⭐ If platecli fits your workflow, a star helps other makers find it.</sub>
</div>
