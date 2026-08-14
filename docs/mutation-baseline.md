# Mutation testing baseline (Phase 3)

**Baseline date:** 2026-07-09 (original widened baseline; retained below for comparison)  
**Current measurement:** 2026-08-04 — **50.7%**, floor raised 40 → **48** (re-run before changing the floor again)  
**Tool:** mutmut 3.6.0  
**Reproduce:** `./scripts/run_mutation_baseline.sh`  
**CI:** `.github/workflows/mutation.yml` — `workflow_dispatch` + nightly `schedule` only  
  (**not** on every `pull_request`; full runs are minutes-long and would slow PR feedback)

Related: [quality-roadmap.md](quality-roadmap.md), [test-backlog.md](test-backlog.md).

## Scope (blocklist-style purity)

Mutate **pure safety logic** only (`[tool.mutmut].only_mutate` in `pyproject.toml`):

| Path | Why included |
|------|----------------|
| `bambu_cli/download/naming.py` | Filename sanitize / remote-name / command-injection chars |
| `bambu_cli/download/validation.py` | URL scheme/host/credentials + download size limits |
| `bambu_cli/netsafety.py` | SSRF `is_global` gating, redirect hop cap, safe opener |
| `bambu_cli/slicer/options.py` | Slice temp/infill/copies/wall-type bounds |
| `bambu_cli/slicer/output.py` | `_is_valid_sliced_3mf` (also mutates `_finalize_slice` I/O — see note) |
| `bambu_cli/job/payload.py` | Print MQTT payload + AMS mapping parse |
| `bambu_cli/job/predict.py` | Dry-run remote-name prediction |

**Explicitly not mutated** (no fast unit signal / subprocess / live I/O):

- `slicer/orca.py`, `slicer/step_convert.py`, `slicer/cmd.py`, `slicer/profiles.py`
- `job/orchestrate.py`, `job/steps.py`, `job/support.py`
- `download/downloader.py`, `download/extract.py`, `download/html_links.py`
- `printer.py`, protocols, camera, setup wizard

Focused tests (also listed in `[tool.mutmut].pytest_add_cli_args_test_selection`):

- `tests/test_naming_and_validation.py`
- `tests/test_properties_safety.py` (Hypothesis invariants)
- `tests/test_slicer_pure.py`
- `tests/test_job.py`
- `tests/test_netsafety.py` / `tests/test_netsafety_handlers.py`
- `tests/test_download_hardening_p0.py`
- `tests/test_bambu_cli_regressions.py`

## Score — before / after Phase 3 widen

### Phase 1 baseline (2026-07-08) — narrow scope

| Metric | Count |
|--------|------:|
| Modules | naming + validation + netsafety only |
| Total mutants | 626 |
| Killed | 324 |
| Survived | 287 |
| Equivalent / skipped | 15 |
| **Score** | **324 / 611 ≈ 53.0%** |

### Phase 3 baseline (2026-07-09) — widened pure safety

| Metric | Count |
|--------|------:|
| Total mutants | 1480 |
| Killed | 610 |
| Survived | 870 |
| **Score** | **610 / 1480 ≈ 41.2%** |

### Measured 2026-08-04 (current) — same scope, re-run on a clean tree

| Metric | Count |
|--------|------:|
| Total mutants | 2091 |
| Killed | 1061 |
| Survived | 1027 |
| Timeout | 3 |
| **Score** | **1061 / 2091 = 50.7%** |

Per-module, derived from the mutant sources and survivor list. **These rows do
not reconcile to the headline totals and should not be read as if they do:** the
seven rows below are the whole `only_mutate` scope, yet they sum to **2010**
mutants (990 survived + 1020 killed) against a headline of **2091** (1027 + 1061
+ 3 timeouts). **81 mutants — 37 survived, 41 killed, 3 timeout — are
unattributed.** Per-module scores are therefore measured but incomplete; the
headline 50.7% is the number to quote. Re-derive the split before using any row
as a target.

| Module | Total | Survived | Killed | Score | vs 2026-07-09 |
|--------|------:|---------:|-------:|------:|--------------:|
| `job/payload.py` | 180 | 55 | 125 | **69.4%** | ~65–69% → flat |
| `netsafety.py` | 153 | 50 | 103 | **67.3%** | ~58% → **+9** |
| `slicer/options.py` | 433 | 155 | 278 | **64.2%** | ~60–63% → +2 |
| `download/naming.py` | 201 | 77 | 124 | **61.7%** | ~55–56% → **+6** |
| `job/predict.py` | 379 | 193 | 186 | **49.1%** | ~33% → **+16** |
| `download/validation.py` | 320 | 191 | 129 | **40.3%** | ~30–31% → **+10** |
| `slicer/output.py` | 344 | 269 | 75 | **21.8%** | ~21% → **+0.8** |

