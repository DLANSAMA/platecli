# Mutation testing

Mutation testing checks that the tests around platecli's safety logic would
actually notice a broken change, not just run the code. It is contributor
tooling: it stays on GitHub and is not shipped in the PyPI packages.

- **Tool:** mutmut 3.6.0 (needs Python 3.10+; CI uses 3.12)
- **Run it:** `./scripts/run_mutation_baseline.sh`
- **CI:** `.github/workflows/mutation.yml`, nightly when `main` has changed and on
  manual dispatch. It does not run on pull requests; a full run takes minutes.

## Scope

Only pure safety logic is mutated (`[tool.mutmut].only_mutate` in `pyproject.toml`):

| Module | What it guards |
|--------|----------------|
| `bambu_cli/download/naming.py` | Filename sanitising, remote names, command-injection characters |
| `bambu_cli/download/validation.py` | URL scheme, host and credentials; download size limits |
| `bambu_cli/netsafety.py` | SSRF `is_global` gating, redirect hop cap, safe opener |
| `bambu_cli/slicer/options.py` | Temperature, infill, copies and wall-type bounds |
| `bambu_cli/slicer/output.py` | Sliced-3MF validation and `_finalize_slice` |
| `bambu_cli/job/payload.py` | Print MQTT payload and AMS mapping |
| `bambu_cli/job/predict.py` | Dry-run remote-name prediction |

Subprocess runners (OrcaSlicer, STEP conversion), live network code
(the downloader), orchestration I/O (`job/orchestrate.py`, `job/steps.py`),
the printer transports, camera and setup wizard are left out: they give no fast
unit-level signal.

The focused test selection is `[tool.mutmut].pytest_add_cli_args_test_selection`
in `pyproject.toml`.

## Current score

Measured 2026-08-04 on a clean tree: **1061 killed of 2091 mutants, 50.7%**
(1027 survived, 3 timed out).

| Module | Score |
|--------|------:|
| `job/payload.py` | 69.4% |
| `netsafety.py` | 67.3% |
| `slicer/options.py` | 64.2% |
| `download/naming.py` | 61.7% |
| `job/predict.py` | 49.1% |
| `download/validation.py` | 40.3% |
| `slicer/output.py` | 21.8% |

The per-module rows cover 2010 of the 2091 mutants; 81 could not be attributed
to a module. Quote the overall score, not the rows.

`slicer/output.py` holds about a quarter of all survivors. Its line coverage is
high because the hermetic OrcaSlicer stub runs `_finalize_slice` end to end, but
those tests check that a slice succeeds, not what it wrote. More end-to-end
tests will not move it; extracting the decisions in `_finalize_slice` into
small functions that can be asserted directly will.

## CI floor

| Setting | Value |
|---------|------:|
| `MUTATION_SCORE_FLOOR` | **48** |
| Formula | `100 * killed / (killed + survived + timeout + suspicious + no_tests)` |

The floor sits just under the measured score, so a real regression fails the
nightly run without one equivalent mutant flaking it. The value lives in
`scripts/run_mutation_baseline.sh` and `.github/workflows/mutation.yml`; change
both, and this file, together. Re-run before moving it.

## Known survivors

Accepted as equivalent or cosmetic: error-message and log-format strings,
`getattr` defaults the tests always set, `ZipFile(..., "r")` versus the default
mode, firmware-shaped constant fields in the print payload (`sequence_id`,
`profile_id`, vibration flags), and early-return branches in `predict.py` that
are observationally equivalent under the focused suite.

Not accepted: the `_finalize_slice` survivors above (exit-code interpretation,
JSON output, path display).

Killed reliably: command-injection character detection, `_safe_remote_name`
rejection of path and control characters, the non-global IP refusal, slice
bounds and AMS slot range, incomplete or non-zip 3MF rejection, and the
`--use-ams` / `--ams-mapping` pairing.

## Reproduce

```bash
# from the repo root
uv pip install '.[test]'
FORCE_CLEAN=1 ./scripts/run_mutation_baseline.sh
```

Artifacts (`mutants/`, `.mutmut-cache`, `.hypothesis/`) are gitignored.
