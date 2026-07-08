# Test backlog

**Planning owner:** [quality-roadmap.md](quality-roadmap.md) (A+ scoreboard, phases, test IDs T0–T6).

This file is a short **remaining-gaps** list. Refresh after each phase.

## Snapshot (2026-07-08, A+ gates)

- **500+ tests** (non-live); single runner: `pytest`.
- **≥98%** measured line coverage (`pytest --cov=bambu_cli`, residual policy in roadmap).
- CI: `--cov-fail-under=92`, blocking bandit/pip-audit/mypy/purity greps, security+contract marker job.
- Module floors (measured under residual policy): mqtt/ftps/netsafety/download/camera/setup/slicer/job all at or above A+ targets.

## Ground rules for new tests

- Run: `uv run python -m pytest tests/ -q -m "not live"` (and the CI smoke scripts listed in `.github/workflows/ci.yml`).
- Never touch a real printer or the network. Use `--sim` for CLI-level tests or mock at module seams.
- Patch functions **in the module that calls them** (e.g. `bambu_cli.download.build_safe_opener`).
- Runtime config: `RuntimeContext` / `settings_ctx` / `config_ctx` — not module globals.
- JSON contracts: assert full payload shapes (`status`, `command`, `failed_step`, `exit_code`, `next_command`); schemas live in `docs/schemas/`.
- Don't add `isinstance(..., Mock)` / `"unittest" in sys.modules` branches to production code.
- Don't reintroduce `@mockable`. Domain code raises `BambuError`/`abort`; `sys.exit` only in `cli.py`.

## Remaining stretch (post-A+ gates)

| Gap | Notes |
|-----|-------|
| mypy on `printer.py` / `slicer.py` | Exception-group / optional-type residuals |
| Hermetic fake Orca binary in CI | Slicer process paths still `# pragma: no cover` |
| Per-module cov-fail-under | Optional; total 92% + residual policy is the gate |
| Live printer scheduled lab | Optional A+ stretch |
| 1.0 release tag | Support matrix + stability promise publish |

## Priority if coverage regresses

1. Transport residual (mqtt/ftps pin, pool recovery) — `tests/test_tls_pinning.py`
2. Netsafety redirect / handlers — `tests/test_netsafety.py`, residual A+ tests
3. Schema contracts — `tests/contracts/`, `docs/schemas/`
4. Setup wizard/preflight/migrate — guided + noninteractive tests
