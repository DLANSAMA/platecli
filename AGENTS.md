# Bambu CLI

Runs on Linux, macOS, and Windows.

**Script:** `python3 <path>/scripts/bambu.py` (legacy path; installed command is `bambu-cli`)

Prefer `job`/`send` for agent work. Always ask the user before running any command with `--confirm`.

## Data handling

ZIP files are opened safely. URL downloads and ZIP extraction have a 2048 MB safety limit via `--max-download-mb`. Conflicting files use a numbered sibling such as `model-1.stl`.

Agent-facing JSON path fields compact paths under the current home directory to `~`. Path-bearing JSON error messages use the same `~` compaction.

## Agent workflows and client architecture

Core printer interaction is `BambuPrinter` in `bambu_cli/printer.py`. Agents and library users should instantiate it via the `get_printer()` factory (or `RuntimeContext.printer()`), not by manipulating globals.

- `BambuPrinter` handles FTPS and MQTT.
- Set `insecure_tls = False` and supply `cert_fingerprint` for MITM protection. Camera TLS (port 6000): the **direct grab** fails closed on a pin mismatch or on an `ssl.SSLError` during the handshake whenever a pin is configured, and without a pin it still refuses to send the access code over the direct connection — but `snapshot` then falls through to the Docker/RTSP streamer path, which has **no TLS verification** of its own; see [SECURITY.md](SECURITY.md) for the residual. MQTT/FTPS without a pin use system CA verification (`CERT_REQUIRED`), which fails for typical Bambu self-signed certs — still effectively fail-closed, but prefer an explicit pin. Pin match/mismatch is enforced when a fingerprint is configured.
- `doctor` prints the live certificate fingerprint and, in an interactive TTY with no pin, may offer to write `cert_fingerprint` into config.json. It never prompts in `--json` mode or non-interactive runs.
- Secret-bearing files are tightened to `0600` automatically on POSIX: config.json on load, and the `access_code_file` when `load_access_code()` reads it. Windows relies on NTFS ACLs (see [SECURITY.md](SECURITY.md)).
- Network operations support `timeout` and `retries` through `printer.send_command()` and `printer.status()`.

### Module layout

Logic lives in focused packages; `bambu_cli/bambu.py` is a **thin entrypoint** (console script + `main` re-export only — no `__getattr__` facade). Prefer injecting collaborators (`ctx.printer()`, keyword factory params with real defaults) over patching module globals. Tests must not patch `bambu_cli.bambu.*` for implementation symbols. Package `__init__.py` re-exports are for stable imports (as with `download/` / `setup_cmd/`), **not** mock targets.

| Module / package | Role |
|------------------|------|
| `cli.py` | argparse, `main()` dispatch, path/JSON message helpers (also imported by domain — see tech debt below) |
| `commands/` | Printer subcommand handlers (`status`, `device`, `files`, `print_cmd`, `doctor`, `gcode`, thin `setup_wrappers`) |
| `download/` | URL/filename validation, HTML scraping, ZIP extraction, `download` command |
| `job/` | One-shot `job`/`send` orchestration, dry-run predict, print payloads, injectable `JobSteps` |
| `setup_cmd/` | Guided/non-interactive setup, mDNS, config show/validate, preflight |
| `camera.py` | Snapshot capture (injectable grab_frame / docker runners) |
| `slicer/` | OrcaSlicer integration |
| `config.py` | Config load/apply, timeouts, fingerprints |
| `context.py` | `Settings` / `RuntimeContext` process context |
| `logging_utils.py` | Process logger proxy; tests use `set_logger` / patch `_BACKEND` |
| `constants.py` | Exit codes, file-type tables, safety limits (immutable) |
| `protocols/` | Low-level FTPS and MQTT clients used by `BambuPrinter` |
| `errors.py` | `BambuError` hierarchy + `abort()` (domain never calls `sys.exit`) |

**Package inventory is derived:** setuptools finds `bambu_cli*`; syntax smoke and CLI help smoke auto-discover modules/commands (`scripts/syntax_smoke.py`, `scripts/cli_help_smoke.py`). Adding a module under `bambu_cli/` or a subcommand in `cli.py` is enough — no triplicated lists.

