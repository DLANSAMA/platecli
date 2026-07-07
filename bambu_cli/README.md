# bambu-cli — CLI for Bambu Lab Printers

Fully local 3D printing pipeline for Bambu Lab printers. Runs on **Linux, macOS, and Windows**. Download models from Printables, direct model links, ZIP archives, and simple pages with direct model-file links; slice with OrcaSlicer; and print — all controlled via CLI by any AI agent or by hand. No cloud account needed.

**Supports:** P1P, P1S, X1C, X1E, A1, A1 Mini (any Bambu printer with LAN mode)

## Features

- 🔍 **Download** — Auto-resolves Printables URLs, direct ZIP archives, and simple pages with direct model-file links, prioritizing STL > STEP > OBJ > 3MF > G-code
- ✂️ **Slice** — Headless OrcaSlicer with customizable settings (infill, temps, supports, copies)
- 📤 **Upload** — FTPS transfer to printer's SD card
- 🖨️ **Print** — Start/pause/resume/stop prints via MQTT
- 📡 **G-code** — Send live G-code commands (change temps, fan speed mid-print)
- 📷 **Camera** — Capture snapshots from the printer's built-in camera
- 📂 **Files** — List and delete files on the printer
- 🔄 **STEP Support** — Auto-converts STEP files to STL via gmsh before slicing
- 🧪 **Preflight** — Local install/config checks with JSON output for agents

## Prerequisites

- **Python 3.9+** — On Windows use `python` or `py`; on Linux/macOS use `python3`
- **Python packages** — `paho-mqtt` for printer control, `zeroconf` for auto-discovery
- **OrcaSlicer** — For slicing STL files (see install steps below)
- **xvfb** — For headless slicing on Linux only: `sudo apt install xvfb`
- **gmsh** — For STEP file support (install via `apt`, `brew`, or Windows installer)
- **Docker** *(optional)* — For camera snapshots

## Setup

### 1. Install the Skill

Copy the `bambu-cli` folder into your workspace:
```
~/.bambu-cli/
├── AGENTS.md          # Agent-facing documentation
├── README.md         # This file
├── pyproject.toml    # Optional installed `bambu-cli` command
├── requirements.txt  # Runtime Python dependencies
├── bambu_cli/
│   ├── bambu.py      # Legacy core module used by installed command
│   ├── cli.py        # Argument parsing and command dispatch
│   ├── commands.py   # Modular subcommand implementations
│   ├── config.py     # Config loading and platform path detection
│   ├── printer.py    # BambuPrinter class (FTPS + MQTT client)
│   ├── slicer.py     # OrcaSlicer integration
│   └── protocols/    # FTPS and MQTT protocol helpers
├── scripts/
│   └── bambu.py      # Compatibility wrapper for direct script usage
└── tests/
    ├── test_bambu.py             # Unit tests
    ├── test_cli.py               # CLI parsing/dispatch unit tests
    ├── test_bambu_cli_regressions.py # Regression coverage
    ├── test_agent_cli_smoke.py   # Pytest wrapper for the agent CLI smoke
    ├── agent_cli_smoke.py        # Installed CLI / agent workflow smoke
    ├── ci_workflow_smoke.py      # CI command coverage guard
    ├── dependency_resolution_smoke.py # Oldest-supported-Python dependency resolver guard
    ├── live_printer_smoke.py     # Opt-in real printer validation
    ├── package_contents_smoke.py # sdist/wheel content guard
    ├── privacy_smoke.py          # Personal-info and secret guard
    ├── python_compat_smoke.py    # Python 3.9 syntax guard
    └── release_readiness_smoke.py # Objective-level release guard
```

### 2. Install Dependencies

```bash
# All platforms
python3 -m pip install -r requirements.txt
# Optional but recommended: install the `bambu-cli` command into the active environment
python3 -m pip install -e .
# Windows:
python -m pip install -r requirements.txt
python -m pip install -e .

# Linux
sudo apt install xvfb gmsh

# macOS (Homebrew)
brew install gmsh
# xvfb is not needed on macOS — OrcaSlicer runs without a virtual framebuffer.

# Windows (PowerShell / winget)
winget install gmsh.gmsh
# xvfb is not needed on Windows — OrcaSlicer runs natively.
```

