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
uv run python -m pytest tests/ -q              # unit + contract suite
uv run python tests/privacy_smoke.py           # smoke suites (see .github/workflows/ci.yml for the full list)
```

- No printer is required: most tests run against simulation mode (`--sim`).
- `tests/live_printer_smoke.py` talks to a real printer — read its module docstring before running it, and never run it against a printer mid-print.
- CI enforces a "no test-awareness in production code" rule: production modules must not branch on test/CI environment markers. Patch via the concrete module under test (or the existing `bambu_cli.bambu` facade where tests already do).

## Quality roadmap

Phased plan to A+ product and testing scores: **[docs/quality-roadmap.md](docs/quality-roadmap.md)**.
Remaining gap scratch list: [docs/test-backlog.md](docs/test-backlog.md).

## Code conventions

- New command logic goes in `bambu_cli/commands.py` (or a new focused module) using `get_printer()` / `RuntimeContext` — do not add new module globals to `bambu_cli/bambu.py`.
- Lint with `uvx ruff check bambu_cli`; typed modules are checked with mypy (see the CI mypy step for the current list — add fully annotated modules there).
- Follow `docs/quality-roadmap.md` and `docs/test-backlog.md` conventions when adding tests.

## Releases (maintainers)

1. Update **`version` only in `pyproject.toml`** (runtime `bambu_cli.constants.VERSION` resolves from package metadata / that file). Move `CHANGELOG.md` entries from Unreleased to the new version.
2. Tag: `git tag vX.Y.Z && git push --tags`.
3. The Release workflow builds, creates a GitHub release, and publishes to PyPI via trusted publishing (the `pypi` environment must be configured on GitHub and the project registered as a trusted publisher on PyPI).
