---
name: bambu-cli
description: Download, slice, and print 3D models on a Bambu Lab printer. Supports Printables URLs, direct model links, ZIP archives, simple pages with direct model-file links, custom slice settings, live G-code, camera snapshots, and full print management.
---

# Bambu Printer CLI

Local 3D printing pipeline via MQTT, FTPS, and OrcaSlicer. No cloud needed. Runs on Linux, macOS, and Windows.

**Script:** `python3 <path>/scripts/bambu.py`

> If this skill lives somewhere else on disk, substitute that path in every example below — every command is `python3 <path-to>/scripts/bambu.py <subcommand> ...`.
>
> **Windows note:** Use `python` (or `py`) instead of `python3` — Windows Python installers typically do not create a `python3` alias.

## Prerequisites (verify before acting)
1. **Config exists** — the CLI refuses to run without one. Auto-detected per platform:
   - Linux: `~/.config/bambu/config.json` (or `$XDG_CONFIG_HOME/bambu/config.json`)
   - macOS: `~/Library/Application Support/bambu/config.json`
   - Windows: `%APPDATA%\bambu\config.json`
   - An existing `~/.config/bambu/config.json` always wins, so legacy setups keep working.
2. **`preflight`** verifies local install/config without contacting the printer:
   ```bash
   python3 <path>/scripts/bambu.py preflight
   python3 <path>/scripts/bambu.py preflight --json
   ```
   Exit 0 = local dependencies/config look usable. Exit 1 = missing required local pieces.
   It also rejects placeholder config values like `YOUR_PRINTER_SERIAL` and `YOUR_ACCESS_CODE`.
   It verifies that the configured OrcaSlicer path exists, and on Linux/macOS that it is executable.
   On Linux/macOS it warns if `config.json` or `access_code_file` is readable by group/other users; use `preflight --strict --json` when an agent should treat those warnings as blockers.
   Agent-facing JSON path fields compact paths under the current home directory to `~`, so JSON output does not casually expose the local account path. Path-bearing JSON error messages use the same `~` compaction.
3. **`doctor`** verifies live printer connectivity after preflight passes:
   ```bash
   python3 <path>/scripts/bambu.py doctor
   python3 <path>/scripts/bambu.py doctor --json
   ```
   Exit 0 = ready. Exit 1 = config missing/bad. Exit 2 = network unreachable.
   `--json` emits one machine-readable health object on stdout; failures include
   `status=error`, `failed_step`, and `exit_code`. The capability report redacts
   the printer serial before writing JSON to disk or stdout. If a secure
   MQTT/FTPS check fails because the printer uses a self-signed cert, the JSON
   error may include `certificate_fingerprint`; add it to config as
   `cert_fingerprint` and re-run `doctor`.