If Python reports `No module named pip`, install or enable pip first:

```bash
# Debian/Ubuntu
sudo apt install python3-pip
# Arch/CachyOS
sudo pacman -S python-pip
# macOS/Windows Python.org installs usually include ensurepip
python3 -m ensurepip --upgrade
python -m ensurepip --upgrade
```

After setup, either command form works:

```bash
python3 scripts/bambu.py --help
python3 scripts/bambu.py --version
bambu-cli --help
bambu-cli --version
```

The examples below use the installed `bambu-cli` command. If you skip
`python3 -m pip install -e .`, use `python3 scripts/bambu.py` on Linux/macOS or
`python scripts\bambu.py` on Windows in place of `bambu-cli`.

For a no-printer smoke test, `--sim` exercises the command flow without touching network hardware:

```bash
# Linux / macOS
python3 scripts/bambu.py --sim status
tmpdir="$(mktemp -d)"
printf 'simulated 3mf content' > "$tmpdir/ready.3mf"
python3 scripts/bambu.py --sim job "$tmpdir/ready.3mf" --confirm --json
```

```powershell
# Windows PowerShell
python scripts\bambu.py --sim status
$tmpdir = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "bambu-cli-sim")
Set-Content -Path (Join-Path $tmpdir.FullName "ready.3mf") -Value "simulated 3mf content"
python scripts\bambu.py --sim job (Join-Path $tmpdir.FullName "ready.3mf") --confirm --json
```

### 3. Enable Printer LAN Mode

On your Bambu Lab printer's touchscreen:
1. Go to **Settings** → **General** → **LAN Only Mode** → **Enable**
2. Go to **Settings** → **General** → **Developer Mode** → **Enable** *(if available)*
3. Note down your printer's:
   - **IP Address** — Found in Settings → Network
   - **Serial Number** — Found in Settings → About or on the printer's label
   - **Access Code** — Found in Settings → Network → Access Code

### 4. Create Config File

The config file is auto-detected by platform:

| Platform | Path |
|----------|------|
| Linux    | `$XDG_CONFIG_HOME/bambu/config.json` → defaults to `~/.config/bambu/config.json` |
| macOS    | `~/Library/Application Support/bambu/config.json` |
| Windows  | `%APPDATA%\bambu\config.json` |

> An existing `~/.config/bambu/config.json` is always honored first, so legacy installs keep working on all platforms.

Recommended setup:

Replace the placeholder IP and serial before running these commands; `setup` refuses to save `192.168.0.XXX`, `YOUR_PRINTER_SERIAL`, or `YOUR_ACCESS_CODE`. The examples prompt for the access code so it is not stored in shell history, command arguments, or `config.json`.

```bash
# Linux: prefer env var + access_code_file so the secret is not stored in shell argv or config.json
read -rsp "Bambu access code: " BAMBU_ACCESS_CODE
echo
export BAMBU_ACCESS_CODE
bambu-cli setup \
  --printer-ip 192.168.0.XXX \
  --serial YOUR_PRINTER_SERIAL \
  --access-code-env BAMBU_ACCESS_CODE \
  --access-code-file ~/.config/bambu/access_code \
  --model P1P \
  --nozzle 0.4 \
  --json
unset BAMBU_ACCESS_CODE
```

```bash
# macOS
read -rsp "Bambu access code: " BAMBU_ACCESS_CODE
echo
export BAMBU_ACCESS_CODE
bambu-cli setup \
  --printer-ip 192.168.0.XXX \
  --serial YOUR_PRINTER_SERIAL \
  --access-code-env BAMBU_ACCESS_CODE \
  --access-code-file "$HOME/Library/Application Support/bambu/access_code" \
  --model P1P \
  --nozzle 0.4 \
  --json
unset BAMBU_ACCESS_CODE
```

```powershell
# Windows PowerShell
$env:BAMBU_ACCESS_CODE = Read-Host "Bambu access code"
bambu-cli setup `
  --printer-ip 192.168.0.XXX `
  --serial YOUR_PRINTER_SERIAL `
  --access-code-env BAMBU_ACCESS_CODE `
  --access-code-file "$env:APPDATA\bambu\access_code" `
  --model P1P `
  --nozzle 0.4 `
  --json
