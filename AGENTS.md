# platecli — agent guide

Runs on Linux, macOS, and Windows.

**Command:** `plate` (installed via pipx/pip/uv). From a source checkout, without installing: `python3 <repo>/scripts/bambu.py` — that runs the working tree, not whatever `plate` resolves to on `PATH`.

> **Confirm which build you are running before reporting behaviour.** An installed `plate` is frequently an *older release* than the checkout you are reading, and the two can disagree — an unreleased fix changes which validation fires first, so the same command can exit `3` on the release and `5` on source. Tell them apart with `plate --version`: unreleased builds carry a `.devN` suffix, released ones do not. When behaviour contradicts this guide, re-run through `scripts/bambu.py` before filing a bug.

**Reading `--json`:** the JSON envelope is written to **stdout**; Rich logging, progress, and `-v` diagnostics go to **stderr**. Parse stdout only — do not merge the streams, or log lines will corrupt the payload.

**Model fixture:** `tests/fixtures/cube.stl` (a 20 mm cube) is the one model committed to the repo — `*.stl` / `*.3mf` are otherwise gitignored. Use it to exercise `job` / `print` instead of creating a stray model file.

Prefer `job`/`send` for agent work. Always ask the user before running any command with `--confirm`.

**Two subcommands are for humans only and have no machine contract:** `go` (the guided wizard) and `tui` (the full-screen Textual UI). Both refuse `--json` and a non-TTY stdin, exiting `5` with the standard error envelope — do not try to drive or scrape them. `plate job <url> --confirm` is the machine path and does everything they do.

## Data handling

ZIP files are opened safely. URL downloads and ZIP extraction have a 2048 MB safety limit via `--max-download-mb`. Conflicting files use a numbered sibling such as `model-1.stl`.

Agent-facing JSON path fields compact paths under the current home directory to `~`, and always use `/` separators (on Windows too). Path-bearing JSON error messages use the same `~` compaction.

## Agent workflows and client architecture

Core printer interaction is `BambuPrinter` in `bambu_cli/printer.py`. Agents and library users should instantiate it via the `get_printer()` factory (or `RuntimeContext.printer()`), not by manipulating globals.

- `BambuPrinter` handles FTPS and MQTT.
- Set `insecure_tls = False` and supply `cert_fingerprint` for MITM protection. Camera TLS (port 6000): the **direct grab** fails closed on a pin mismatch or on an `ssl.SSLError` during the handshake whenever a pin is configured, and without a pin it still refuses to send the access code over the direct connection. The Docker/RTSP streamer (no TLS verification of its own) is **opt-in** via `camera_allow_streamer` or `--allow-camera-streamer`; the default is to abort. See [SECURITY.md](SECURITY.md). MQTT/FTPS without a pin use system CA verification (`CERT_REQUIRED`), which fails for typical Bambu self-signed certs — still effectively fail-closed, but prefer an explicit pin. Pin match/mismatch is enforced when a fingerprint is configured.
- `doctor` prints the live certificate fingerprint only when it is not yet pinned (or with `-v`); once pinned it prints a hex-free match confirmation, and the printer's LAN IP is redacted from human output unless `-v` is passed. `--json` always carries `certificate_fingerprint`. In an interactive TTY with no pin, doctor may offer to write `cert_fingerprint` into config.json. It never prompts in `--json` mode or non-interactive runs.
- Secret-bearing files are tightened to `0600` automatically on POSIX: config.json on load, and the `access_code_file` when `load_access_code()` reads it. Windows relies on NTFS ACLs (see [SECURITY.md](SECURITY.md)).
- Network operations support `timeout` and `retries` through `printer.send_command()` and `printer.status()`.
- `printer.status()` returns a **complete** state snapshot, never a partial MQTT delta: the printer streams incremental updates on its report topic and only answers `pushall` with the whole state, so report messages are merged and the wait continues until `gcode_state`, `mc_percent`, `bed_temper`, and `nozzle_temper` are all present. If only deltas arrive it raises `PrinterStatusIncomplete` (exit `6`) instead of returning a partial — so `plate --json status` never emits a `printer` object missing `gcode_state`. Pass `require_complete=False` for liveness probes that only need to know MQTT works (this is what `doctor` and `print --dry-run` do).
- A long-lived process can call `printer.hold_mqtt()` so `status` / `send_command` / `get_version` reuse one TLS session. The TUI does this: dashboard and monitor refreshes must not open a new connection every few seconds. One-shot CLI commands still connect and tear down. Always pair with `release_mqtt()` so the paho loop thread does not leak (CI runs with `-W error::ResourceWarning`).

### Module layout

