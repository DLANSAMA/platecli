# Contributing

Thanks for your interest in improving bambu-local-cli!

## Setup

```bash
git clone https://github.com/DLANSAMA/bambu-local-cli
cd bambu-local-cli   # or bambu-cli if that is your local directory name
uv sync              # or: pip install -e ".[test]"
```

## Running tests

```bash
# Unit + contract suite (no printer, no live network to a real machine)
uv run python -m pytest tests/ -q -m "not live"

# Match CI hardness (ResourceWarning as error + coverage floor)
uv run python -W error::ResourceWarning -m pytest tests/ -m "not live" \
  --cov=bambu_cli --cov-report=term-missing --cov-fail-under=81

# Smokes used in CI (see .github/workflows/ci.yml)
uv run python tests/privacy_smoke.py
uv run python tests/ci_workflow_smoke.py
python scripts/syntax_smoke.py
python scripts/cli_help_smoke.py
```

- **No printer** is required for the default suite: use simulation (`--sim`) or mocks.
- **Live pre-release harness** (`tests/live_printer_smoke.py`): opt-in only
  (`BAMBU_LIVE=1` + real config + `BAMBU_LIVE_SOURCE`). Marked `live` so CI's
  `-m "not live"` never runs it. See [docs/live-printer-smoke.md](docs/live-printer-smoke.md).
  Never run against a printer mid-print; print start needs `BAMBU_LIVE_PRINT_CONFIRM`.
- **Mutation baseline** (safety modules): `./scripts/run_mutation_baseline.sh` —
  [docs/mutation-baseline.md](docs/mutation-baseline.md). Nightly CI only, not every PR.
- CI enforces a "no test-awareness in production code" rule: production modules must not
  branch on `Mock` / `unittest in sys.modules`. Prefer injecting collaborators over
  patching module globals. Domain code raises `BambuError` / `abort`; `sys.exit` only in
  `bambu_cli/cli.py`.

## Lint and types (blocking in CI)

```bash
uvx ruff check bambu_cli
uvx ruff format --check bambu_cli
uvx mypy -p bambu_cli          # full package; check_untyped_defs; no residual excludes
uvx bandit -c pyproject.toml -r bambu_cli -ll
# pip-audit is also blocking in CI (dependency high/critical)
```

A green `pytest` does **not** mean lint/types/security gates are green.

## Quality roadmap

Phased plan and **honest scoreboard** (GitHub checkout only — not in the PyPI sdist):
**[docs/quality-roadmap.md](docs/quality-roadmap.md)**.  
Remaining gaps: **[docs/test-backlog.md](docs/test-backlog.md)**.  
Agent/runtime rules: **[AGENTS.md](AGENTS.md)** (ships in sdist).  
Threat model: **[SECURITY.md](SECURITY.md)** (ships in sdist).  
JSON contracts: **[docs/api.md](docs/api.md)** + **[docs/schemas/](docs/schemas/)** (ship in sdist).

As of the 2026-07 codebase audit: overall **solid A− / A**. Main gaps to A+ / 1.0 are
coverage (~82% vs target 92%), domain→`cli` helper extraction, single-sourced TLS pin
verification, remaining JSON schemas, and a few camera-hardening items documented in
SECURITY.md.

## Code conventions

- New command logic goes in `bambu_cli/commands/` (or a new focused package) using
  `get_printer()` / `RuntimeContext` — do not grow `bambu_cli/bambu.py` beyond the thin entrypoint.
- Prefer dependency injection over patching module globals (see `download/` for the pattern).
- JSON success and error payloads: assert full shapes (`status`, `command`, `failed_step`,
  `exit_code`, `next_command` where applicable); add or extend a schema under `docs/schemas/`
  when introducing agent-facing fields.
- Follow `docs/quality-roadmap.md` and `docs/test-backlog.md` when adding tests.
- Do not add Claude-Session or similar trailers to commits or PRs.

## Releases (maintainers)

1. Update **`version` only in `pyproject.toml`** (runtime `bambu_cli.constants.VERSION`
   resolves from package metadata / that file). Move `CHANGELOG.md` entries from Unreleased
   to the new version.
2. Tag: `git tag vX.Y.Z && git push --tags`.
3. The Release workflow builds, creates a GitHub release, and publishes to PyPI via trusted
   publishing (the `pypi` environment must be configured on GitHub and the project registered
   as a trusted publisher on PyPI).
4. For releases that touch FTPS, gcode confirm, slice validation, or job upload: run the
   [live-printer smoke](docs/live-printer-smoke.md) when a printer is available.