Remove-Item Env:BAMBU_ACCESS_CODE
```

You can also run `setup` with no flags for guided discovery and prompts:

```bash
bambu-cli setup
```

Guided setup defaults to storing the access code in a separate `access_code` file next to `config.json`, rather than embedding the secret directly in JSON. You can opt out during the prompt if you specifically want inline config.
`setup --json` emits one machine-readable setup result on stdout. It reports paths and configured-state booleans but does not echo the printer serial or access code. Path fields under the current home directory are compacted to `~` in agent-facing JSON, including setup error payloads.
When `setup --json` is run without setup values from a non-interactive stdin, it returns a structured missing-values error instead of prompting or printing a traceback.
If non-interactive setup is given an existing `--access-code-file` without a new `--access-code-env`/`--access-code` value, it reads that file first and rejects empty or placeholder contents before writing config.
`--access-code-file` must point to a real file path separate from `config.json`; setup rejects directories and self-referential config paths with structured JSON errors before writing anything.

Run local preflight before a real print:

```bash
bambu-cli preflight
# Agent-friendly JSON:
bambu-cli preflight --json
```

Preflight fails on placeholder values such as `192.168.0.XXX`, `YOUR_PRINTER_SERIAL`, and `YOUR_ACCESS_CODE`, so agents do not continue with an unfinished config. It also verifies that the configured OrcaSlicer path exists, and on Linux/macOS that it is executable.
On Linux/macOS it also warns if `config.json` or `access_code_file` is readable by group/other users; use `preflight --strict --json` when an agent should treat those warnings as blockers.
Agent-facing JSON path fields compact paths under the current home directory to `~`, so JSON output does not casually expose the local account path. Path-bearing JSON error messages use the same `~` compaction.

`doctor` writes a capability report for agents, but it redacts the printer serial so a report can be shared without exposing that identifier.
Use `doctor --json` when an agent needs a live readiness result on stdout. It
returns `status: "ok"` on success or `status: "error"` with `failed_step` and
`exit_code` on failure; human logs stay on stderr. If secure MQTT/FTPS fails
because the printer uses a self-signed certificate, `doctor --json` includes
`certificate_fingerprint`; add that value to `config.json` as `cert_fingerprint`
and re-run `doctor`.

Manual config creation also works:

**Linux / macOS:**
```bash
mkdir -p ~/.config/bambu       # Linux
mkdir -p "$HOME/Library/Application Support/bambu"  # macOS
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force -Path "$env:APPDATA\bambu" | Out-Null
```

Then create `config.json` in that directory with:

```json
{
  "printer_ip": "192.168.0.XXX",
  "serial": "YOUR_PRINTER_SERIAL",
  "access_code_file": "~/.config/bambu/access_code",
  "model": "P1P",
  "nozzle": "0.4"
}
```

Replace the placeholder values with your actual printer info from Step 3, then put only the access code into the separate `access_code` file. On macOS, use `"~/Library/Application Support/bambu/access_code"`; on Windows, use `"%APPDATA%\\bambu\\access_code"`.

> **Security note:** The config file is created with `0600` permissions on Linux/macOS. Windows ignores POSIX mode bits — if you're on Windows and concerned about other local users reading the access code, use a separate `access_code_file` under `%APPDATA%\bambu\access_code` and restrict it via NTFS ACLs (right-click → Properties → Security). On macOS, use `~/Library/Application Support/bambu/access_code`.
> ```json
> {
>   "printer_ip": "192.168.0.XXX",
>   "serial": "YOUR_PRINTER_SERIAL",
>   "access_code_file": "%APPDATA%\\bambu\\access_code"
> }
> ```
> On Linux, use `"~/.config/bambu/access_code"`; on macOS, use `"~/Library/Application Support/bambu/access_code"`. Then put just the access code into that file.

### 5. Install OrcaSlicer (for slicing)

**Linux** (AppImage):
```bash
mkdir -p ~/tools && cd ~/tools
wget https://github.com/SoftFever/OrcaSlicer/releases/latest/download/OrcaSlicer_Linux.AppImage -O OrcaSlicer.AppImage
chmod +x OrcaSlicer.AppImage
./OrcaSlicer.AppImage --appimage-extract  # extracts profiles
```

**macOS** (download the `.dmg` from the [OrcaSlicer releases](https://github.com/SoftFever/OrcaSlicer/releases) page, then install). Profiles live inside the app bundle — typically at:
```
/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL
```

**Windows** (download the installer from the [OrcaSlicer releases](https://github.com/SoftFever/OrcaSlicer/releases) page). Profiles are installed to:
```
C:\Program Files\OrcaSlicer\resources\profiles\BBL
```

Point your config at the binary and profiles:
```json
{
  "orca_slicer": "/path/to/OrcaSlicer",
  "profiles_dir": "/path/to/profiles/BBL"
}
```

Examples:
- Linux AppImage: `"orca_slicer": "~/tools/OrcaSlicer.AppImage"`, `"profiles_dir": "~/tools/squashfs-root/resources/profiles/BBL"`
- macOS: `"orca_slicer": "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"`, `"profiles_dir": "/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL"`
- Windows: `"orca_slicer": "C:\\Program Files\\OrcaSlicer\\OrcaSlicer.exe"`, `"profiles_dir": "C:\\Program Files\\OrcaSlicer\\resources\\profiles\\BBL"`

### 6. Test It

```bash
# Check connection to printer (Linux / macOS)
bambu-cli status