4. **`setup`** bootstraps the config. Prefer non-interactive setup when the user gives printer details:
   ```bash
   # Linux
   read -rsp "Bambu access code: " BAMBU_ACCESS_CODE
   echo
   export BAMBU_ACCESS_CODE
   python3 <path>/scripts/bambu.py setup \
     --printer-ip USER_PROVIDED_IP \
     --serial USER_PROVIDED_SERIAL \
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
   python3 <path>/scripts/bambu.py setup \
     --printer-ip USER_PROVIDED_IP \
     --serial USER_PROVIDED_SERIAL \
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
   python <path>\scripts\bambu.py setup `
     --printer-ip USER_PROVIDED_IP `
     --serial USER_PROVIDED_SERIAL `
     --access-code-env BAMBU_ACCESS_CODE `
     --access-code-file "$env:APPDATA\bambu\access_code" `
     --model P1P `
     --nozzle 0.4 `
     --json
   Remove-Item Env:BAMBU_ACCESS_CODE
   ```
   Replace every `USER_PROVIDED_*` value before running it; do not put the access code directly in the command line. Non-interactive setup rejects placeholders instead of writing an unfinished config. If `--access-code-file` points to an existing file without a new `--access-code-env` value, setup reads that file and rejects placeholder/empty contents before saving config. `--access-code-file` must point to a real file path separate from `config.json`; setup rejects directories and self-referential config paths with structured JSON errors before writing anything. `setup --json` emits `status=configured`, config/secret-file paths, booleans for configured printer identifiers, and never echoes the serial or access code. Path fields under the current home directory are compacted to `~`, including setup error payloads.
   If an agent runs `setup --json` without setup values from non-interactive stdin, it returns a structured missing-values error instead of prompting.
   If details are not provided, `python3 <path>/scripts/bambu.py setup` runs guided discovery and prompts for the access code. Guided setup defaults to writing the secret to an `access_code` file next to `config.json`, not inline in `config.json`.
5. **Python dependencies** are declared in `requirements.txt`; `pyproject.toml` also exposes a `bambu-cli` command:
   ```bash
   python3 -m pip install -r <path>/requirements.txt
   python3 -m pip install -e <path>
   bambu-cli --version
   # Windows:
   python -m pip install -r <path>\requirements.txt
   python -m pip install -e <path>
   bambu-cli --version
   ```
   If `python -m pip` fails with `No module named pip`, install/enable pip first (`python -m ensurepip --upgrade`, `sudo apt install python3-pip`, or `sudo pacman -S python-pip` depending on platform).

## Quick Start - Print a Model
```bash
# URL or local path -> download/slice/upload/print as needed
python3 <path>/scripts/bambu.py job "https://www.printables.com/model/XXXXX-name" --confirm
# For agent parsing, add --json; logs go to stderr and final JSON goes to stdout.
python3 <path>/scripts/bambu.py job "https://www.printables.com/model/XXXXX-name" --confirm --json
# On failure, job/send --json still emits one JSON object with status=error,
# failed_step, and exit_code before exiting nonzero.

# Local file works the same way
python3 <path>/scripts/bambu.py job "/path/to/model.stl" --confirm
```

## Core Workflow
Prefer `job`/`send` for agent work. It accepts either a website URL or a local path and chooses the right steps:
1. URL sources are downloaded first. Supported website pages are Printables model pages and simple HTML pages containing direct links to `.stl`, `.step`, `.stp`, `.obj`, `.3mf`, `.gcode`, or `.zip` files, including links whose URL is a download endpoint but whose HTML `download` attribute names a supported model file. Direct model-file and ZIP download URLs also work, including download endpoints that provide a `Content-Disposition` filename. ZIP files are opened safely and only one supported model/print file is extracted. `https://` may be omitted for normal website inputs like `printables.com/model/12345-thing`. Obvious non-model downloads such as `.rar`, `.7z`, `.pdf`, images, text files, and known-bad response content types are rejected instead of being renamed as model files.
   URL downloads and ZIP extraction have a 2048 MB safety limit by default so agent-triggered jobs cannot fill the disk by mistake; use `--max-download-mb` for unusually large files.
2. Local paths may use `~` or environment variables. `.zip` archives are opened safely and one supported model/print file is extracted; `.stl`, `.step`, `.stp`, and `.obj` files are sliced into `.3mf`.
3. `.3mf` and `.gcode` files are uploaded directly.
4. Unless `--output` is provided, `job/send` uses a private temporary work directory only when it needs to download, extract, or slice, so local source directories are not cluttered or overwritten. `--output` is ignored for local `.3mf`/`.gcode` files that are already printer-ready.
5. It starts the printer only when `--confirm` is present.

Lower-level commands are still available when you need manual control:
1. `download` — Get model from Printables URL, direct link, ZIP archive, or simple page with direct model links (priority: STL > STEP > OBJ > 3MF > G-code)
2. `slice` — Slice STL/STEP/STP/OBJ into .3mf with OrcaSlicer
3. `upload` — Transfer .3mf/.gcode to printer via FTPS
4. `print` — Start a file already on the printer via MQTT

Always ask the user before running any command with `--confirm`.

