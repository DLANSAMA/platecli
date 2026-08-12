# Contributing

Thanks for your interest in improving platecli!

## Setup

```bash
git clone https://github.com/DLANSAMA/platecli
cd platecli   # or your local directory name
uv sync --extra test   # test deps (pytest etc.) live in the "test" extra
# or: pip install -e ".[test]"
```

Plain `uv sync` installs runtime deps only — the test commands below then fail
with `No module named pytest`. CI installs the same set via `uv pip install '.[test]'`.

The `test` extra also pulls in `textual`, so the `plate tui` pilot tests run in the
default suite. The user-facing install is the separate `[tui]` extra
(`pip install 'platecli[tui]'`); Textual is never a runtime dependency.

## Running tests

```bash
# Unit + contract suite (no printer, no live network to a real machine)
uv run python -m pytest tests/ -q -m "not live"

# Match CI hardness (ResourceWarning as error + coverage floor)
uv run python -W error::ResourceWarning -m pytest tests/ -m "not live" \
  --cov=bambu_cli --cov-report=term-missing --cov-fail-under=83

# Smokes used in CI — all of them (see the "lint" job in .github/workflows/ci.yml).
# These are NOT part of pytest; a green suite says nothing about them.
python scripts/syntax_smoke.py
python scripts/cli_help_smoke.py
uv run python tests/ci_workflow_smoke.py
uv run python tests/python_compat_smoke.py       # 3.10 floor: syntax parse
uv run python tests/dependency_resolution_smoke.py
uv run python tests/release_readiness_smoke.py
uv run python tests/privacy_smoke.py
uv run python tests/agent_cli_smoke.py
```

`python_compat_smoke.py` parses the package and tests as Python 3.10 so a
3.11-only construct cannot sneak in on a newer laptop. The advertised floor
is 3.10.
`release_readiness_smoke.py` flags local `__pycache__` / `build` / `dist` noise
that CI's clean checkout does not have — expected locally, not a failure to chase.

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

## Cleaning generated artifacts

```bash
python scripts/clean_artifacts.py             # __pycache__, *.pyc, tool caches, build/dist/wheelhouse, *.egg-info
python scripts/clean_artifacts.py --dry-run   # list targets, delete nothing
python scripts/clean_artifacts.py --venv      # ALSO delete .venv (asks first; -y skips the prompt)
```

`.venv` is **never** removed unless you pass `--venv` / `--all` — the script walks
around `.git`, `.venv`, `venv`, `.claude` and `node_modules`. If you do remove it,
recreate it with `uv sync --extra test` before the next `uv run`.

Removal is verified rather than best-effort. On Windows a file that is in use (a
running `python.exe`, an editor, another shell) makes deletion fail; the script
reports the surviving path and exits non-zero instead of leaving a half-deleted
tree. A `.venv` in that state makes every later `uv run` fail with
`No pyvenv.cfg file` (exit code 106) — recover with:

```bash
python scripts/clean_artifacts.py --venv -y   # after closing whatever held the lock
uv sync --extra test
```

CI runs the plain form before the release readiness smoke and never passes `--venv`.

## Lint and types (blocking in CI)

```bash
uvx ruff check bambu_cli
uvx ruff format --check bambu_cli
uvx mypy -p bambu_cli          # full package; check_untyped_defs; no residual excludes
uvx bandit -c pyproject.toml -r bambu_cli -ll
# pip-audit is also blocking in CI (dependency high/critical)

# Also blocking in the same CI job, and cheap to run locally:
python scripts/check_layers.py                 # import-layer boundaries
uv run --python 3.12 --with pydantic python scripts/gen_schemas.py --check
```

The lint job additionally runs three greps (no test-awareness in production code,
`sys.exit` only in `cli.py`, no `@mockable`) and a `-m "security or contract"`
pytest pass. `gen_schemas.py` needs 3.10+ to evaluate the contracts' `X | None`
annotations and is pinned to 3.12 in CI.

CI pins these tool versions (see `.github/workflows/ci.yml`); running them unpinned locally is fine.

A green `pytest` does **not** mean lint/types/security gates are green.

## Quality roadmap

Phased plan and **honest scoreboard** (GitHub checkout only — not in the PyPI sdist):
**[docs/quality-roadmap.md](docs/quality-roadmap.md)**.  
Remaining gaps: **[docs/test-backlog.md](docs/test-backlog.md)**.  
Agent/runtime rules: **[AGENTS.md](AGENTS.md)** (ships in sdist).  
Threat model: **[SECURITY.md](SECURITY.md)** (ships in sdist).  
JSON contracts: **[docs/api.md](docs/api.md)** + **[docs/schemas/](docs/schemas/)** (ship in sdist).

As of 2026-08-05 (0.5.0): overall **solid A− / A**. The 2026-07 audit's four
architecture/contract gaps have since closed — the domain→`cli` helper extraction
(B.4), the single-sourced TLS pin verification (B.5), the remaining JSON schemas
(now *generated* from `bambu_cli/contracts/`, one per `--json` subcommand), and the
camera bind/pin-fallback hardenings. Main gaps to A+ / 1.0 are now coverage
(89.2% measured on CI's Linux legs, CI floor **83**, target 92) and the camera
residuals still listed in SECURITY.md. Do not read "A−/A" as "A+" — see the
scoreboard for what is actually ticked.

## Code conventions

- New command logic goes in `bambu_cli/commands/` (or a new focused package) using
  `get_printer()` / `RuntimeContext` — do not grow `bambu_cli/bambu.py` beyond the thin entrypoint.
- Prefer dependency injection over patching module globals (see `download/` for the pattern).
- JSON success and error payloads: assert full shapes (`status`, `command`, `failed_step`,
  `exit_code`, `next_command` where applicable). When introducing agent-facing fields, edit the
  dataclass in `bambu_cli/contracts/` and run `python scripts/gen_schemas.py` — **never hand-edit
  `docs/schemas/*.json`**, it is generated and CI diffs it.
- Follow `docs/quality-roadmap.md` and `docs/test-backlog.md` when adding tests.
- Do not add Claude-Session or similar trailers to commits or PRs.

## Releases (maintainers)

Full procedure, including the **yank/rollback runbook**: [docs/releasing.md](docs/releasing.md).

1. Update **`version` only in `pyproject.toml`** (runtime `bambu_cli.constants.VERSION`
   resolves from package metadata / that file). Move `CHANGELOG.md` entries from Unreleased
   to the new version.
2. Only tag a commit that is green on CI: `git tag vX.Y.Z && git push --tags`.
3. The Release workflow re-runs the full CI matrix on the tagged commit, builds, publishes
   to PyPI via trusted publishing (the `pypi` environment must be configured on GitHub and
   the project registered as a trusted publisher on PyPI), then creates the GitHub release.
4. A bad release cannot be re-uploaded — yank it and ship `X.Y.Z+1`. See docs/releasing.md.
5. For releases that touch FTPS, gcode confirm, slice validation, or job upload: run the
   [live-printer smoke](docs/live-printer-smoke.md) when a printer is available.
