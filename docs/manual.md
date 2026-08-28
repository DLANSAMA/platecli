# platecli user guide

The complete reference for `plate` — setup, configuration, slicing, monitoring, and every flag. If you're new here, start with the [README](https://github.com/DLANSAMA/platecli/blob/main/README.md) for installation and a quick tour.

**Contents**

- [Your first print](#your-first-print)
- [Setup](#setup)
- [OrcaSlicer](#orcaslicer)
- [Usage](#usage)
- [Guided mode (plate go)](#guided-mode-plate-go)
- [Full-screen mode (plate tui)](#full-screen-mode-plate-tui)
- [Monitoring a print](#monitoring-a-print)
- [Camera snapshots](#camera-snapshots)
- [Global flags](#global-flags)
- [Slicing & AMS](#slicing--ams)
- [Config reference](#config-reference)
- [Use with AI agents](#use-with-ai-agents)
- [Features in depth](#features-in-depth)
- [Installing from source](#installing-from-source)
- [Troubleshooting](https://github.com/DLANSAMA/platecli/blob/main/docs/troubleshooting.md)
- [Support matrix](#support-matrix)
- [Project layout](#project-layout)
- [Documentation map](#documentation-map)

## Your first print

Four steps, in order. None of them needs a slicer setting or a flag.

1. **Install OrcaSlicer.** `plate` slices with it, and cannot slice without it.
   It is a *second* slicer app, separate from Bambu Studio — see
   [OrcaSlicer](#orcaslicer) for the one-line install on each platform.
2. **Turn on LAN mode** on the printer's touchscreen and note the three values
   shown next to it: the **IP address**, the **serial number**, and the
   **LAN access code**. The access code is *not* your Bambu account password,
   and it changes whenever LAN mode is toggled or the printer is factory-reset.
3. **Run `plate setup`.** It asks for those three values and auto-detects
   OrcaSlicer. `plate doctor` afterwards checks the connection end to end
   (see [Setup](#setup)).
4. **Run `plate go`** and paste a model link or a local file. It asks for a
   material and a quality, shows the time and filament estimate, and starts
   the print only after you say yes. Plain `plate` in a terminal opens the same
   wizard (see [Guided mode](#guided-mode-plate-go)).

No printer within reach yet? `plate --sim status` fakes one so you can look
around, and `plate preflight` checks the local pieces (Python, OrcaSlicer,
profiles) without touching a printer.

Writing a script or driving `plate` from an agent instead? Use `plate job`
— see [Use with AI agents](#use-with-ai-agents) for the flags and for what
`--confirm` does and does not gate.

## Setup

Before running `setup`, turn on LAN mode on the printer and gather the three
values its touchscreen shows under network/LAN settings: the LAN IP address,
the serial number, and the LAN access code. The access code is **not** your
Bambu account password, and it is regenerated whenever LAN mode is toggled or
the printer is factory-reset — if `plate` suddenly cannot connect, read it off
the screen again and re-run `setup`.

Use the interactive `setup` command to create your config securely (it also
auto-detects [OrcaSlicer](#orcaslicer), which must be installed first if you
want to slice):

```bash
plate setup
```

Inspect or check the resulting config at any time:

```bash
plate config show       # print config path + contents (access code redacted)
plate config validate   # check config values without contacting the printer
plate doctor            # connectivity + cert-pin check (add -v for the LAN IP and full fingerprint)
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-light.gif">
  <img alt="plate doctor: config, MQTT, and FTPS health checks with TLS-pin verification against a real printer" src="https://raw.githubusercontent.com/DLANSAMA/platecli/main/docs/doctor-dark.gif">
</picture>

## OrcaSlicer

Slicing shells out to OrcaSlicer, so `plate` needs two paths: the **binary** and
the bundled **`resources/profiles/BBL`** directory. `plate setup` auto-detects
the usual locations; this section covers installing it and overriding the paths
when detection misses.

Check what was detected at any time:

```bash
plate preflight     # emits explicit `orca-slicer` and `profiles-dir` checks
```

### Install

**Linux — distro package (simplest, if your distro has it)**

```bash
sudo pacman -S orca-slicer          # Arch
# Fedora/Debian: no official package; use Flatpak or the AppImage below
```

**Linux — Flatpak (recommended)**

```bash
flatpak install flathub com.orcaslicer.OrcaSlicer
flatpak run com.orcaslicer.OrcaSlicer   # first run, to let it unpack resources
```

> Auto-detection knows both the current Flathub app id
> (`com.orcaslicer.OrcaSlicer`) and the legacy one (`io.github.softfever.OrcaSlicer`),
> so a standard Flathub install should be picked up automatically. Manual paths are
> only needed for a non-standard install location — see
> [Overriding the paths](#overriding-the-paths).
>
> **Note:** The Flatpak profile-directory paths are inferred and have not been
> verified against a real Flathub install. If `plate preflight` reports profiles
> not found after a Flatpak install, set `profiles_dir` manually:
>
> ```bash
> flatpak info --show-location com.orcaslicer.OrcaSlicer
> # then look under <location>/files/share/OrcaSlicer/resources/profiles/BBL
> plate setup --profiles-dir <path>
> ```

**Linux — AppImage**

Download the AppImage from the [OrcaSlicer releases page](https://github.com/OrcaSlicer/OrcaSlicer/releases),
then extract it so the `profiles/BBL` directory is reachable (an un-extracted
AppImage hides its resources):

```bash
mkdir -p ~/tools && cd ~/tools
mv ~/Downloads/OrcaSlicer_Linux_*.AppImage OrcaSlicer.AppImage
chmod +x OrcaSlicer.AppImage
./OrcaSlicer.AppImage --appimage-extract    # creates ~/tools/squashfs-root/
```

`~/tools/OrcaSlicer.AppImage` and `~/tools/squashfs-root/resources/profiles/BBL`
are both on the auto-detection list, so this layout needs no config.

Also install `xvfb` on a headless Linux machine — OrcaSlicer needs a display
even when slicing from the command line (`preflight` warns if `xvfb-run` is
missing).

**macOS**

Install OrcaSlicer from its `.dmg` into `/Applications` (or `~/Applications`),
both of which are auto-detected.

**Windows**

Run the installer from the OrcaSlicer releases page; the default
`Program Files` and per-user `Programs` locations are both auto-detected.

### Where plate looks by default

Best-match-first, exactly as auto-detection tries them:

| Platform | Binary | `profiles/BBL` |
|---|---|---|
| Linux | `orca-slicer` / `OrcaSlicer` / `orcaslicer` on `$PATH`, then `/usr/bin/orca-slicer`, `/usr/local/bin/orca-slicer`, `/opt/OrcaSlicer/orca-slicer`, the Flatpak exports (`{/var/lib,~/.local/share}/flatpak/exports/bin/<app-id>` for **both** app ids — current `com.orcaslicer.OrcaSlicer` first, legacy `io.github.softfever.OrcaSlicer` after), `~/Applications/OrcaSlicer.AppImage`, `~/tools/OrcaSlicer.AppImage` | `/usr/share/OrcaSlicer/resources/profiles/BBL`, `/opt/OrcaSlicer/resources/profiles/BBL`, the Flatpak app trees (`{/var/lib,~/.local/share}/flatpak/app/<app-id>/current/active/files/share/OrcaSlicer/resources/profiles/BBL`, both app ids — these are inferred, see the note above), `~/tools/squashfs-root/resources/profiles/BBL` |
| macOS | `/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer`, `~/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer` | the matching `.../Contents/Resources/profiles/BBL` |
| Windows | Each of the three directories is probed for `orca-slicer.exe` first (current installer), then `OrcaSlicer.exe` (older builds): `%PROGRAMFILES%\OrcaSlicer\`, `%LOCALAPPDATA%\Programs\OrcaSlicer\`, `%PROGRAMFILES(X86)%\OrcaSlicer\` | the matching `...\OrcaSlicer\resources\profiles\BBL` |

A checkout-relative `../tools/OrcaSlicer.AppImage` (and its `squashfs-root`
profiles dir) is also probed, for running from a source tree.

### Overriding the paths

The supported way is `plate setup` — it accepts both as flags and writes them to
`config.json`:

```bash
plate setup \
  --orca-slicer /var/lib/flatpak/exports/bin/com.orcaslicer.OrcaSlicer \
  --profiles-dir ~/tools/squashfs-root/resources/profiles/BBL
```

Or edit `config.json` directly and set the `orca_slicer` and `profiles_dir`
keys (see [Config reference](#config-reference); there is no `plate config set`
— `plate config` is `show`/`validate` only).

When a configured path is wrong, `preflight` and `config validate` search for a
working install and tell you which path to use instead of failing with a generic
error. Verify with `plate preflight`.

If slicing still fails, see
[Troubleshooting](https://github.com/DLANSAMA/platecli/blob/main/docs/troubleshooting.md#orcaslicer-or-its-bbl-profiles-were-not-found).

## Usage

```bash
# Guided: paste a link, answer a few questions, print (asks before starting)
plate go
plate go "https://www.printables.com/model/3161-3d-benchy"

# Read-only: check connectivity and printer state (safe, no printer state changes)
plate status
plate doctor
```

For programmatic checks, `plate --json --version` emits JSON version details.

```bash
# Scripted full workflow: download, slice, upload, and START A PHYSICAL PRINT.
# --confirm is what starts the print; without it job/send still download,
# slice, and upload, then exit 0 with "status": "uploaded_not_printed".
plate job "https://www.printables.com/model/3161-3d-benchy" --confirm --json
```

## Guided mode (plate go)

`plate go` is an interactive wizard for printing from a URL without learning any slicer settings — OrcaSlicer still does the slicing underneath, so it must be installed. It is a front-end over the same pipeline as `plate job` — it collects your answers, builds the same request `job` would, and drives `download` → `slice` → `job` — so the result is identical; it just asks the questions instead of taking flags.

```bash
plate go                                                   # prompts for everything
plate go "https://www.printables.com/model/3161-3d-benchy" # skips the first prompt
```

Bare `plate` (no subcommand) on an interactive terminal launches the same wizard. This is a convenience for people who just installed `plate` and typed it to see what happens; it only triggers when **both** stdin and stdout are TTYs and `--json` was not passed. In every non-interactive context — CI, pipes, `subprocess`, `plate | less`, or with `--json` — bare `plate` keeps its previous behavior: print help to stderr and exit `5`.

The wizard walks these steps:

1. **Source** — paste a Printables page, a direct STL/3MF/ZIP link, or a local file path (re-prompts on a bad URL, up to three tries).
2. **Printer** — shows the configured printer and asks you to confirm it. If no printer is configured yet, it offers to run `plate setup` for you first.
3. **Material** — `PLA` / `PETG` / `ABS` / `TPU`. Each preset carries the correct nozzle and bed temperatures. If your printer has an AMS, the currently-loaded filament is detected and offered as the default (marked *(detected in AMS)*); if that read fails for any reason, it falls back to `PLA` without slowing you down.
4. **Quality** — `draft` / `standard` / `fine` (mapping to OrcaSlicer's 0.28 / 0.20 / 0.12 mm profiles).
5. **Supports** — one yes/no question for models with big overhangs.
6. **Preview** — it downloads and slices, then shows an estimated print time and filament weight *before* anything is sent.
7. **Confirm** — a default-**No** gate. Answering Yes is the deliberate-action equivalent of `job --confirm` and starts the print. Declining offers an upload-only path; declining that keeps the sliced file rather than deleting it.

The wizard never exposes raw slicer knobs (infill, walls, speeds, seams, `--set` overrides). For that level of control, or for scripts and AI agents, use `plate job <url> --confirm` directly — `plate go` deliberately requires an interactive terminal and `plate go --json` always errors with exit `5` (interactive mode has no machine contract).

## Full-screen mode (plate tui)

`plate tui` is a live view of your printer — state, temperatures, layer and progress, and the AMS trays, on one screen that keeps updating — with a job monitor that follows a running print to completion. Starting a print is built in too, through the same prepare-and-confirm flow the wizard uses, so you never have to leave the screen to do it. It is a *front-end*, not new machinery: it validates sources, reads the AMS, slices, and builds the `job` request through exactly the same shared code `plate go` uses, so the two cannot drift apart.

It needs the optional TUI extra (it is not a runtime dependency):

```bash
pip install 'platecli[tui]'   # or: uv tool install 'platecli[tui]'
plate tui
plate tui --sim               # explore every screen with a simulated printer
```

Without the extra, `plate tui` exits `1` (config error) with the install command. Like `plate go` it is interactive-only: `plate tui --json` and a non-TTY stdin both exit `5` with the standard error envelope (there is no machine contract — agents use `plate job`).

### Screens

| Screen | What it does |
|--------|--------------|
| **Dashboard** | Live printer state, temperatures, layer/progress, and the AMS trays (active tray highlighted). Refreshes on `r` and every 10 s while it is the active screen. An unreachable printer renders inline — the app never crashes on it. |
| **Prepare** (`n`) | Source box (URL or local path, validated as you submit), material and quality presets with guidance, and a supports checkbox. The AMS-detected filament is pre-selected and tagged *(detected in AMS)* — unless you already picked something, in which case a slow AMS read never overrides you. "Prepare" downloads and slices in the background and shows the same preview the wizard prints (model, printer, material line, time/filament estimate). If the printer or OrcaSlicer is not configured, this screen is replaced by a message pointing at `plate setup` — the TUI never embeds setup. |
| **Settings** (`s` from prepare) | Advanced slice settings — the named `slice` flags grouped by Quality / Strength / Supports / Adhesion / Filament / Speed / Plate. Flags with a fixed set of options are dropdowns. Below the form, **Add an override** takes any other setting as a name, a bucket (*process* or *filament*, matching `--set` / `--set-filament`) and a value — run `plate slice --list-settings` to see what your profiles accept. Pending overrides are listed; click one to edit it, or remove it. A blank field keeps the profile default, exactly like leaving a flag off. Unsafe values are refused inline by the same checks the CLI runs. Disabled for pre-sliced sources, which are printed as-is. |
| **Confirm** | Start print / Upload only / Cancel. |
| **Monitor** (`m`) | Follows a running job — percent, layer, remaining time — until it reaches a terminal state (`FINISH` / `FAILED` / `STOP` / `IDLE`, the same set `plate status --monitor` uses). |
| **Help** (`?` or `F1`) | The key reference, always available. |

### Keys

| Key | Action |
|-----|--------|
| `?` / `F1` | Help overlay (`F1` also works while typing in the source box) |
| `r` | Refresh the dashboard now |
| `n` | New print (prepare flow) |
| `m` | Monitor the running job |
| `s` | Advanced slice settings (from the prepare screen) |
| `Esc` | Back — from prepare, from the confirm dialog, or from the monitor |
| `q` / `Ctrl-Q` | Quit (refused while an upload or print-start is in flight) |

Each screen's footer lists only the keys that do something there; the help overlay lists all of them.

### Safety model

The TUI keeps every guarantee the CLI makes:

- **A print starts only from the confirm dialog.** That dialog is the single place in the TUI that sets `confirm=True`; nothing else in the app can start a print.
- **Upload only** uploads the sliced file and leaves it unstarted — the same as `plate job` without `--confirm`.
- **Cancel keeps your work.** The sliced file is moved out of the temp directory and the app tells you where it is, exactly like the wizard's "Nothing sent. Sliced file kept at …".
- **Leaving the monitor is not stopping the print.** `Esc` stops watching; it never sends a stop or pause command.
- **Advanced settings cannot exceed CLI limits.** Every override goes through the same `slice` validation, so an unsafe temperature is refused in the form rather than sent to the printer. The **Applies to** dropdown is the routing, exactly as `--set` vs `--set-filament` is on the command line: a filament setting such as `filament_flow_ratio` sent as a process override is accepted by OrcaSlicer and then *silently ignored*, so the slice comes out unchanged. The picker starts every new key at *process* (the bucket a bare `--set` uses) rather than carrying your last choice over, so a process setting is never sent as a filament override by accident either.
- Quitting is refused while an upload or print-start is still running, so a physical action is never abandoned half-way.

### Where it runs

Textual is an optional extra, never a runtime dependency, so `plate go` keeps
working with nothing extra installed — on a dumb terminal, over a slow SSH link,
and with a screen reader, where a full-screen redraw is a liability. Both drive
the same pipeline, so nothing is out of reach either way. Scripts and AI agents
should use neither: `plate job <url> --confirm` is the machine path.

## Monitoring a print

`plate status --monitor` (alias `--wait`) follows a print until it reaches a
terminal state (`FINISH`, `FAILED`, `STOP`, or `IDLE`). For a human it renders a
live progress bar; for an agent, add `--json` to stream **newline-delimited
JSON** (NDJSON) — one compact object per change as the print advances:

```bash
plate status --monitor --json
```

```json
{"event":"update","command":"status","gcode_state":"RUNNING","mc_percent":42,"layer_num":50,"total_layer_num":200,"mc_remaining_time":33,"nozzle_temper":220,"nozzle_target_temper":220,"bed_temper":60,"bed_target_temper":60,"gcode_file":"model.gcode"}
{"event":"terminal","command":"status","gcode_state":"FINISH","mc_percent":100,"layer_num":200,"total_layer_num":200,"mc_remaining_time":0,"nozzle_temper":38,"nozzle_target_temper":0,"bed_temper":31,"bed_target_temper":0,"gcode_file":"model.gcode"}
```

Each line is a self-contained JSON object, so an agent can consume the stream
incrementally and stop once it sees `"event":"terminal"`. Pair with `--sim` to
exercise the exact event shape without a printer. Schema: [`docs/schemas/status_event.json`](https://github.com/DLANSAMA/platecli/blob/main/docs/schemas/status_event.json).

## Camera snapshots

`plate snapshot` captures a JPEG image from the printer camera and saves it locally.

```bash
plate snapshot                          # saves printer_snapshot.jpg
plate snapshot --output my_photo.jpg    # explicit output path
plate snapshot --unique                 # saves printer_snapshot_20260724T191530Z.jpg
plate snapshot --unique --output cam.jpg  # saves cam_20260724T191530Z.jpg
plate snapshot --json                   # machine-readable result
```

Every `--json` response includes `captured_at` (ISO-8601 UTC timestamp) and `sha256` (hex digest of the JPEG bytes). Agents should compare these fields across captures to confirm a fresh frame was received before sending the image to a user. Use `--unique` when taking repeated snapshots — it inserts a UTC timestamp into the filename so successive captures never silently overwrite each other.

P1/A1-class printers are captured directly over TLS port 6000 — no extra software. X1-series cameras are RTSP and need the Docker streamer, which is **opt-in** (`camera_allow_streamer` or `--allow-camera-streamer`) because it does not honour `cert_fingerprint`. A failed direct grab aborts unless you opt in. `camera_port` defaults to loopback-only (`127.0.0.1:1985:1984`); set it to `0.0.0.0:1985:1984` only if you deliberately want the unauthenticated feed on the LAN.

## Global flags

| Flag | Description |
|------|-------------|
| `--json` | Emit JSON for commands that support it; may appear before or after the subcommand |
| `--sim` | Simulation mode (no real printer) |
| `--max-download-mb` | Cap URL download and ZIP extraction size (default 2048 MB); accepted by `job`, `send`, and `download` |
| `--allow-private-ips` | Allow downloads that resolve to private/loopback addresses (default: deny). CLI-only, not sticky config |
| `--network-timeout` / `--slicer-timeout` / `--command-timeout` / `--upload-timeout` | Bound long operations (see [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md)) |

## Slicing & AMS

`slice` accepts common mesh formats in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. When mapping filaments to AMS slots, mapping arguments take zero-or-positive slot indexes.

To decide that mapping, read what is actually loaded first: `plate status`
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
(e.g. `~/.config/bambu/config.json` on Linux). Create/edit via `plate setup`
or manually.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `printer_ip` | ✅ | — | Printer's LAN IP address |
| `serial` | ✅ | — | Printer serial number |
| `access_code_file` | ✅* | — | Path to file containing access code (**recommended**) |
| `access_code` | ✅* | — | Inline access code (**deprecated**; migrate with `plate setup --migrate-access-code`) |
| `cert_fingerprint` | recommended | — | SHA-256 of the printer TLS cert (no separators or colon form both accepted) |
| `insecure_tls` | no | `false` | Disable TLS verification (last resort; CLI warns when true) |
| `username` | no | `bblp` | MQTT username |
| `mqtt_port` | no | `8883` | MQTTS port |
| `model` / `printer_model` | no | `P1P` | Printer model token for slicing |
| `nozzle` / `nozzle_size` | no | `0.4` | Nozzle diameter string |
| `orca_slicer` | for slice | auto-detect | Path to OrcaSlicer binary |
| `profiles_dir` | for slice | auto-detect | Path to OrcaSlicer `profiles/BBL` directory |
| `camera_image` | no | `bambu_p1_streamer` | Docker image for the opt-in X1-style streamer |
| `camera_container_name` | no | `bambu_camera` | Docker container name |
| `camera_port` | no | `127.0.0.1:1985:1984` | Docker publish mapping; loopback-only by default. Set to `0.0.0.0:1985:1984` to expose on the LAN (see [SECURITY.md](https://github.com/DLANSAMA/platecli/blob/main/SECURITY.md)) |
| `camera_stream_url` | no | derived | Must be localhost if set; used for Docker frame fetch |
| `camera_allow_streamer` | no | `false` | Opt in to the unpinned Docker/RTSP streamer when the direct grab fails. Required for X1-series. Also available as `--allow-camera-streamer`. |
| `camera_direct_only` | no | `false` | When `true`, forbids the Docker streamer even if `camera_allow_streamer` is set. Does not imply TLS verification — combine with `cert_fingerprint`. |
| Timeouts | no | package defaults | Optional `network_timeout`, `slicer_timeout`, `command_timeout`, `upload_timeout` (seconds) |

\* Either `access_code_file` or `access_code` is required. Inline `access_code` is deprecated and will be removed in a future release.

`allow_private_ips` is **not** a config key — use the CLI flag `--allow-private-ips` per invocation.

## Use with AI agents

`--json` is a global flag accepted by every command that produces structured output. Responses follow published JSON Schema files under [`docs/schemas/`](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/) — agents can validate against them or use them to understand the exact shape of each response.

`--sim` (simulation mode) replaces the real printer with a **canned** local stub — fixed status, files, and camera bytes. It is not a protocol test of MQTT/FTPS. Use it to develop agents and scripts without hardware.

Destructive and physical actions — starting a print, pausing or resuming a print, stopping a job, deleting a file, or sending raw G-code — are gated behind an explicit `--confirm` flag. `print`, `stop`, `pause`, `resume`, `delete`, and `gcode` refuse without it (exit code `5`, `"status": "confirmation_required"`) and the printer is untouched. `job` / `send` without `--confirm` still download, slice, and upload, then exit `0` with `"status": "uploaded_not_printed"` — only the print step is withheld. Note this is a gate against accidents, not an authorization boundary: anything that can run `plate` can also pass `--confirm`.

```bash
# Inspect printer state without hardware
plate --sim status --json

# Full pipeline. Without --confirm this still downloads, slices, and UPLOADS
# (exit 0, "uploaded_not_printed"); with it, the print starts.
plate job <url> --json --confirm
```

## Features in depth

- **Jobs & URL support** — Use `job` when an agent or user gives either a website URL or a local file path. It handles everything in one shot.
- **Printables downloads** — platecli fetches files from Printables *on your behalf*, from your own machine and network — the same file you would get by clicking Download. It identifies itself honestly as `platecli/<version>`, keeps at least one second between requests to the same host, and honors `Retry-After`. Your use is subject to [Printables' terms of service](https://www.printables.com/legal/terms-of-use) and to the individual model's own licence (often a Creative Commons variant with attribution, non-commercial, or no-derivatives conditions). platecli grants you no rights to any downloaded model — check the licence on the model page before printing, remixing, redistributing, or selling. The Printables API used for resolution is undocumented and may change or stop working without notice.
- **Safe extraction** — ZIP archives containing model files are fully supported. Existing files are kept safe by creating a numbered sibling such as `model-1.stl`. URL downloads and ZIP extraction have a 2048 MB safety limit, adjustable via `--max-download-mb`.
- **Modularity** — Run steps individually using `download`, `slice`, `upload`, or `print`.
- **Safety first** — `print`, `pause`, `resume`, `stop`, `delete`, and raw `gcode` refuse without `--confirm` (exit code `5`). `job` / `send` without `--confirm` still upload and exit `0` with `"status": "uploaded_not_printed"`; only the print step is withheld.
- **TLS pinning** — Pin the printer’s self-signed cert with `cert_fingerprint` (setup/doctor can capture it). Prefer this over `insecure_tls`.
- **SSRF-hardened downloads** — Private/loopback targets are refused unless you pass `--allow-private-ips` for that invocation.
- **Diagnostics** — Network, FTPS, and MQTT health checking with `doctor` and `preflight`.
- **Agent JSON** — Structured `--json` output with published schemas under `docs/schemas/`.

## Installing from source

```bash
pip install .
# or: uv sync
```

## Support matrix

OS × Python is what CI runs. Printer models are honest: P1 series on real hardware, everything else best-effort. The full table (including camera caveats and firmware) lives in [docs/api.md](api.md#support-matrix).

## Project layout

- `bambu_cli/` — Runtime package used by the installed command (`plate`).
- `scripts/bambu.py` — Compatibility wrapper for direct script usage without installing.
- `tests/` — Unit, contract, security-marker, and smoke tests.
- `docs/` — API, schemas, quality roadmap, test backlog, mutation baseline, live smoke.

## Documentation map

### Ships with the PyPI sdist (and on GitHub)

| Doc | Audience |
|-----|----------|
| [AGENTS.md](https://github.com/DLANSAMA/platecli/blob/main/AGENTS.md) | Agents and automation (architecture, safety) |
| [docs/api.md](https://github.com/DLANSAMA/platecli/blob/main/docs/api.md) | JSON contracts, support matrix, and stability policy |
| [docs/troubleshooting.md](https://github.com/DLANSAMA/platecli/blob/main/docs/troubleshooting.md) | Symptom-keyed fixes for connection, slicing, and camera errors |
| [docs/schemas/](https://github.com/DLANSAMA/platecli/tree/main/docs/schemas/) | Machine-checkable JSON Schema files |
| [SECURITY.md](https://github.com/DLANSAMA/platecli/blob/main/SECURITY.md) | Threat model, reporting, known limitations |
| [CHANGELOG.md](https://github.com/DLANSAMA/platecli/blob/main/CHANGELOG.md) | Release notes |

Wheels contain **runtime code only** (no docs).

### GitHub / contributor only (not in PyPI packages)

| Doc | Audience |
|-----|----------|
| [CONTRIBUTING.md](https://github.com/DLANSAMA/platecli/blob/main/CONTRIBUTING.md) | Dev setup, tests, releases |
| [docs/quality-roadmap.md](https://github.com/DLANSAMA/platecli/blob/main/docs/quality-roadmap.md) | Quality scoreboard and phased plan |
| [docs/test-backlog.md](https://github.com/DLANSAMA/platecli/blob/main/docs/test-backlog.md) | Remaining test / coverage gaps |
| [docs/live-printer-smoke.md](https://github.com/DLANSAMA/platecli/blob/main/docs/live-printer-smoke.md) | Opt-in real-printer harness |
| [docs/mutation-baseline.md](https://github.com/DLANSAMA/platecli/blob/main/docs/mutation-baseline.md) | Mutation testing scope and floor |