## No-Printer Smoke Test
Use `--sim` when validating the skill on a machine without a printer. It avoids real network/FTPS/MQTT traffic but still exercises the CLI path:
```bash
# Linux / macOS
tmpdir="$(mktemp -d)"
printf 'simulated 3mf content' > "$tmpdir/ready.3mf"
python3 <path>/scripts/bambu.py --sim job "$tmpdir/ready.3mf" --confirm --json
```
```powershell
# Windows PowerShell
$tmpdir = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "bambu-cli-sim")
Set-Content -Path (Join-Path $tmpdir.FullName "ready.3mf") -Value "simulated 3mf content"
python <path>\scripts\bambu.py --sim job (Join-Path $tmpdir.FullName "ready.3mf") --confirm --json
```
The JSON status should be `printed`.

## All Commands

### Download
```bash
# From Printables (auto-resolves, finds STL files)
python3 <path>/scripts/bambu.py download "https://www.printables.com/model/12345-thing"

# Direct URL
python3 <path>/scripts/bambu.py download "https://example.com/model.stl"
# Agent-readable path summary
python3 <path>/scripts/bambu.py download "https://example.com/model.stl" --json

# Options
--output /path/to/dir    # Save directory; created if missing (default: system temp dir)
--name "custom.stl"      # Custom filename
--json                   # Emit final path/filename/byte summary on stdout
```
If the target filename already exists, `download`/ZIP extraction writes a
numbered sibling such as `model-1.stl` instead of overwriting the existing file.

### Setup & Health
```bash
# Local install/config readiness, no printer connection
python3 <path>/scripts/bambu.py preflight
python3 <path>/scripts/bambu.py preflight --json

# Guided discovery and config generation
python3 <path>/scripts/bambu.py setup

# Verify connectivity and discover capabilities
python3 <path>/scripts/bambu.py doctor
python3 <path>/scripts/bambu.py doctor --json
# Optional: pick where the capabilities JSON is written (default: system temp dir)
python3 <path>/scripts/bambu.py doctor --output ./bambu-work/caps.json
```

### One-Shot Job
```bash
# URL -> download -> slice if needed -> upload -> print
python3 <path>/scripts/bambu.py job "https://www.printables.com/model/12345-thing" --confirm
python3 <path>/scripts/bambu.py job "https://www.printables.com/model/12345-thing" --confirm --json

# Local model -> slice -> upload -> print
python3 <path>/scripts/bambu.py job "/path/to/model.stl" --confirm

# Printer-ready file -> upload -> print
python3 <path>/scripts/bambu.py job "/path/to/model.3mf" --confirm

# Prepare/upload only, no print start
python3 <path>/scripts/bambu.py job "/path/to/model.stl" --upload-only

# No-side-effect check; skips download/slice/upload/print and reports planned steps
python3 <path>/scripts/bambu.py job "https://www.printables.com/model/12345-thing" --dry-run --json

# `send` is an alias for `job`
python3 <path>/scripts/bambu.py send "/path/to/model.3mf" --confirm

# Job accepts the same slice/print flags:
--quality standard
--json
--dry-run                         # No-side-effect validation; skips download/slice/upload/print
--output /path/to/workdir          # Optional work dir; defaults to private temp dir for job/send
--max-download-mb 2048             # URL download/ZIP extraction safety limit; raise only for unusually large files
--filament "PLA Basic"
--infill 15
--supports
--copies 1
--use-ams
--ams-mapping 0,1,2
--timelapse
--skip-bed-leveling
--skip-flow-cali
```