Logic lives in focused packages; `bambu_cli/bambu.py` is a **thin entrypoint** (console script + `main` re-export only — no `__getattr__` facade). Prefer injecting collaborators (`ctx.printer()`, keyword factory params with real defaults) over patching module globals. Tests must not patch `bambu_cli.bambu.*` for implementation symbols. Package `__init__.py` re-exports are for stable imports (as with `download/` / `setup_cmd/`), **not** mock targets.

| Module / package | Role |
|------------------|------|
| `bambu.py` | Thin entrypoint: the `plate` console script + a `main` re-export. Nothing else belongs here |
| `printer.py` | `BambuPrinter` — the transport facade over `protocols/` (FTPS + MQTT); build it via `get_printer()` / `RuntimeContext.printer()` |
| `cli.py` | `main()` dispatch and the **only** module holding `sys.exit`; re-exports `build_parser` from `cliparse` |
| `cliparse.py` | The argparse tree (`build_parser`, `get_global_parser`, `JsonArgumentParser`). Split from `cli.py` so domain code can build a namespace without importing the entrypoint |
| `paths.py` | Filesystem path helpers (`expand_path`, `display_path`, `path_for_message`, `exception_for_message`) shared by CLI and domain |
| `fsutil.py` | Pure path/file mechanics (`_portable_basename`, `_remove_partial_file`, `_download_partial_path`, `_noncolliding_path`) shared by slicer/download without either importing the other |
| `jsonio.py` | JSON-mode detection + URL-credential redaction for logs/JSON output |
| `argutils.py` | argparse/`Namespace` coercion helpers (`namespace_get`, `exit_code_from_system_exit`, `setup_args_provided`) |
| `commands/` | Printer subcommand handlers (`status`, `device`, `files`, `print_cmd`, `doctor`, `gcode`, thin `setup_wrappers`) |
| `download/` | URL/filename validation, HTML scraping, ZIP extraction, `download` command |
| `printables/` | Printables.com integration behind a strict adapter. `client.py` (the undocumented GraphQL wire format) is **sealed** — import only from `bambu_cli.printables`. `adapter.py` guarantees no Printables failure escapes as an exception |
| `contracts/` | Typed `--json` payload shapes (frozen dataclasses). **Generates `docs/schemas/*.json`** via `scripts/gen_schemas.py`; do not hand-edit a schema |
| `job/` | One-shot `job`/`send` orchestration, dry-run predict, print payloads, injectable `JobSteps` |
| `setup_cmd/` | Guided/non-interactive setup, mDNS, config show/validate, preflight |
| `slicer/` | OrcaSlicer integration |
| `interactive/` | Shared wizard core (`core.py`: `GoSteps`, presets, override validation) + the `plate go` session. Both interactive front-ends inject at this one seam |
| `tui/` | Textual full-screen UI (`plate tui`), optional `[tui]` extra. A front-end over `interactive/core.py` — **not** an agent surface (see "human only" above) |
| `tlspin.py` | The single `verify_cert_fingerprint` used by mqtt, ftps, and camera (fail-closed; B.5) |
| `netsafety.py` | SSRF / private-IP guards for download targets |
| `printables.py` | Printables model resolution (GraphQL + HTML fallback) |
| `ams.py` | AMS tray parsing and material matching |
| `utils.py` | `emit_json` / `emit_json_error` envelopes and shared output helpers |
| `config.py` | Config load/apply, timeouts, fingerprints |
| `context.py` | `Settings` / `RuntimeContext` process context |
| `logging_utils.py` | Process logger proxy; tests use `set_logger` / patch `_BACKEND` |
| `constants.py` | Exit codes, file-type tables, safety limits (immutable) |
| `protocols/` | Low-level FTPS, MQTT, and camera clients used by `BambuPrinter` (`camera.py` moved here — it is a TLS transport sharing `tlspin`) |
| `errors.py` | `BambuError` hierarchy + `abort()` (domain never calls `sys.exit`) |

**Layer boundaries are enforced (blocking CI):** `scripts/check_layers.py` assigns every module a rank and rejects any import that goes *upward*, plus any import between the three sibling adapters. Deferred (function-local) imports count — they break the import cycle, not the dependency.

```
70  cli.py (sys.exit lives here)          80  bambu.py (__main__ shim)
50  commands/  interactive/  tui/         45  job/       40  download/  setup_cmd/
35  printer.py                            30  protocols/ | slicer/ | printables/   <- MUST NOT import each other
25  cliparse.py                           20  utils config context netsafety ams
10  constants errors paths logging_utils argutils jsonio tlspin fsutil
```

