## What and why

<!-- Describe the change and why it's needed. Link the issue if there is one. -->

## Checklist

Before requesting review, confirm the following gates pass locally (see [CONTRIBUTING.md](https://github.com/DLANSAMA/platecli/blob/main/CONTRIBUTING.md)):

- [ ] `uvx ruff check bambu_cli` — no lint errors
- [ ] `uvx ruff format --check bambu_cli` — no formatting drift
- [ ] `uvx mypy -p bambu_cli` — no type errors
- [ ] `uvx bandit -c pyproject.toml -r bambu_cli -ll` — no new medium/high findings
- [ ] `uv run python -m pytest tests/ -q -m "not live"` — tests pass
- [ ] `uv run python tests/python_compat_smoke.py` — Python 3.9 floor holds

The smokes are **not** part of pytest, and 3.9 is the easiest leg to break without
noticing: `X | None` in a runtime position (a class base, a `cast()`, a `TypeVar`
bound) passes ruff, mypy, and the suite on your machine and fails at import on 3.9.
CONTRIBUTING.md lists the full set of eight smokes CI runs.