For `job/send --json`, parse stdout as a single JSON object. The payload includes
`command` (`job` or `send`). On success, `status` is `printed`, `uploaded`,
`uploaded_not_printed`, or a dry-run status. On failure, `status` is `error` and
`failed_step` identifies `validate`, `download`, `extract`, `slice`, `upload`, or `print`;
use `exit_code` for the CLI reason. If a delegated step fails inside
`job/send`, inspect the matching detail object (`download_error`, `slice_error`,
`upload_error`, or `print_error`) for the original lower-level command reason,
such as rejected content type, missing slicer profile, upload timeout, or print
ACK timeout.
When `job/send --json` uploads successfully but does not start a print,
`next_command` contains the explicit `["print", "<remote_name>", "--confirm",
"--json"]` command arguments to run after user confirmation.
If upload succeeds but confirmed print start fails, the error payload includes
`next_command: ["status", "--json"]` and a `recovery_hint` so agents check the
printer state before retrying.
Lower-level `print`, `stop`, and `delete` JSON confirmation-required payloads
also include `next_command` with the exact confirmed command arguments.
Dry-run `job/send --json` summaries include `would_download`, `would_extract`,
`would_slice`, `would_upload`, and `would_print` when those steps are knowable
without side effects. URL dry-runs do not download; direct `.stl`, `.step`,
`.stp`, and `.obj` URLs still report `would_slice: true`, while direct `.zip`
URLs report `would_extract: true`.
For local model dry-runs, local ZIP dry-runs, and direct URL dry-runs with a
known filename or explicit `--name`, `remote_name` is included when the eventual
printer filename is predictable without creating files or downloading.
Local files uploaded or printed by name must use printer-safe portable
filenames: control characters, path separators, Windows-reserved characters
such as `:`, `?`, `*`, trailing spaces/dots, and reserved names like `CON.gcode`
are rejected before any printer connection. Printer filenames must also fit
within the same 160-character safety limit used for downloaded files.
Printer-side `print` and `delete` accept a remote filename only, not `/model/...`
paths or local filesystem paths; path-like values are rejected instead of being
silently reduced to a basename. Local model files whose sliced `.3mf` output
would have an unsafe printer filename are rejected before slicing. URL download
filenames are sanitized automatically.
If a dry-run would need to create a missing `--output` directory during the real
run, the JSON includes `would_create_output_dir: true` instead of creating it.
Agents may place `--json` before or after the subcommand for commands that
support JSON. If an agent makes a CLI argument mistake while including `--json`,
parse errors are also emitted as a single JSON object with
`failed_step: "parse"`.
Use `python3 <path>/scripts/bambu.py --json --version` or
`bambu-cli --json --version` for machine-readable installed-command provenance
checks.

Lower-level `download`, `slice`, `upload`, `files`, `print`, `status`, `light`, `pause`, `resume`, `stop`, `delete`, `snapshot`, `doctor`, and `gcode` commands also
emit a single JSON object when `--json` is present. Successful payloads include
`command`; `status --json` also includes the raw printer report under `printer`
and preserves common printer fields such as `gcode_state` at top level. On
failure, expect `status=error`, `command`, `failed_step` where applicable,
`exit_code`, and `error`.

### Slice
```bash
python3 <path>/scripts/bambu.py slice ./model.stl --output ./bambu-work/
python3 <path>/scripts/bambu.py slice ./model.stl --output ./bambu-work/ --json
# STEP files are auto-converted to STL via gmsh
python3 <path>/scripts/bambu.py slice ./model.step --output ./bambu-work/

# Slice options (every flag the CLI accepts — defaults shown where applicable):
--quality standard                 # draft (0.28mm) / standard (0.20mm) / high (0.12mm) / 0.16 / 0.24
--infill 15                        # Infill density 0–100%
--pattern 3dhoneycomb              # grid / gyroid / honeycomb / 3dhoneycomb / crosshatch
--nozzle-temp 220                  # Nozzle temperature °C
--bed-temp 60                      # Bed temperature °C
--supports                         # Enable support material (off by default)
--support-type tree                # tree / normal
--support-interface-density 50     # Support interface density % (no default, optional)
--support-interface-pattern rectilinear # rectilinear / concentric / honeycomb
--walls 2                          # Number of wall loops
--wall-type normal                 # normal (arachne) / classic
--top-layers 4                     # Number of top solid layers (optional)
--bottom-layers 4                  # Number of bottom solid layers (optional)
--accel-wall 500                   # Inner wall acceleration (mm/s²)
--accel-wall-outer 1000            # Outer wall acceleration (mm/s²)
--accel-infill 800                 # Infill acceleration (mm/s²)
--accel-travel 1500                # Travel acceleration (mm/s²)
--accel-first-layer 500            # First-layer acceleration (mm/s²)
--copies 1                         # Number of copies auto-arranged on plate
--filament "PLA Basic"              # Filament profile (e.g. 'PLA Basic', 'PETG', 'ABS')
--output <dir>                     # Output directory; created if missing (default: same dir as input)

# Examples
python3 <path>/scripts/bambu.py slice model.stl --quality high --infill 30 --pattern gyroid
python3 <path>/scripts/bambu.py slice model.stl --nozzle-temp 210 --bed-temp 55 --supports
python3 <path>/scripts/bambu.py slice model.stl --copies 9 --output ./bambu-work/
```

