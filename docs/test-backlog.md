# Test backlog

**Planning owner:** [quality-roadmap.md](quality-roadmap.md) (scoreboard, phases, test IDs T0–T6).  
**Threat model residuals:** [SECURITY.md](../SECURITY.md).

This file is a short **remaining-gaps** list only. Refresh after each phase or audit.
Do not treat historical “≥98% coverage” claims as current — see the snapshot below.

## Snapshot (2026-08-05, release commit `5b08720`)

| Metric | Current (honest) | A+ / 1.0 target |
|--------|------------------|-----------------|
| Non-live tests collected | **1499** passing (measured 2026-08-13 on Linux; 1 live test deselected) | ≥550 with zero known flakes ✅ size |
| Line/branch coverage (CI) | **91.0%** branch coverage over 8368 statements (local Linux, 2026-08-13); prior CI matrix on `5b08720` was 88.8–89.3%; **floor 86** | **≥92%** total; optional module floors |
| Typing | Full package mypy + `check_untyped_defs` | keep; optional full `strict` later |
| Error model | `sys.exit` only in `cli.py` | keep |
| `@mockable` / test-awareness | **0** (CI greps) | keep |
| JSON schemas | **27** files under `docs/schemas/`, **generated** from `bambu_cli/contracts/`; coverage is derived from `build_parser()`, so a new subcommand cannot ship schema-less | monitor goldens; field-level api.md ↔ schema sync |
| Mutation baseline | Pure safety modules; score **50.7%** measured 2026-08-04, CI floor raised 40 → **48** | the C.4 re-run happened and **disproved** the prediction: `slicer/output.py` stayed at 21.8% while its line coverage went 79.8% → 92.7%. See [mutation-baseline.md](mutation-baseline.md); raising that row needs a production refactor, not more tests |
| Live printer | Documented opt-in harness | manual pre-release (optional scheduled lab) |
| Product version | pre-1.0 Beta (single-sourced from `pyproject.toml`) | **v1.0.0** when roadmap §5 is complete |

CI evidence: `.github/workflows/ci.yml` (`--cov-fail-under=86`, blocking ruff/mypy/bandit/pip-audit/purity greps).

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
| Camera pin soft-fallback | **Done.** Aborts on pin mismatch and on `ssl.SSLError` from the handshake when a pin is configured; no Docker fallthrough in either case; regression tests in `tests/test_camera_capture.py` / `tests/test_cmd_snapshot.py` |
| Single TLS pin helper | **Done.** One `verify_cert_fingerprint` (`bambu_cli/tlspin.py`, constant-time compare) used by mqtt/ftps/camera; direct unit suite in `tests/test_tlspin.py` + per-transport fail-closed tests |

### P1 — Coverage ratchet & transport residual

| Gap | Notes |
|-----|-------|
| Raise CI floor 86 → 88 → **92** | Residual: mqtt/ftps pin paths, pool recovery, wizard TTY, Orca process |
| Per-module floors (optional) | mqtt / ftps / netsafety / download / camera |
| ~~Hermetic fake Orca binary~~ | **Done (C.4).** `tests/fakes/orca_stub` + `tests/test_slice_stub_integration.py` run `cmd_slice` end-to-end through the real slicer subprocess (`_run_orcaslicer`/`_finalize_slice`); `slicer/output.py` line coverage 79.8%→92.7%. The mutation re-run is **done** (2026-08-04): the module's score did not move (21.8%), so the remaining work there is extracting the pure decision logic out of `_finalize_slice`, not more end-to-end tests. |

### P2 — Contracts & agent surface

| Gap | Notes |
|-----|-------|
| ~~Schemas missing for `upload`/`files`/`stop`/`setup`~~ | **Closed.** All four published; coverage is now derived from `build_parser()` so a new subcommand cannot ship schema-less. `send` intentionally shares `job`'s envelopes |
| `docs/api.md` ↔ schemas | Presence is now guarded (`test_api_doc_lists_every_schema`), but *field-level* drift is not — keep the hand-written prose in sync when fields change (T5.3); optional generate later |
| Monitor NDJSON goldens | `status_event.json` exists; add golden fixtures if not already covered |

### P3 — Suite maintainability & stretch

| Gap | Notes |
|-----|-------|
| Giant unittest-style modules | **Done (2026-08-04).** `test_printer_commands.py` (1333), `test_json_contracts.py` (1031) and `test_camera_cmd.py` (927) split by command surface; largest remaining is `test_job.py` (1246) |
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
