# Test backlog

**Planning owner:** [quality-roadmap.md](quality-roadmap.md) (scoreboard, phases, test IDs T0–T6).  
**Threat model residuals:** [SECURITY.md](../SECURITY.md).

This file is a short **remaining-gaps** list only. Refresh after each phase or audit.
Do not treat historical “≥98% coverage” claims as current — see the snapshot below.

## Snapshot (2026-07-17)

| Metric | Current (honest) | A+ / 1.0 target |
|--------|------------------|-----------------|
| Non-live tests collected | **~618** | ≥550 with zero known flakes ✅ size |
| Line/branch coverage (CI) | **~82%** Linux / ~81.9% Windows; **floor 81** | **≥92%** total; optional module floors |
| Typing | Full package mypy + `check_untyped_defs` | keep; optional full `strict` later |
| Error model | `sys.exit` only in `cli.py` | keep |
| `@mockable` / test-awareness | **0** (CI greps) | keep |
| JSON schemas | **19** files under `docs/schemas/` | every `--json` command + monitor goldens |
| Mutation baseline | Pure safety modules; floor **40%** | optional raise after hermetic Orca stub |
| Live printer | Documented opt-in harness | manual pre-release (optional scheduled lab) |
| Product version | **0.1.0** Beta | **v1.0.0** when roadmap §5 is complete |

CI evidence: `.github/workflows/ci.yml` (`--cov-fail-under=81`, blocking ruff/mypy/bandit/pip-audit/purity greps).

## Ground rules for new tests

- Run: `uv run python -m pytest tests/ -q -m "not live"` (and the CI smoke scripts listed in `.github/workflows/ci.yml`).
- Never touch a real printer or the open internet from unit tests. Use `--sim` for CLI-level tests or mock at module seams.
- Patch functions **in the module that calls them** (e.g. `bambu_cli.download.downloader.build_safe_opener`).
- Runtime config: `RuntimeContext` / `settings_ctx` / `config_ctx` — not module globals.
- JSON contracts: assert full payload shapes (`status`, `command`, `failed_step`, `exit_code`, `next_command`); schemas live in `docs/schemas/`.
- Don't add `isinstance(..., Mock)` / `"unittest" in sys.modules` branches to production code.
- Don't reintroduce `@mockable`. Domain code raises `BambuError`/`abort`; `sys.exit` only in `cli.py`.

## Remaining work (priority)

### P0 — Security hardening (product + tests)

Tracked in [SECURITY.md](../SECURITY.md) known limitations:

| Gap | Notes |
|-----|-------|
| Camera Docker bind default | **Done.** Defaults to `127.0.0.1:…` publish; `camera_port` → stream URL parsing fixed; bind-parse tests in place |
| Camera pin soft-fallback | **Done.** Aborts on pin mismatch and on `ssl.SSLError` from the handshake when a pin is configured; no Docker fallthrough in either case; regression tests in `tests/test_camera_cmd.py` |
| Single TLS pin helper | One `verify_cert_fingerprint` used by mqtt/ftps/camera + unit suite |

### P1 — Coverage ratchet & transport residual

| Gap | Notes |
|-----|-------|
| Raise CI floor 81 → 85 → 88 → **92** | Residual: mqtt/ftps pin paths, pool recovery, wizard TTY, Orca process |
| Per-module floors (optional) | mqtt / ftps / netsafety / download / camera |
| Hermetic fake Orca binary | Raises mutation kill rate on `slicer/output._finalize_slice`; slice unit tests less mock-heavy |

### P2 — Contracts & agent surface

| Gap | Notes |
|-----|-------|
| Schemas still thin / missing for | Dedicated `status` success (beyond envelope), `upload`, `files`, `stop`, `setup`; `send` is alias of `job` |
| `docs/api.md` ↔ schemas | Keep hand-written api in sync when fields change (T5.3); optional generate later |
| Monitor NDJSON goldens | `status_event.json` exists; add golden fixtures if not already covered |

### P3 — Suite maintainability & stretch

| Gap | Notes |
|-----|-------|
| Giant unittest-style modules | e.g. `test_printer_commands.py`, `test_download_cmd.py` — split by family over time |
| `tests/fakes/` package | Shared TLS/FTP/MQTT fakes (roadmap A.3) |
| Mutation survivors | Honest ~30–33% on some `predict` / `validation` emit paths; cosmetic/equivalent accepted |
| Phase E | Weekly fuzz (ZIP/URL), SBOM, Dependabot, optional scheduled live lab |
| 1.0 release tag | Support matrix + stability promise already started in api.md |

## Priority if coverage regresses

1. Transport residual (mqtt/ftps pin, pool recovery) — `tests/test_tls_pinning.py`
2. Netsafety redirect / handlers — `tests/test_netsafety.py`, residual A+ tests
3. Schema contracts — `tests/contracts/`, `docs/schemas/`
4. Setup wizard/preflight/migrate — guided + noninteractive tests

## Done enough (do not re-litigate without measurement)

- T0 SSRF basics (`--allow-private-ips` CLI wiring, private IP default deny)
- TLS pin match/mismatch suites for mqtt/ftps/camera direct path
- Error model migration (`abort` / `BambuError`; entry-only `sys.exit`)
- Full-package mypy
- Core schema set for job/slice/download/doctor/config/print/gcode/etc.
- Blocking CI purity greps and bandit/pip-audit

Re-measure with `pytest --cov=bambu_cli` before claiming A+ coverage.
