#!/usr/bin/env bash
# Reproduce the Phase 1 mutation-testing baseline for safety-critical modules.
# Not run in CI (slow); see docs/mutation-baseline.md.
#
# Config lives in pyproject.toml [tool.mutmut].
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/mutmut ]]; then
  MUTMUT=(.venv/bin/mutmut)
else
  MUTMUT=(uv run mutmut)
fi

echo "==> mutmut baseline (safety-critical modules from [tool.mutmut])"
echo "    source: slicer.py, job.py, download/*, netsafety.py"
echo

# Ensure mutmut is available in the env.
uv pip install 'mutmut>=3.0' -q

"${MUTMUT[@]}" run "$@"
echo
echo "==> results"
"${MUTMUT[@]}" results || true
echo
echo "Done. Record totals in docs/mutation-baseline.md after a clean run."
