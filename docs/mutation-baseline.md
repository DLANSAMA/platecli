# Mutation testing baseline (Phase 3)

**Baseline date:** 2026-07-09 (scores below; re-run before changing the floor)  
**Doc refresh:** 2026-07-17 (scope/floor unchanged)  
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
| Timeout / suspicious / no_tests | 0 |
| **Score** | **610 / 1480 ≈ 41.2%** |

Per-module (approx. killed / accounted on a clean run):

| Module | Score | Note |
|--------|------:|------|
| `job/payload.py` | ~65–69% | AMS + payload generation well tested |
| `slicer/options.py` | ~60–63% | Bounds + property tests |
| `netsafety.py` | ~58% | Private-IP refusal strong; cache/handler cosmetics survive |
| `download/naming.py` | ~55–56% | Injection + sanitize properties; CD/header edges survive |
| `job/predict.py` | ~33% | Dry-run heuristics under-specified; many equivalent branches |
| `download/validation.py` | ~30–31% | Message/emit paths + normalize edge strings |
| `slicer/output.py` | ~21% | **Low:** `_finalize_slice` I/O/logging mutates with little unit signal; `_is_valid_sliced_3mf` itself is much better covered |

**Honest reading:** the overall score **dropped vs Phase 1** because scope **widened** into harder modules (especially `output._finalize_slice` and `predict`/`validation` emit paths). That is intentional. Do **not** restore a high score by shrinking back to only well-covered files.

## CI floor

| Setting | Value |
|---------|------:|
| `MUTATION_SCORE_FLOOR` | **40** |
| Formula | `100 * killed / (killed + survived + timeout + suspicious + no_tests)` |
| Rationale | Just under the honest Phase 3 score (~41.2%), same discipline as coverage `fail_under` — catch real regressions without flaking on one equivalent mutant |

Enforced by `./scripts/run_mutation_baseline.sh` after `mutmut export-cicd-stats`. Nightly / manual workflow fails if the score falls below the floor.

## Surviving mutants (accepted / deferred)

Categories (not an exhaustive dump of 861 IDs):

1. **Equivalent / cosmetic** — error-message string literals, log format, `getattr` default when tests always set the attribute, `ZipFile(..., "r")` vs default mode.
2. **`_finalize_slice` (output.py)** — subprocess exit interpretation, JSON emit, path display. Deferred: needs hermetic fake-Orca fixtures; not pure safety. Dominates the low output.py score.
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
# optional: MUTATION_SCORE_FLOOR=40 (default in script / CI)
```

Artifacts (`mutants/`, `.mutmut-cache`, `.hypothesis/`) are gitignored.

## Notes

- mutmut 3.x needs Python ≥ 3.10 (CI mutation job uses 3.12).
- Hypothesis property tests live in `tests/test_properties_safety.py` and are part of the focused mutmut suite.
- Raising the score further: add hermetic tests for `_finalize_slice` decision branches, or move pure 3mf validation to a tiny module so mutmut does not spend budget on I/O.