**Typing (mypy):** CI runs `uvx mypy -p bambu_cli` over the **whole package** with `check_untyped_defs = true`. There is **no residual exclude blocklist** — `printer.py` and `slicer/` are included. New modules are type-checked automatically.

New command logic goes in `commands/` (or a new focused package) using `get_printer()` / `RuntimeContext` and injectable collaborators.

When adding tests, follow [docs/test-backlog.md](docs/test-backlog.md) and the quality plan in [docs/quality-roadmap.md](docs/quality-roadmap.md) (inject collaborators, JSON-contract assertions, no new test-awareness branches in production code).

### Known architecture debt (honest)

- Domain modules still import private helpers from `bambu_cli.cli` (`_expand_path`, `_namespace_get`, path/JSON helpers). Target: extract `paths` / `jsonio` / `argutils` (roadmap Phase B.4).
- TLS fingerprint verification is reimplemented in mqtt, ftps, and camera rather than one shared helper (roadmap B.5).

## Agent usage

Agents may place `--json` before or after the subcommand; `bambu-cli --json --version` emits machine-readable version details. Slicing accepts meshes in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. AMS slot mappings are zero-or-positive integers. When a slice fails because OrcaSlicer profiles are missing, the `--json` error includes `profiles_dir` (configured) and `detected_profiles_dir` (a real BBL profiles directory found on disk, or null) so the fix is machine-actionable.

JSON contracts: human reference [docs/api.md](docs/api.md); machine schemas in [docs/schemas/](docs/schemas/).

## Packaging

Published on PyPI as `bambu-local-cli`; the installed command is `bambu-cli`.

| Artifact | Contents |
|----------|----------|
| **Wheel** | Runtime `bambu_cli` package only — no docs, scripts, or tests |
| **Sdist** | Runtime + tests/scripts + **ship docs**: `README.md`, `AGENTS.md`, `SECURITY.md`, `CHANGELOG.md`, `docs/api.md`, `docs/schemas/*` |

**Repo-only (never in sdist/wheel):** `CONTRIBUTING.md`, `docs/quality-roadmap.md`, `docs/test-backlog.md`, `docs/mutation-baseline.md`, `docs/live-printer-smoke.md`, and local `CLAUDE.md`. Enforced by `MANIFEST.in` + `tests/package_contents_smoke.py`.

## Quality gates (agents)

| Gate | Command / note |
|------|----------------|
| Default tests | `uv run python -m pytest tests/ -q -m "not live"` — never contacts a printer |
| Coverage (CI) | `--cov-fail-under=81` (measured ~82%; A+ target **92%** — see roadmap) |
| Lint | `uvx ruff check bambu_cli` + `uvx ruff format --check bambu_cli` |
| Types | `uvx mypy -p bambu_cli` |
| Security lint | `uvx bandit -c pyproject.toml -r bambu_cli -ll` |
| Mutation baseline | `./scripts/run_mutation_baseline.sh` — nightly / `workflow_dispatch` only; [docs/mutation-baseline.md](docs/mutation-baseline.md) |
| Live printer | Opt-in only: `BAMBU_LIVE=1` + real config + `BAMBU_LIVE_SOURCE`. [docs/live-printer-smoke.md](docs/live-printer-smoke.md). Always ask the user before `--confirm` or `BAMBU_LIVE_PRINT_CONFIRM`. |

**Truth sources for quality status:** [docs/quality-roadmap.md](docs/quality-roadmap.md) (scoreboard) and [docs/test-backlog.md](docs/test-backlog.md) (remaining gaps). Prefer those over older blog-style claims.

## Security (agent checklist)

Full threat model: [SECURITY.md](SECURITY.md).

- Prefer `cert_fingerprint` + never enable `insecure_tls` unless the user insists.
- Prefer `access_code_file` over inline `access_code`.
- Downloads block private/loopback targets unless `--allow-private-ips` (CLI-only, not sticky config).
- Destructive actions need `--confirm`: print, job print step, stop, delete, gcode. Pause/resume do **not** currently require it.
- Camera Docker streamer (when used) publishes via `camera_port`, now loopback-only by default (`127.0.0.1:1985:1984`); the feed is unauthenticated, so only expose it on the LAN (`0.0.0.0:...`) deliberately (see SECURITY.md).
