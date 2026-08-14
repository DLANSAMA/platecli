"""Guards against version drift and TOC drift in user-facing docs.

README.md is the PyPI long_description (pyproject.toml ``readme``), so a stale
version string there is the first thing a new user sees. The fix is not to keep
the number updated but to forbid the number: the version is single-sourced from
pyproject.toml and surfaced by the PyPI badge and ``plate --version``.

Drift guards for test/coverage numbers in docs:
- The coverage *floor* (--cov-fail-under) cited in docs must match ci.yml exactly.
  This is exact and can never false-positive.
- The documented test count is checked against the actually-collected count within
  a 5% tolerance (tight enough to catch the 666→718 drift = 7.8% but lenient enough
  to survive adding a handful of tests without a doc update).
- The documented branch-coverage percentage is checked against the floor from ci.yml:
  the docs must claim a number at or above the floor (they cannot truthfully claim
  less than the floor without CI being broken).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A bare semantic-version-looking literal, e.g. 0.2.0 or 1.10.3.
SEMVER = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?![\w.])")
# URLs legitimately contain version-shaped path segments; strip them (do NOT
# skip the whole line -- README.md's status line carries both a version and a
# link, and skipping the line would silently disarm this guard).
URL = re.compile(r"https?://\S+|www\.\S+")


def _code_fences_only(text):
    """Blank every line outside a ``` fence, preserving line numbering.

    Used for docs/api.md, where the guard targets *sample payloads*. Prose there
    legitimately cites release numbers ("`pause` and `resume` require --confirm
    (since 0.3.0)") -- those are dated API-history statements that do not go
    stale, unlike an example payload carrying a real version.
    """
    out = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append(line if in_fence else "")
    return "\n".join(out)


def _version_offenders(text):
    offenders = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = URL.sub(" ", line)
        if SEMVER.search(stripped):
            offenders.append((lineno, line.strip()))
    return offenders


def test_readme_contains_no_hardcoded_version():
    offenders = _version_offenders((ROOT / "README.md").read_text(encoding="utf-8"))
    assert not offenders, (
        "README.md must not hardcode a release version (it is the PyPI "
        "long_description and goes stale silently). Point at the PyPI badge or "
        f"`plate --version` instead. Offending lines: {offenders}"
    )


def test_bug_report_template_contains_no_hardcoded_version():
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    offenders = _version_offenders(path.read_text(encoding="utf-8"))
    assert not offenders, f"bug_report.yml must not hardcode a version: {offenders}"


def test_api_doc_contains_no_hardcoded_version():
    api_md = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    offenders = _version_offenders(_code_fences_only(api_md))
    assert not offenders, (
        "docs/api.md sample payloads must use a placeholder such as X.Y.Z "
        f"instead of a real release number: {offenders}"
    )


def test_manual_toc_covers_every_section():
    manual = (ROOT / "docs" / "manual.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", manual, flags=re.MULTILINE)
    toc = re.findall(r"^- \[([^\]]+)\]\(#", manual, flags=re.MULTILINE)
    missing = [h for h in headings if h not in toc]
    assert not missing, f"docs/manual.md Contents list is missing sections: {missing}"


# ---------------------------------------------------------------------------
# Drift guards for measured test/coverage numbers
# ---------------------------------------------------------------------------


def _parse_ci_cov_floor() -> int:
    """Return the integer --cov-fail-under value from ci.yml."""
    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"--cov-fail-under=(\d+)", ci_text)
    assert m, "Could not parse --cov-fail-under from ci.yml"
    return int(m.group(1))


def _parse_doc_cov_floor(text: str) -> int | None:
    """Extract the CI coverage floor from doc text.

    Matches patterns like 'floor 83', 'floor **83**', '--cov-fail-under=83'.
    Returns the first small-ish integer found after 'floor' (ignores mutation
    floor 40 or coverage-target 92 by picking the smallest value ≥ 50).
    """
    # Match 'floor' optionally followed by bold markers, then digits
    for m in re.finditer(r"floor\s+\*{0,2}(\d+)\*{0,2}", text):
        val = int(m.group(1))
        # Skip mutation baseline floor (40%) and target (92); the CI floor is the
        # only value in the 50-90 band
        if 50 <= val <= 90:
            return val
    return None


def _collect_test_count() -> int:
    """Run pytest --collect-only and return the collected item count.

    Uses -q so output is minimal; filters for the summary line.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only", "-m", "not live", "--no-header"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    # Output ends with a line like "718 tests collected" or "718 items / 1 deselected"
    for line in (result.stdout + result.stderr).splitlines():
        m = re.search(r"(\d+)\s+(?:tests?\s+collected|items?\s*/)", line)
        if m:
            return int(m.group(1))
    raise AssertionError(
        f"Could not parse collected test count from pytest output.\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )


def _parse_doc_cov_percent(text: str) -> float | None:
    """Return the first bold measured coverage percentage like **88.9%**."""
    for m in re.finditer(r"\*\*(\d{2}\.\d)%\*\*", text):
        val = float(m.group(1))
        if 80.0 <= val <= 99.9:
            return val
    return None


def test_coverage_floor_matches_ci():
    """The coverage floor cited in docs must exactly match --cov-fail-under in ci.yml.

    This is the most stable guard: the floor never changes without a deliberate
    decision, so there is zero risk of false positives from adding tests.
    """
    ci_floor = _parse_ci_cov_floor()

    roadmap = (ROOT / "docs" / "quality-roadmap.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs" / "test-backlog.md").read_text(encoding="utf-8")

    for doc, text in [("quality-roadmap.md", roadmap), ("test-backlog.md", backlog)]:
        floor = _parse_doc_cov_floor(text)
        assert floor is not None, f"{doc}: could not find 'floor NN' coverage floor citation"
        assert floor == ci_floor, (
            f"{doc} cites coverage floor {floor} but ci.yml has --cov-fail-under={ci_floor}. Update the doc to match."
        )


def test_documented_coverage_percent_at_or_above_floor():
    """Docs must cite a measured coverage % that is not below the CI floor.

    Prevents the scoreboard from silently rotting to a number CI would reject.
    """
    ci_floor = float(_parse_ci_cov_floor())
    roadmap = (ROOT / "docs" / "quality-roadmap.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs" / "test-backlog.md").read_text(encoding="utf-8")
    for doc, text in [("quality-roadmap.md", roadmap), ("test-backlog.md", backlog)]:
        percent = _parse_doc_cov_percent(text)
        assert percent is not None, f"{doc}: could not find a bold coverage percent like **88.9%**"
        assert percent >= ci_floor, (
            f"{doc} cites measured coverage {percent}% but ci.yml floor is {ci_floor:g}. "
            "Update the snapshot or the floor."
        )


def test_documented_test_count_within_tolerance():
    """Documented test count must be within 5% of the actual collected count.

    5% catches the 666→718 drift (7.8%) while tolerating a few new tests
    without requiring an immediate doc update. If this test is triggered by
    growth, update the snapshot numbers in docs/quality-roadmap.md and
    docs/test-backlog.md.
    """
    actual = _collect_test_count()
    tolerance = 0.05

    roadmap = (ROOT / "docs" / "quality-roadmap.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs" / "test-backlog.md").read_text(encoding="utf-8")

    # Extract bold test counts like **718** from each doc.
    # Matches patterns like:
    #   "**718** non-live tests passing" (quality-roadmap)
    #   "**718** passing" (test-backlog table cell)
    for doc, text in [("quality-roadmap.md", roadmap), ("test-backlog.md", backlog)]:
        counts = [int(m) for m in re.findall(r"\*\*(\d{3,4})\*\*\s+(?:non-live\s+)?(?:tests?\s+)?passing", text)]
        assert counts, f"{doc}: could not find a bolded test count like **718** near 'passing'"
        for count in counts:
            drift = abs(count - actual) / actual
            assert drift <= tolerance, (
                f"{doc} documents {count} tests but {actual} are currently collected "
                f"(drift {drift:.1%} > {tolerance:.0%} tolerance). "
                "Update the snapshot numbers in the docs."
            )