The rule exists because directories alone never held it: `protocols/`, `slicer/` and `download/` were already separate packages and still drifted — `slicer/output.py` imported a **private FTPS helper** to delete a partial file, so a change to Bambu transport code silently changed slicer behavior. If you need a helper in two adapters, push it down to rank 10 (that is what `fsutil.py` is for); do not import sideways.

Accepted debt lives in `ALLOWED` in that script, each entry with a reason. Shrink it; do not grow it. There are currently no allowlisted edges: `RuntimeContext.printer()` uses an injectable factory registered downward from `bambu_cli.printer`.

The same script also enforces `SEALED` — package internals no outside module may import. `bambu_cli.printables.client` is sealed because an adapter is only a sandbox if callers cannot reach past it. **Third-party integrations go behind an adapter that cannot raise:** `PrintablesAdapter.resolve()` returns a `PrintablesResolution` for every outcome, converting a renamed field or a redesigned error envelope into a typed `printables_contract_changed` result instead of a traceback in the middle of `plate job`. `KeyboardInterrupt`/`SystemExit` are deliberately the only things that still propagate.

**JSON schemas are generated, never hand-written.** `docs/schemas/*.json` comes from the dataclasses in `bambu_cli/contracts/`:

```bash
python scripts/gen_schemas.py            # regenerate after changing a payload
python scripts/gen_schemas.py --check    # what CI runs (blocking)
```

Editing a schema by hand will be overwritten and will red CI. Change the model, regenerate, commit both. The gate fails in *both* directions — a stale schema, and a schema with no contract behind it.

**Pydantic is a dev/build dependency only** (`[test]` extra, `python_version >= '3.10'`). `bambu_cli` never imports it, and a test asserts that. Serialization stays in `emit_json`, because that pass applies the credential redaction a `model_dump_json()` would bypass. The contracts annotate optionals as `X | None`, which only *evaluates* on 3.10+ — safe because nothing at runtime resolves those annotations (also asserted by a test). Only the generator does, and it refuses to run below 3.10 with an explanatory message.

**Package inventory is derived:** setuptools finds `bambu_cli*`; syntax smoke and CLI help smoke auto-discover modules/commands (`scripts/syntax_smoke.py`, `scripts/cli_help_smoke.py`). Adding a module under `bambu_cli/` or a subcommand in `cli.py` is enough — no triplicated lists.

**Typing (mypy):** CI runs `uvx mypy@<pinned> -p bambu_cli` over the **whole package** with `check_untyped_defs = true` (CI pins the tool version in `.github/workflows/ci.yml`; running it unpinned locally is fine). There is **no residual exclude blocklist** — `printer.py` and `slicer/` are included. New modules are type-checked automatically.

New command logic goes in `commands/` (or a new focused package) using `get_printer()` / `RuntimeContext` and injectable collaborators.

When adding tests, follow [docs/test-backlog.md](docs/test-backlog.md) and the quality plan in [docs/quality-roadmap.md](docs/quality-roadmap.md) (inject collaborators, JSON-contract assertions, no new test-awareness branches in production code).

### Known architecture debt (honest)

- **`protocols/mqtt.py` is a facade** over `mqtt_tls` / `mqtt_cmd` / `mqtt_print` / `mqtt_monitor` / `mqtt_session`. The old ~880 LOC hotspot was split; keep new MQTT logic in those siblings, not the facade.
- B.4 (cli extraction → paths/jsonio/argutils) and B.5 (single `verify_cert_fingerprint` in tlspin.py) both landed; see [docs/quality-roadmap.md](docs/quality-roadmap.md) for the current gap list.

## Camera snapshots for agents

`plate snapshot` captures a JPEG from the printer camera. To avoid stale-photo mistakes — where an agent re-sends a cached file instead of a fresh capture — always pass a fresh `--output` name or use `--unique` (generates `printer_snapshot_<UTC>Z.jpg`). Every successful `--json` response includes `sha256` (hex digest of the JPEG bytes) and `captured_at` (ISO-8601 UTC); compare these fields before sending the image to a user to verify the capture is genuinely new. Do not pass `--allow-camera-streamer` unless the user asked: that path has no TLS pin.

## Agent usage

**OrcaSlicer missing?** Slicing needs it. When no install exists anywhere, the `orca-slicer` / `profiles-dir` errors from `preflight` and `slice` carry a ready-to-run install command for the host platform (`winget install --id SoftFever.OrcaSlicer` on Windows, `brew install --cask orcaslicer` on macOS, `flatpak install -y flathub com.orcaslicer.OrcaSlicer` on Linux). Run it, then `plate setup` — auto-detection writes both paths. When an install *is* present but the configured path is stale, the same errors instead name the detected path (and `slice --json` carries `detected_orca_slicer` / `detected_profiles_dir`), so prefer that over installing again.

