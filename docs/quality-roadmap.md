# Quality status

Where platecli stands on testing and code quality, what CI enforces, and what is
left before `v1.0.0`. This file is for contributors; it stays on GitHub and is
not shipped in the PyPI packages.

How to run the checks yourself is in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Current measurement

Measured on Linux, `main`, 2026-11-26:

| Metric | Value |
|--------|-------|
| Tests | **2947** non-live tests passing (`pytest -m "not live"`; the live printer test is deselected) |
| Coverage | **95.0%** line + branch over the whole `bambu_cli` package; CI coverage floor **92** (`--cov-fail-under=92`) |
| Typing | Full-package `mypy` with `check_untyped_defs`, no excluded modules |
| JSON schemas | 29 files in `docs/schemas/`, generated from `bambu_cli/contracts/`; every `--json` subcommand has one |
| Mutation score | 50.7% on the safety modules, floor 48 (see [mutation-baseline.md](mutation-baseline.md)) |
| Test-only code in production | None: no `@mockable`, no `Mock` / `unittest` / `pytest` imports or branches in `bambu_cli/` |

CI runs on a clean checkout across Linux, macOS and Windows, so its coverage
numbers differ slightly from a local run. The floor sits at the lowest leg
(usually Windows) with a little headroom so the matrix does not flake.

When the floor moves, change `.github/workflows/ci.yml` and this file in the
same commit. `tests/test_docs_consistency.py` fails if the floor here does not
match CI, if the coverage figure here is below the floor, or if the test count
drifts more than 5% from what pytest collects.

## Scoreboard

| Area | Grade | Evidence |
|------|-------|----------|
| Security | **A+** | One fail-closed TLS pin helper for MQTT, FTPS and camera; SSRF guard and Hypothesis adversarial suite in the default run; `bandit` and `pip-audit` blocking. Remaining residuals are listed in [SECURITY.md](../SECURITY.md#known-limitations) |
| Architecture | **A** | Import layers enforced by `scripts/check_layers.py` with no allowlisted edges; `sys.exit` only in `cli.py`; the MQTT facade's exports are frozen by a test |
| Agent JSON | **A+** | Generated schemas, loaded by the contract tests |
| Correctness | **A+** | No known dead flags; property tests on filenames, SSRF, ZIP extraction and temperatures (`tests/test_properties_safety.py`) |
| Typing | **A** | Full-package `mypy` with `check_untyped_defs` |
| Error model | **A+** | Domain code raises `BambuError` / `abort`; `emit_json_error` is `NoReturn` |
| Tests | **A+** | Suite size and coverage above; `protocols/ftps.py` and `netsafety.py` at 100% |
| CI and release | **A+** | Coverage floor, blocking lint, type and security gates, one test command, version single-sourced in `pyproject.toml` |
| Docs | **A+** | Stability policy and support matrix in [api.md](api.md); numbers here pinned by tests |
| Product polish | **A-** | Support matrix published and a hermetic OrcaSlicer stub in the suite; still Beta, no `v1.0.0` |

## Before v1.0.0

Every row above at A or better, plus:

- [ ] Tag `v1.0.0` and move the classifier from Beta to Production/Stable.
- [ ] Golden fixtures for the monitor NDJSON stream (`status_event.json`).
- [ ] Shared TLS / FTP / MQTT fakes in `tests/fakes/` instead of per-file stubs.
- [ ] Run the [live-printer smoke](live-printer-smoke.md) against the release candidate.

Wanted, but not required for 1.0:

- `mypy --strict` across the package (about 1,500 errors today).
- A complexity budget (ruff `C901`) to keep the largest modules from growing.
- Pull the decision logic out of `slicer/output.py`'s `_finalize_slice` so it can
  be asserted directly; that module is the largest pocket of surviving mutants.
- Field-level sync between `docs/api.md` prose and the schemas (presence is
  already tested).
- A scheduled fuzz job for ZIP and URL handling, an SBOM, and an optional
  scheduled live-printer run.

## Coverage policy

Coverage counts `except Exception`, `pass` and `continue` lines; they are not
excluded in `[tool.coverage.report]`. Integration-heavy paths (MQTT ack loops,
FTPS resume, the interactive wizard's TTY handling, the OrcaSlicer process) may
carry `# pragma: no cover` only when the pure decision logic around them has
its own tests. Do not pragma a helper that has no tests.
