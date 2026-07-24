## What and why

<!-- Describe the change and why it's needed. Link the issue if there is one. -->

## Checklist

Before requesting review, confirm the following gates pass locally (see [CONTRIBUTING.md](https://github.com/DLANSAMA/platecli/blob/main/CONTRIBUTING.md)):

- [ ] `uvx ruff check bambu_cli` — no lint errors
- [ ] `uvx ruff format --check bambu_cli` — no formatting drift
- [ ] `uvx mypy -p bambu_cli` — no type errors
- [ ] `uvx bandit -c pyproject.toml -r bambu_cli -ll` — no new medium/high findings
- [ ] `uv run python -m pytest tests/ -q -m "not live"` — tests pass
