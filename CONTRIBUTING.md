# Contributing

Thanks for your interest in improving bambu-local-cli!

## Setup

```bash
git clone https://github.com/DLANSAMA/bambu-local-cli
cd bambu-cli
uv sync          # or: pip install -e ".[test]"
```

## Running tests

```bash
uv run python -m pytest tests/ -q -m "not live"   # unit + contract suite (no printer)
uv run python tests/privacy_smoke.py              # smoke suites (see .github/workflows/ci.yml)
```

- No printer is required for the default suite: use simulation (`--sim`) or mocks.
- **Live pre-release harness** (`tests/live_printer_smoke.py`): opt-in only
  (`BAMBU_LIVE=1` + real config + `BAMBU_LIVE_SOURCE`). Marked `live` so CI's
  `-m "not live"` never runs it. See [docs/live-printer-smoke.md](docs/live-printer-smoke.md).
  Never run against a printer mid-print; print start needs `BAMBU_LIVE_PRINT_CONFIRM`.
- **Mutation baseline** (safety modules): `./scripts/run_mutation_baseline.sh` —
  [docs/mutation-baseline.md](docs/mutation-baseline.md). Nightly CI only, not every PR.
- CI enforces a "no test-awareness in production code" rule: production modules must not branch on test/CI environment markers. Prefer injecting collaborators over patching module globals.

## Quality roadmap

Phased plan to A+ product and testing scores: **[docs/quality-roadmap.md](docs/quality-roadmap.md)**.
Remaining gap scratch list: [docs/test-backlog.md](docs/test-backlog.md).

## Code conventions

- New command logic goes in `bambu_cli/commands/` (or a new focused module) using `get_printer()` / `RuntimeContext` — do not add new module globals to `bambu_cli/bambu.py`.
- Lint with `uvx ruff check bambu_cli`; mypy is whole-package with a blocklist of
  residuals in `[tool.mypy].exclude` (`uvx mypy -p bambu_cli`).
- Follow `docs/quality-roadmap.md` and `docs/test-backlog.md` conventions when adding tests.

## Releases (maintainers)

1. Update **`version` only in `pyproject.toml`** (runtime `bambu_cli.constants.VERSION` resolves from package metadata / that file). Move `CHANGELOG.md` entries from Unreleased to the new version.
2. Tag: `git tag vX.Y.Z && git push --tags`.
3. The Release workflow builds, creates a GitHub release, and publishes to PyPI via trusted publishing (the `pypi` environment must be configured on GitHub and the project registered as a trusted publisher on PyPI).