Agents may place `--json` before or after the subcommand; `plate --json --version` emits machine-readable version details. Slicing accepts meshes in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. AMS slot mappings are zero-or-positive integers. When a slice fails because OrcaSlicer profiles are missing, the `--json` error includes `profiles_dir` (configured) and `detected_profiles_dir` (a real BBL profiles directory found on disk, or null) so the fix is machine-actionable.

JSON contracts: human reference [docs/api.md](docs/api.md); machine schemas in [docs/schemas/](docs/schemas/).

## Packaging

Published on PyPI as `platecli`; the installed command is `plate`.

| Artifact | Contents |
|----------|----------|
| **Wheel** | Runtime `bambu_cli` package only — no docs, scripts, or tests |
| **Sdist** | Runtime + tests/scripts + **ship docs**: `README.md`, `AGENTS.md`, `SECURITY.md`, `CHANGELOG.md`, `docs/api.md`, `docs/manual.md`, `docs/troubleshooting.md`, `docs/schemas/*` |

**Repo-only (never in sdist/wheel):** `CONTRIBUTING.md`, `docs/quality-roadmap.md`, `docs/test-backlog.md`, `docs/mutation-baseline.md`, `docs/live-printer-smoke.md`, `docs/releasing.md`, `docs/README.md`, `docs/plans/*`, and local agent notes (not in repo). `MANIFEST.in` ships only the files it lists, and `tests/package_contents_smoke.py` additionally asserts the first six are absent from the sdist (`FORBIDDEN_SDIST_FILES`).

## Quality gates (agents)

| Gate | Command / note |
|------|----------------|
| Default tests | `uv run python -m pytest tests/ -q -m "not live"` — never contacts a printer |
| Coverage (CI) | `--cov-fail-under=86` (2026-08-13, PR #119: Linux 90.99% / Windows 90.68% / macOS passing; matrix 3.10/3.12/3.14; A+ target **92%** — see roadmap) |
| Lint | `uvx ruff check bambu_cli` + `uvx ruff format --check bambu_cli` |
| Types | `uvx mypy -p bambu_cli` |
| Security lint | `uvx bandit -c pyproject.toml -r bambu_cli -ll` |
| Dependency audit | `pip-audit` over the exported lockfile (blocking high+; CI-only step) |
| Contracts & layers | `python scripts/gen_schemas.py --check` and `python scripts/check_layers.py` — both blocking in the same lint job |
| Smokes (not pytest) | `syntax`, `cli_help`, `ci_workflow`, `python_compat`, `dependency_resolution`, `release_readiness`, `privacy`, `agent_cli` — see [CONTRIBUTING.md](CONTRIBUTING.md). A green pytest says nothing about these. |
| Mutation baseline | `./scripts/run_mutation_baseline.sh` — nightly / `workflow_dispatch` only; [docs/mutation-baseline.md](docs/mutation-baseline.md) |
| Live printer | Opt-in only: `BAMBU_LIVE=1` + real config + `BAMBU_LIVE_SOURCE`. [docs/live-printer-smoke.md](docs/live-printer-smoke.md). Always ask the user before `--confirm` or `BAMBU_LIVE_PRINT_CONFIRM`. |

CI pins these tool versions (see `.github/workflows/ci.yml`); running them unpinned locally is fine.
The lint job runs the smokes and the type/security gates **separately from pytest** —
a passing test suite is not evidence any of them are green.

**Truth sources for quality status:** [docs/quality-roadmap.md](docs/quality-roadmap.md) (scoreboard) and [docs/test-backlog.md](docs/test-backlog.md) (remaining gaps). Prefer those over older blog-style claims.

## Security (agent checklist)

Full threat model: [SECURITY.md](SECURITY.md).

- Prefer `cert_fingerprint` + never enable `insecure_tls` unless the user insists.
- Prefer `access_code_file` over inline `access_code`.
- Downloads block private/loopback targets unless `--allow-private-ips` (CLI-only, not sticky config).
- Destructive/physical actions need `--confirm` and exit `5` without it: `print`, `stop`, `pause`, `resume`, `delete`, `gcode`. `job` / `send` without `--confirm` still uploads and exits `0` with `"status": "uploaded_not_printed"` — only the print step is withheld. `light` is deliberately exempt (no motion/thermal/material effect). `--confirm` is a deliberate-action gate, not an authorization boundary — anything that can run `plate` can pass it.
- Camera Docker streamer (when used) publishes via `camera_port`, now loopback-only by default (`127.0.0.1:1985:1984`); the feed is unauthenticated, so only expose it on the LAN (`0.0.0.0:...`) deliberately (see SECURITY.md).