### Upload & Print
```bash
# Upload sliced file to printer
python3 <path>/scripts/bambu.py upload ./bambu-work/model_sliced.3mf

# Dry run (validates file and network without action)
python3 <path>/scripts/bambu.py upload ./model.3mf --dry-run
python3 <path>/scripts/bambu.py print "model.3mf" --dry-run
# Agent-readable summaries
python3 <path>/scripts/bambu.py upload ./model.3mf --json
python3 <path>/scripts/bambu.py print "model.3mf" --dry-run --json

# Start print (requires --confirm)
python3 <path>/scripts/bambu.py print "model_sliced.3mf" --confirm

# Print with AMS enabled
python3 <path>/scripts/bambu.py print "model.3mf" --confirm --use-ams --ams-mapping 0,1,2

# Print with timelapse, skip calibration
python3 <path>/scripts/bambu.py print "model.3mf" --confirm --timelapse --skip-flow-cali

# Print flags:
# --use-ams             Enable AMS (Automatic Material System)
# --ams-mapping 0,1,2   AMS slot mapping (comma-separated zero-or-positive integers)
# --timelapse           Enable timelapse recording
# --skip-bed-leveling   Skip automatic bed leveling
# --skip-flow-cali      Skip flow calibration
```

### Monitor & Control
```bash
# Printer status
python3 <path>/scripts/bambu.py status
python3 <path>/scripts/bambu.py status --json

# Print control
python3 <path>/scripts/bambu.py pause
python3 <path>/scripts/bambu.py pause --json
python3 <path>/scripts/bambu.py resume
python3 <path>/scripts/bambu.py resume --json
python3 <path>/scripts/bambu.py stop --confirm
python3 <path>/scripts/bambu.py stop --confirm --json

# Light
python3 <path>/scripts/bambu.py light on
python3 <path>/scripts/bambu.py light off
python3 <path>/scripts/bambu.py light on --json

# Camera snapshot (optional; requires a local BambuP1Streamer Docker image)
python3 <path>/scripts/bambu.py snapshot
python3 <path>/scripts/bambu.py snapshot --output ./bambu-work/photo.jpg
python3 <path>/scripts/bambu.py snapshot --json
```

### File Management
```bash
# List files on printer
python3 <path>/scripts/bambu.py files
python3 <path>/scripts/bambu.py files --json
# In JSON output, use each entry's `name` value with the `print` command.

# Delete file (requires --confirm)
python3 <path>/scripts/bambu.py delete "old_print.3mf" --confirm
python3 <path>/scripts/bambu.py delete "old_print.3mf" --confirm --json
```

### Live G-code
```bash
# Send any G-code command to the printer in real-time
python3 <path>/scripts/bambu.py gcode "M104 S220"   # Set nozzle temp
python3 <path>/scripts/bambu.py gcode "M140 S60"    # Set bed temp
python3 <path>/scripts/bambu.py gcode "M106 S255"   # Fan speed (0-255)
python3 <path>/scripts/bambu.py gcode "M960 S4 P1"  # Chamber light on
python3 <path>/scripts/bambu.py gcode "M104 S220" --json
```

## Important Rules
- **ALWAYS ask user before printing** — never auto-print without confirmation
- **Prefer `job`/`send` for URLs and local file paths** — it handles download, slicing, upload, and print confirmation in one place
- **Use `download` for Printables, ZIP archives, and simple model pages** — do NOT use agent-browser when the page exposes direct model-file links; the download command handles it
- **STL is preferred** — the download command auto-picks STL > STEP/STP > OBJ > 3MF > G-code for maximum slicing flexibility before falling back to printer-ready files, including inside ZIP archives
- **STEP files work** — auto-converted to a temporary STL via `gmsh` before slicing, without overwriting or deleting any same-name `.stl` next to the source
- **Default settings work well** — only change slice settings if the user specifically asks
- **Use CLI-managed temp paths** — `download` defaults to the system temp directory, while `job/send` defaults to a private temp work directory only when it needs to download, extract, or slice. Override with `--output <dir>` when the user wants generated files saved somewhere specific. On Windows the literal `/tmp` path does **not** exist, so don't hardcode it.
- **The `--confirm` flag is required** for print, stop, and delete commands