# Windows (PowerShell) — adjust path to your install location
bambu-cli status

# Expected output:
# 🖨️  Bambu Printer Status
#    State: IDLE
#    ...
```

## Agent-Friendly One-Shot Printing

Use `job` when an agent or user gives either a website URL or a local file path. It chooses the right workflow automatically:

- URL: Printables model pages, direct model-file downloads, ZIP archives containing model files, or simple HTML pages containing direct model-file links are downloaded first, then sliced if needed. Direct download endpoints with `Content-Disposition` filenames or HTML `download` filename hints are supported.
- Website inputs may include or omit `https://`, e.g. `printables.com/model/12345-thing`
- Local paths may use `~` or environment variables such as `$MODEL_DIR/part.stl`; local ZIP archives are opened safely the same way downloaded ZIPs are
- `.stl`, `.step`, `.stp`, `.obj`: slice to `.3mf`, upload, then print if confirmed. STEP/STP conversion uses a temporary STL so an existing same-name `.stl` beside the source is not overwritten or deleted.
- `.3mf`, `.gcode`: upload directly, then print if confirmed
- Obvious non-model downloads such as `.rar`, `.7z`, `.pdf`, images, text files, and known-bad response content types are rejected instead of being renamed as model files. ZIP files are opened safely and only a supported model/print file is extracted.
- URL downloads and ZIP extraction have a 2048 MB safety limit by default so agent-triggered jobs cannot fill the disk by mistake; use `--max-download-mb` for unusually large files.
- Unless `--output` is provided, `job/send` uses a private temporary work directory for downloads, ZIP extraction, and sliced files, so local source directories are not cluttered or overwritten. `--output` is ignored for local `.3mf`/`.gcode` files that are already printer-ready.

```bash
# URL -> download -> slice/upload -> print
bambu-cli job "https://www.printables.com/model/12345-thing" --confirm
# Agent-readable final summary (logs go to stderr, JSON goes to stdout)
bambu-cli job "https://www.printables.com/model/12345-thing" --confirm --json

# Local file -> slice/upload -> print
bambu-cli job "/path/to/model.stl" --confirm

# Safe preparation only: upload but do not start the printer
bambu-cli job "/path/to/model.stl" --upload-only

# No-side-effect check; dry-runs skip download/slice/upload/print
bambu-cli job "https://www.printables.com/model/12345-thing" --dry-run --json

# Alias: `send` is the same command as `job`
bambu-cli send "/path/to/model.3mf" --confirm

# Standalone download also has agent-readable JSON
bambu-cli download "https://example.com/model.stl" --json

# List uploaded printer files for agent follow-up actions
bambu-cli files --json

# Machine-readable control commands for agent workflows
bambu-cli pause --json
bambu-cli resume --json
bambu-cli light on --json
bambu-cli stop --confirm --json
bambu-cli delete "old_print.3mf" --confirm --json
bambu-cli gcode "M104 S220" --json
bambu-cli snapshot --json
```

