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
Logic lives in focused packages/modules; `bambu_cli/bambu.py` is a **thin entrypoint**
(console script + `main` re-export only — no `__getattr__` facade). Prefer
injecting collaborators (`ctx.printer()`, keyword factory params with real
defaults) over patching module globals. Tests must not patch `bambu_cli.bambu.*`
for implementation symbols. Package `__init__.py` re-exports are for stable
imports (as with `download/` / `setup_cmd/`), **not** mock targets.
- `cli.py` — argparse setup, `main()` dispatch, path/JSON message helpers
- `commands/` — printer subcommand handlers by family (`status`, `device`, `files`, `print_cmd`, `doctor`, `gcode`, thin `setup_wrappers`)
- `download/` — URL/filename validation, HTML link scraping, ZIP extraction, the `download` command. Collaborators (`opener_factory`, `resolve_printables`, `noncolliding_path`) are injectable
- `job/` — one-shot `job`/`send` orchestration (`orchestrate`), dry-run prediction (`predict`), print payloads (`payload`), injectable `JobSteps` (`steps`)
- `setup_cmd/` — guided/non-interactive setup, mDNS discovery, config show/validate, preflight
- `camera.py` — snapshot capture (injectable grab_frame / docker runners)
- `slicer/` — OrcaSlicer integration (`options`, `profiles`, `step_convert`, `orca`, `output`, `cmd`)
- `config.py` — config load/apply, timeouts
- `logging_utils.py` — process logger proxy; tests use `set_logger` / patch `_BACKEND`
- `constants.py` — exit codes, file-type tables, safety limits (immutable)
- `protocols/` — low-level FTPS and MQTT clients used by `BambuPrinter`

**Package inventory is derived:** setuptools finds `bambu_cli*`; syntax smoke and
CLI help smoke auto-discover modules/commands (`scripts/syntax_smoke.py`,
`scripts/cli_help_smoke.py`). Adding a module under `bambu_cli/` or a subcommand
in `cli.py` is enough — no triplicated lists.

**Typing (mypy):** CI runs `uvx mypy -p bambu_cli` over the whole package. Scope
is configured in `pyproject.toml` as a **blocklist** (`[tool.mypy].exclude`):
only residual modules (`printer.py`, `slicer/`) are skipped until typed. A new
module under `bambu_cli/` is checked automatically — do not maintain a CI file
allowlist.

New command logic goes in `commands/` (or a new focused package) using
`get_printer()` / `RuntimeContext` and injectable collaborators.

When adding tests, follow the conventions and prioritized gap list in
`docs/test-backlog.md` (inject collaborators, JSON-contract assertions, no new
test-awareness branches in production code).

## Agent Usage
Agents may place `--json` before or after the subcommand; `bambu-cli --json --version` emits machine-readable version details. Slicing accepts meshes in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. AMS slot mappings are zero-or-positive integers. When a slice fails because OrcaSlicer profiles are missing, the `--json` error includes `profiles_dir` (configured) and `detected_profiles_dir` (a real BBL profiles directory found on disk, or null) so the fix is machine-actionable.

## Packaging
Published on PyPI as `bambu-local-cli`; the installed command is `bambu-cli`.
Wheels contain runtime code only — do not add docs or requirements files to
package-data (tests/package_contents_smoke.py enforces this).

## Quality gates (agents)

- **Default tests:** `uv run python -m pytest tests/ -q -m "not live"` — never
  contacts a printer.
- **Mutation baseline** (safety pure modules): `./scripts/run_mutation_baseline.sh`
  — not a per-PR job; nightly / `workflow_dispatch` only. Scope, score floor, and
  surviving mutants: [docs/mutation-baseline.md](docs/mutation-baseline.md).
- **Live-printer pre-release smoke** (opt-in): requires `BAMBU_LIVE=1`, a real
  config, and `BAMBU_LIVE_SOURCE`. Marked `@pytest.mark.live`. Full procedure and
  safety notes: [docs/live-printer-smoke.md](docs/live-printer-smoke.md). Always
  ask the user before any run with `--confirm` or `BAMBU_LIVE_PRINT_CONFIRM`.
