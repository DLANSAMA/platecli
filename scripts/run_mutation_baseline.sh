#!/usr/bin/env bash
# Reproduce the Phase 3 mutation-testing baseline for safety-critical pure modules.
# Config: [tool.mutmut] in pyproject.toml. Docs: docs/mutation-baseline.md.
#
# Nightly / workflow_dispatch CI uses this script and enforces MUTATION_SCORE_FLOOR.
# Not run on every PR (too slow). Local: ./scripts/run_mutation_baseline.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Floor: kill rate (killed / (total - skipped equivalents if available)).
# Documented in docs/mutation-baseline.md; keep CI and docs in sync.
# Score = 100 * killed / max(1, killed + survived + timeout + suspicious + no_tests)
# (mutmut "skipped"/equivalent rows are omitted from the denominator when absent).
MUTATION_SCORE_FLOOR="${MUTATION_SCORE_FLOOR:-40}"

if [[ -x .venv/bin/mutmut ]]; then
  MUTMUT=(.venv/bin/mutmut)
  PYTHON=(.venv/bin/python)
else
  MUTMUT=(uv run mutmut)
  PYTHON=(uv run python)
fi

echo "==> mutmut baseline (pure safety modules from [tool.mutmut].only_mutate)"
echo "    naming/validation/netsafety + slicer/{options,output} + job/{payload,predict}"
echo "    floor: ${MUTATION_SCORE_FLOOR}%"
echo

# Ensure mutmut + hypothesis (property suite) are available.
uv pip install 'mutmut>=3.0' 'hypothesis>=6.0' -q

# Fresh run when CI or FORCE_CLEAN=1; local re-runs may resume.
if [[ "${FORCE_CLEAN:-0}" == "1" || "${CI:-}" == "true" ]]; then
  rm -rf mutants .mutmut-cache
fi

"${MUTMUT[@]}" run "$@"
echo
echo "==> results"
"${MUTMUT[@]}" results || true
echo
echo "==> CI/CD stats export"
"${MUTMUT[@]}" export-cicd-stats || true

# Compute honest score and enforce floor.
"${PYTHON[@]}" - <<'PY'
import json
import os
import sys
from pathlib import Path

floor = float(os.environ.get("MUTATION_SCORE_FLOOR", "48"))
stats_path = Path("mutants/mutmut-cicd-stats.json")
if not stats_path.is_file():
    print("ERROR: mutants/mutmut-cicd-stats.json missing after mutmut run", file=sys.stderr)
    sys.exit(2)

s = json.loads(stats_path.read_text(encoding="utf-8"))
killed = int(s.get("killed", 0))
survived = int(s.get("survived", 0))
timeout = int(s.get("timeout", 0))
suspicious = int(s.get("suspicious", 0))
no_tests = int(s.get("no_tests", 0))
total = int(s.get("total", 0))
# Denominator: mutants that should have been killed by tests (exclude pure skips if any).
accounted = killed + survived + timeout + suspicious + no_tests
if accounted <= 0:
    print(f"ERROR: no accounted mutants (total={total}, stats={s})", file=sys.stderr)
    sys.exit(2)
score = 100.0 * killed / accounted
print(
    f"Mutation score: {score:.1f}%  "
    f"(killed={killed}, survived={survived}, timeout={timeout}, "
    f"suspicious={suspicious}, no_tests={no_tests}, total={total})"
)
print(f"Floor: {floor:.1f}%")
if score + 1e-9 < floor:
    print(
        f"ERROR: mutation score {score:.1f}% is below floor {floor:.1f}%. "
        "See docs/mutation-baseline.md.",
        file=sys.stderr,
    )
    sys.exit(1)
print("Mutation score floor OK.")
PY

echo
echo "Done. Record totals in docs/mutation-baseline.md after a clean run."