Standalone `download` and ZIP extraction do not overwrite an existing file with
the same name; they save to a numbered sibling such as `model-1.stl` instead.
Local files uploaded or printed by name must also have printer-safe portable
filenames: control characters, path separators, Windows-reserved characters
such as `:`, `?`, `*`, trailing spaces/dots, and reserved names like `CON.gcode`
are rejected before any printer connection is attempted. Printer filenames must
also fit within the same 160-character safety limit used for downloaded files.
Printer-side `print` and `delete` accept a remote filename only, not `/model/...`
paths or local filesystem paths; path-like values are rejected instead of being
silently reduced to a basename.
Local model files whose sliced `.3mf` output would have an unsafe printer
filename are rejected before slicing. URL download filenames are sanitized
automatically.

The one-shot command will not start a print unless `--confirm` is present.
With `job/send --json`, stdout is a single JSON object on success or failure.
The JSON includes `command` (`job` or `send`) so agents can distinguish the
entry point they invoked.
For agent-generated commands, `--json` may be placed before or after the
subcommand, so `bambu-cli --json job ...` and `bambu-cli job ... --json` are
equivalent for commands that support JSON.
`bambu-cli --json --version` emits a single JSON object for installed-command
provenance checks.
Failures use `status: "error"` with `failed_step` (`validate`, `download`,
`extract`, `slice`, `upload`, or `print`) and `exit_code`, while human logs stay on stderr.
When a lower-level step fails inside `job/send`, the payload also includes a
step-specific detail object such as `download_error`, `slice_error`,
`upload_error`, or `print_error` with the original command's structured reason,
such as rejected content type, missing slicer profile, or print ACK timeout.
When `job/send --json` uploads successfully but does not start a print,
`next_command` contains the explicit `["print", "<remote_name>", "--confirm",
"--json"]` command arguments to run after user confirmation.
If upload succeeds but confirmed print start fails, the error payload includes
`next_command: ["status", "--json"]` and a `recovery_hint` so agents check the
printer state before retrying.
Lower-level `print`, `stop`, and `delete` JSON confirmation-required payloads
also include `next_command` with the exact confirmed command arguments.
If an agent makes a CLI argument mistake while including `--json`, the parser
also returns one JSON error object with `failed_step: "parse"`.

### 7. Camera Setup *(Optional)*