**The C.4 prediction did not pan out — correcting it here.** The previous
revision of this file said the hermetic Orca stub "should score materially
higher" for `slicer/output.py` and asked for the row to be updated after a
re-run. Re-run: **21.8%**, essentially unchanged. Line coverage rose (79.8% →
92.7%) while the mutation score did not, which is the textbook signal that the
new tests *execute* `_finalize_slice` without *constraining* it — they assert
the command succeeds, not what it wrote. `slicer/output.py` now holds 269 of the
1027 survivors (26%), the single largest pocket.

The honest options for that module are (a) extract the pure decision logic out
of `_finalize_slice` so it can be asserted directly, or (b) accept it and stop
counting it. Adding more end-to-end tests will not move it. Not attempted here:
it is a production refactor, not a test change.

`download/validation.py`'s +10 comes from `tests/test_download_validation_boundary.py`,
which covers the `_reject_*` functions that previously had no direct tests.

## CI floor

| Setting | Value |
|---------|------:|
| `MUTATION_SCORE_FLOOR` | **48** |
| Formula | `100 * killed / (killed + survived + timeout + suspicious + no_tests)` |
| Rationale | Just under the measured 2026-08-04 score (50.7%), same discipline as coverage `fail_under` — catch real regressions without flaking on one equivalent mutant. Raised from 40, which was set against the older 41.2% and had ~10 points of silent-drift room. |

> **Fixed 2026-08-04:** the floor was previously assigned in
> `run_mutation_baseline.sh` **without `export`**, so the score check — which runs
> in a child python process — never saw it and fell back to its own hardcoded
> default. A local run printed `floor: 40%` in its header and then enforced a
> different number a few lines later. CI was unaffected only because the workflow
> sets the variable at job level. There is now one value, exported, and the child
> errors out rather than inventing a default.

Enforced by `./scripts/run_mutation_baseline.sh` after `mutmut export-cicd-stats`. Nightly / manual workflow fails if the score falls below the floor.

## Surviving mutants (accepted / deferred)

Categories (not an exhaustive dump of the 1027 survivors):

1. **Equivalent / cosmetic** — error-message string literals, log format, `getattr` default when tests always set the attribute, `ZipFile(..., "r")` vs default mode.
2. **`_finalize_slice` (output.py)** — subprocess exit interpretation, JSON emit, path display. **Addressed (C.4), but the prediction failed:** `tests/fakes/orca_stub` + `tests/test_slice_stub_integration.py` run these branches against a real fake-slicer subprocess, and the re-run still scored **21.8%** — **269 survivors, the single largest pocket (26% of all survivors)**. They are *not* cosmetic: the bulk are unconstrained `_finalize_slice` exit-code interpretation, JSON emit, and path display (see the C.4 correction above). Extract those decisions into assertable units or stop counting the module — do not treat these as accepted.
3. **DNS cache / hop bookkeeping (netsafety)** — TTL, cache size clear, attribute names on redirect requests. Core `is_global` refuse path is well killed.
4. **URL normalize / Content-Disposition edges (validation/naming)** — ambiguous scheme-less inputs and RFC2231 header tuples; behavior partially covered; full combinatorial matrix deferred.
5. **Dry-run prediction (predict.py)** — Printables/archive/extension branches that return `None` early; many mutants are observationally equivalent under the focused suite.
6. **Print payload constant fields** — `sequence_id`, `profile_id`, vibration flags: firmware-shaped defaults not all asserted (accepted as non-safety for local CLI).

Safety gates that **do** kill well under the widened suite:

- Command-injection char detection / `_safe_remote_name` rejection of path & control chars  
- Non-global IP refuse (`is_global` gating) for SSRF  
- Slice nozzle/bed/infill/copies bounds + AMS slot range  
- Incomplete / non-zip 3mf rejection (`_is_valid_sliced_3mf`)  
- `--use-ams` / `--ams-mapping` pairing  

## Reproduce

```bash
# from repo root
uv pip install '.[test]'   # mutmut + hypothesis
FORCE_CLEAN=1 ./scripts/run_mutation_baseline.sh
# optional: MUTATION_SCORE_FLOOR=48 (default in script / CI)
```

Artifacts (`mutants/`, `.mutmut-cache`, `.hypothesis/`) are gitignored.

## Notes

- mutmut 3.x needs Python ≥ 3.10 (CI mutation job uses 3.12).
- Hypothesis property tests live in `tests/test_properties_safety.py` and are part of the focused mutmut suite.
- Raising the score further: the `slicer/output.py` re-run is **done** (2026-08-04, 21.8% — the row above is current); adding hermetic tests moved line coverage but not the mutation score, so the next lever is extracting `_finalize_slice`'s decisions into assertable units, or dropping the module from `only_mutate` rather than carrying a 21.8% row. Separately, attribute the 81 unaccounted mutants (see §Measured 2026-08-04) before trusting per-module targets. Optionally still move pure 3mf validation to a tiny module so mutmut does not spend budget on I/O.
