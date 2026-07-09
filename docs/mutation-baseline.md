# Mutation testing baseline (Phase 1)

**Date:** 2026-07-08  
**Tool:** mutmut 3.6.0  
**Scope (baseline):** `bambu_cli/download/naming.py`, `bambu_cli/download/validation.py`, `bambu_cli/netsafety.py`  
**Not in this baseline (too large for a quick run; expand later):** `bambu_cli/slicer/`, `bambu_cli/job/`, rest of `bambu_cli/download/`

## Score

| Metric | Count |
|--------|------:|
| Total mutants | 626 |
| Killed | 324 |
| Survived | 287 |
| Equivalent / skipped (mutmut 🫥) | 15 |
| Timed out / suspicious / no tests | 0 (some naming helpers initially had "no tests"; pure tests added) |

**Mutation score (killed / (total − equivalent)) = 324 / 611 ≈ 53.0%**

Legend (mutmut UI): 🎉 killed · 🙁 survived · 🫥 equivalent · ⏰ timeout · 🤔 suspicious · 🔇 no tests

This is an **honest baseline**, not a target. Phase 3 may wire a non-blocking report into CI; raising the score is future work.

## Reproduce

```bash
# from repo root, with test extras
uv pip install '.[test]'
# or: uv pip install 'mutmut>=3.0'

# Config is [tool.mutmut] in pyproject.toml
./scripts/run_mutation_baseline.sh
# equivalent:
uv run mutmut run --max-children 6
uv run mutmut results
```

Focused tests used during mutation (also listed in `[tool.mutmut].pytest_add_cli_args_test_selection`):

- `tests/test_naming_and_validation.py`
- `tests/test_slicer_pure.py`
- `tests/test_job.py`
- `tests/test_netsafety.py`
- `tests/test_netsafety_handlers.py`
- `tests/test_download_hardening_p0.py`
- `tests/test_bambu_cli_regressions.py`

## Surviving mutants (known / acceptable for baseline)

Many survivors are **message-string / logging / cosmetic** mutations (changing error text, operator swaps in display paths) or **branchy network helpers** exercised only under heavy mocks. Acceptable for Phase 1 baseline; not all indicate missing safety checks.

Categories observed:

1. **Error-message string literals** in validation emit paths — changing the text does not change control flow; tests assert exit codes / raised type more than exact wording.
2. **`_get_safe_connection` / socket connect plumbing** — some IP-iteration and cache bookkeeping mutants survive the focused suite; private-IP refusal and hop-cap paths *are* tested, but not every cache/fallback branch.
3. **Redirect hop bookkeeping** after the cap is enforced — hop-count increments / attribute names on request objects have residual survivors.
4. **Filename sanitization edge branches** (length limits, reserved-name prefixes) — partially covered; full combinatorial matrix not in the focused suite.

Safety-critical gates that *do* kill mutants well:

- `_has_command_injection_chars` / `_safe_remote_name` control-char rejection
- SSRF private-IP refusal (majority of netsafety connect paths)
- Download size-limit abort (`_validate_max_download_mb_or_exit` / oversized path)

## Expanding scope

To include `slicer/` / `job/` / full `download/`:

1. Edit `[tool.mutmut].only_mutate` in `pyproject.toml` to add those paths.
2. Expect multi-hour runs and high RAM during mutant generation.
3. Re-record scores in a new dated section below this baseline.

## Notes

- mutmut 3.x requires Python ≥ 3.10 (dev machines / CI matrix 3.12+). It is an optional test extra, not a runtime dependency.
- Artifacts: `mutants/` and `.mutmut-cache` are gitignored.
- Do **not** fail CI on mutation score until Phase 3 explicitly enables it.