For the `snapshot` command, you need a locally available [BambuP1Streamer](https://github.com/slynn1324/BambuP1Streamer) Docker image. The upstream project documents building an image named `bambu_p1_streamer`, and notes it is intended for P1-class cameras.

```bash
docker run -d --name bambu_camera -p 1985:1984 \
  -e PRINTER_ADDRESS=YOUR_IP \
  -e PRINTER_ACCESS_CODE=YOUR_CODE \
  bambu_p1_streamer
```

> **Note:** The `snapshot` command will auto-start this container if it's stopped, so manual setup is optional.
> `--network host` is sometimes shown in guides but only works on Linux — the `-p` flag is cross-platform.
> If you build or pull the image under another name, set `"camera_image": "your-image-name"` in `config.json`.

## Config Reference

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `printer_ip` | ✅ | — | Printer's LAN IP address |
| `serial` | ✅ | — | Printer serial number |
| `access_code` | ✅* | — | Printer access code (or use `access_code_file`) |
| `access_code_file` | ✅* | — | Path to file containing access code |
| `mqtt_port` | | 8883 | MQTT TLS port |
| `cert_fingerprint` | | — | SHA-256 of the printer's self-signed cert. Pins FTPS + MQTT to that exact cert. Run `doctor` to print the value to copy. Recommended over `insecure_tls`. |
| `insecure_tls` | | false | Disable TLS certificate verification entirely (last resort if pinning isn't viable) |
| `username` | | bblp | MQTT username |
| `model` | | P1P | Printer model: P1P, P1S, X1C, X1, X1E, A1, A1M |
| `nozzle` | | 0.4 | Nozzle size: 0.2, 0.4, 0.6, 0.8 |
| `camera_image` | | bambu_p1_streamer | Optional Docker image name used by `snapshot` |
| `orca_slicer` | | *(auto-detected)* | OrcaSlicer path — defaults to `/Applications/OrcaSlicer.app/.../OrcaSlicer` (macOS), `C:\Program Files\OrcaSlicer\OrcaSlicer.exe` (Windows), or `~/tools/OrcaSlicer.AppImage` (Linux), with secondary platform fallbacks |
| `profiles_dir` | | *(auto-detected)* | Slicer profiles path — defaults to the standard OrcaSlicer profiles directory per platform, with secondary platform fallbacks |

*Either `access_code` or `access_code_file` is required.

## Slice Defaults

| Setting | Default | Description |
|---------|---------|-------------|
| Quality | standard (0.20mm) | Layer height (draft/standard/high/0.16/0.24) |
| Infill | 15% | Fill density |
| Pattern | 3dhoneycomb | Fill pattern |
| Filament | PLA Basic | Filament profile (e.g. 'PLA Basic', 'PETG') |
| Nozzle | 220°C | Nozzle temperature |
| Bed | 60°C | Bed temperature (all plate types) |
| Supports | off | Support material |
| Copies | 1 | Number of copies auto-arranged on plate |

## Print Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--confirm` | off | Required to start the print |
| `--dry-run` | off | Validate file existence without printing |
| `--use-ams` | off | Enable AMS (Automatic Material System) |
| `--ams-mapping` | — | AMS slot mapping with zero-or-positive slot indexes, e.g. `1` or `0,1,2` |
| `--timelapse` | off | Enable timelapse recording |
| `--skip-bed-leveling` | off | Skip automatic bed leveling |
| `--skip-flow-cali` | off | Skip flow calibration |

## Global Flags

| Flag | Description |
|------|-------------|
| `-v` / `--verbose` | Enable debug logging |
| `--sim` | Simulation mode (no live printer needed) |
| `--json` | Emit JSON for commands that support it; may appear before the subcommand |
| `--version` | Print the CLI version |

## One-Shot Job Flags

`job` accepts all slice and print flags, plus:

| Flag | Description |
|------|-------------|
| `--confirm` | Start the print after upload |
| `--upload-only` | Stop after uploading to the printer |
| `--dry-run` | No-side-effect validation; skips download/slice/upload/print and reports planned steps |
| `--json` | Emit a final machine-readable summary for agents |
| `--name` | Filename to use for URL downloads |
| `--max-download-mb` | Maximum URL download and ZIP extraction size in MB; default 2048 |
| `--output` | Working/output directory for downloads, ZIP extraction, and sliced `.3mf` files; created automatically only when those steps need it. Ignored for local `.3mf`/`.gcode` files that are already printer-ready |

Lower-level `download`, `slice`, `upload`, `files`, `print`, `status`, `light`, `pause`, `resume`, `stop`, `delete`, `gcode`, `snapshot`, and `doctor` commands also support `--json` for agent-readable summaries. Successful payloads include `command`; `status --json` includes the raw printer report under `printer` while preserving common printer fields such as `gcode_state` at top level. On failure they emit `status: "error"` with `command`, `failed_step`, `exit_code`, and `error` where applicable, including parser failures as `failed_step: "parse"`. Prefer `job --json` or `send --json` for normal URL/path-to-print workflows; if a delegated step fails inside `job/send`, inspect `download_error`, `slice_error`, `upload_error`, or `print_error` for the lower-level reason; ZIP selection/extraction problems use `failed_step: "extract"`.
Dry-run `job/send --json` summaries include `would_download`, `would_extract`,
`would_slice`, `would_upload`, and `would_print` where those steps are known
without side effects. URL dry-runs do not download; direct `.stl`, `.step`,
`.stp`, and `.obj` URLs still report `would_slice: true`, while direct `.zip`
URLs report `would_extract: true`.
For local model dry-runs, local ZIP dry-runs, and direct URL dry-runs with a
known filename or explicit `--name`, `remote_name` is included when the eventual
printer filename is predictable without creating files or downloading.
If a dry-run would need to create a missing `--output` directory during the real
run, the JSON includes `would_create_output_dir: true` instead of creating it.
Use `preflight --json` for local install/config checks and `doctor --json` for
live printer readiness before starting an automated job.

## Release Validation

Before shipping changes, run the full local release stack from the repository root:

On Windows, replace `python3` with `python` or `py` in the commands below.

```bash
python3 -m py_compile bambu_cli/__init__.py bambu_cli/bambu.py bambu_cli/cli.py bambu_cli/config.py bambu_cli/slicer.py bambu_cli/commands.py bambu_cli/printer.py bambu_cli/protocols/ftps.py bambu_cli/protocols/mqtt.py scripts/bambu.py tests/test_bambu.py tests/agent_cli_smoke.py tests/ci_workflow_smoke.py tests/dependency_resolution_smoke.py tests/live_printer_smoke.py tests/package_contents_smoke.py tests/privacy_smoke.py tests/python_compat_smoke.py tests/release_readiness_smoke.py scripts/__init__.py
python3 -W error::ResourceWarning -m unittest tests.test_bambu
python3 tests/agent_cli_smoke.py
python3 tests/privacy_smoke.py
python3 tests/ci_workflow_smoke.py
python3 tests/release_readiness_smoke.py
python3 tests/python_compat_smoke.py
python3 tests/dependency_resolution_smoke.py
uv run --with build python -m build --sdist --wheel --outdir dist
python3 tests/package_contents_smoke.py
python3 tests/privacy_smoke.py --include-dist
```

If `uv` is unavailable and your Python has pip, install `build` with `python3 -m pip install build` and then run `python3 -m build --sdist --wheel --outdir dist`.
Also install the built wheel into a fresh virtual environment and run `tests/agent_cli_smoke.py` with `BAMBU_CLI` pointed at that environment's `bambu-cli` executable. The final release gate is the opt-in live-printer smoke below.
Without `BAMBU_CLI`, `tests/agent_cli_smoke.py` intentionally runs this checkout's `scripts/bambu.py` so source validation cannot accidentally pass against a stale installed command from `PATH`.

## Live Printer Release Smoke

The live proof gate is intentionally opt-in because it can upload to a real printer and, when explicitly enabled, start a print. It uses your existing Bambu config and never stores credentials in the repo.

```bash
# Upload-only proof: no print starts.
BAMBU_LIVE_SOURCE=/path/to/ready.3mf python3 tests/live_printer_smoke.py

# Upload-only proof with cleanup after verification:
BAMBU_LIVE_SOURCE=/path/to/ready.3mf \
BAMBU_LIVE_CLEANUP=1 \
python3 tests/live_printer_smoke.py

# Full print ACK proof: starts the uploaded file after doctor/preflight/upload pass.
BAMBU_LIVE_SOURCE=/path/to/ready.3mf \
BAMBU_LIVE_PRINT_CONFIRM=1 \
python3 tests/live_printer_smoke.py
```

`BAMBU_LIVE_SOURCE` may be a printer-ready `.3mf`/`.gcode` file or a source accepted by `job`. Use a uniquely named source file for release proof; the smoke checks `files --json` before upload and fails if the resulting remote filename already exists, because that would make the upload proof ambiguous. By default the script runs this checkout's `scripts/bambu.py`, so release proof covers the code being reviewed; set `BAMBU_CLI` explicitly only when validating an installed command. The script verifies `preflight --strict --json`, `doctor --json`, `job --upload-only --json`, confirms the uploaded file appears in `files --json`, and, only with an explicit `BAMBU_LIVE_PRINT_CONFIRM` truthy value (`1`, `true`, `yes`, or `on`), `print --confirm --json`. Set `BAMBU_LIVE_CLEANUP=1` to delete the uploaded test file after upload-only verification; cleanup is skipped after a confirmed print start and independently confirms the deleted file no longer appears in `files --json`. It refuses `BAMBU_CLI` commands that include `--sim`, because simulation is not real-printer proof.
For local `.3mf`, `.gcode`, `.stl`, `.step`, `.stp`, and `.obj` sources, plus direct URL paths ending in those extensions, the live smoke predicts the remote filename and rejects a pre-existing match before upload, so release proof does not overwrite an older printer file. For ZIP, Printables, HTML page, or extensionless URL sources where the remote name is not knowable before work starts, set `BAMBU_LIVE_EXPECT_REMOTE_NAME=<expected-file.3mf>` to enable the same pre-upload collision check.

## License

MIT — Use freely, modify as needed.
