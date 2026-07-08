# Bambu CLI
Runs on Linux, macOS, and Windows.

**Script:** `python3 <path>/scripts/bambu.py` (legacy path, but installed system-wide as `bambu-cli`)

Prefer `job`/`send` for agent work. Always ask the user before running any command with `--confirm`.

## Data Handling
ZIP files are opened safely. URL downloads and ZIP extraction have a 2048 MB safety limit via `--max-download-mb`. Conflicting files use a numbered sibling such as `model-1.stl`.

Agent-facing JSON path fields compact paths under the current home directory to `~`. Path-bearing JSON error messages use the same `~` compaction.

## Agent Workflows & Client Architecture
The core interaction with the printer is handled via the `BambuPrinter` class located in `bambu_cli/printer.py`. 
Agents interacting directly with the codebase should instantiate this class via the `get_printer()` factory instead of manipulating globals.
- `BambuPrinter` handles FTPS and MQTT connections.
- Set `insecure_tls = False` and supply the `cert_fingerprint` to ensure MITM protection. All TLS channels (MQTT, FTPS, camera port 6000) fail closed when no fingerprint is pinned and `insecure_tls` is not set.
- `doctor` prints the live certificate fingerprint and, in an interactive TTY session with no fingerprint pinned, offers to write `cert_fingerprint` into config.json for you. It never prompts in `--json` mode or non-interactive runs.
- Secret-bearing files are tightened to `0600` automatically: config.json on load, and the `access_code_file` when `load_access_code()` reads it.
- Network operations (like MQTT request-response) support `timeout` and `retries` out of the box through `printer.send_command()` and `printer.status()`.

### Module layout
Logic lives in focused modules; `bambu_cli/bambu.py` is a thin entry point that
holds config-derived runtime state (`SIMULATION_MODE`, `PRINTER_IP`, ...) and
re-exports every helper as a stable compatibility facade for tests and scripts.
- `cli.py` — argparse setup, `main()` dispatch, path/JSON message helpers
- `commands.py` — printer subcommand handlers (status, upload, print, doctor, ...)
- `download/` — package: URL/filename validation, HTML link scraping, ZIP extraction, the `download` command
- `job.py` — one-shot `job`/`send` orchestration, dry-run prediction, print payloads
- `setup_cmd/` — package: guided/non-interactive setup, mDNS discovery, config show/validate, preflight
- `camera.py` — snapshot capture (direct port-6000 grab + Docker streamer fallback)
- `slicer.py` — OrcaSlicer integration; `config.py` — config load/apply, timeouts
- `constants.py` — exit codes, file-type tables, safety limits (immutable)
- `protocols/` — low-level FTPS and MQTT clients used by `BambuPrinter`
New command logic goes in `commands.py` (or a new focused module) using
`get_printer()`; add a re-export in `bambu.py` if tests or agents need to patch it.

When adding tests, follow the conventions and prioritized gap list in
`docs/test-backlog.md` (patch targets, JSON-contract assertions, no new
test-awareness branches in production code).

## Agent Usage
Agents may place `--json` before or after the subcommand; `bambu-cli --json --version` emits machine-readable version details. Slicing accepts meshes in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. AMS slot mappings are zero-or-positive integers. When a slice fails because OrcaSlicer profiles are missing, the `--json` error includes `profiles_dir` (configured) and `detected_profiles_dir` (a real BBL profiles directory found on disk, or null) so the fix is machine-actionable.

## Packaging
Published on PyPI as `bambu-local-cli`; the installed command is `bambu-cli`.
Wheels contain runtime code only — do not add docs or requirements files to
package-data (tests/package_contents_smoke.py enforces this).