## Troubleshooting
- **Print says success but printer idle?** — Check `status --json` for `print_error` field
- **Bed temp wrong?** — Slice settings set ALL plate types (cool/hot/textured/eng), should always be correct
- **Slice fails with FileNotFoundError?** — Make sure the `--quality` value is valid: draft, standard, high, 0.16, 0.24
- **Slice fails with STEP file?** — Ensure `gmsh` is installed for your platform (`sudo apt install gmsh` or via Homebrew/installers)
- **Upload timeout?** — Large files (>5MB) may take longer. The upload uses FTPS with a 300s timeout
- **Camera snapshot fails?** — The optional BambuP1Streamer image must exist locally as `bambu_p1_streamer`, or `camera_image` must point to the image name in config. This path is intended for P1-class cameras.
- **FTPS/MQTT fails with a certificate error?** — Bambu printers use a self-signed cert. Run `doctor`; it prints the printer's `Printer certificate SHA-256`. Add it to `config.json` as `"cert_fingerprint": "<value>"` to pin the connection (preferred), or set `"insecure_tls": true` to skip verification entirely (last resort).

## Release Validation
Before calling the skill shippable, run the full local release stack from the repository root:

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
If `uv` is unavailable and Python has pip, install `build` with `python3 -m pip install build` and then run `python3 -m build --sdist --wheel --outdir dist`.
Then install the built wheel into a fresh virtual environment and run
`tests/agent_cli_smoke.py` with `BAMBU_CLI` pointed at that environment's
`bambu-cli` executable. The final release gate is the opt-in live-printer smoke
below.
Without `BAMBU_CLI`, `tests/agent_cli_smoke.py` intentionally runs this
checkout's `scripts/bambu.py` so source validation cannot accidentally pass
against a stale installed command from `PATH`.

## Live Release Proof
Use the opt-in live smoke only when the user has a real printer configured and has explicitly approved the source file:
```bash
BAMBU_LIVE_SOURCE=/path/to/ready.3mf python3 <path>/tests/live_printer_smoke.py
```
By default this runs the checkout's `scripts/bambu.py`, so the proof covers the
code being reviewed. Set `BAMBU_CLI` explicitly only when validating an
installed command. This verifies `preflight --strict --json`, doctor, upload-only JSON, and that
the uploaded file appears in `files --json` without starting a print. Use a
uniquely named source file for release proof; the smoke checks `files --json`
before upload and fails if the resulting remote filename already exists,
because that would make the upload proof ambiguous. To verify the real print
ACK, ask the user first, then run:
```bash
BAMBU_LIVE_SOURCE=/path/to/ready.3mf BAMBU_LIVE_PRINT_CONFIRM=1 python3 <path>/tests/live_printer_smoke.py
```
The second form starts the uploaded file. `BAMBU_LIVE_PRINT_CONFIRM` must be an
explicit truthy value (`1`, `true`, `yes`, or `on`); absent, false, or ambiguous
values skip the print. Set `BAMBU_LIVE_CLEANUP=1` to delete the uploaded test
file after upload-only verification; cleanup is skipped after a confirmed print
start, and cleanup independently confirms the deleted file no longer appears in
`files --json`. The live smoke refuses `BAMBU_CLI` commands that include
`--sim`, because simulation is not real-printer proof.
For local `.3mf`, `.gcode`, `.stl`, `.step`, `.stp`, and `.obj` sources, plus
direct URL paths ending in those extensions, the live smoke predicts the remote
filename and rejects a pre-existing match before upload. For ZIP, Printables,
HTML page, or extensionless URL sources where the remote name is not knowable
before work starts, set `BAMBU_LIVE_EXPECT_REMOTE_NAME=<expected-file.3mf>` to
enable the same pre-upload collision check.
