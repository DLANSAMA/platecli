"""Guards against version drift and TOC drift in user-facing docs.

README.md is the PyPI long_description (pyproject.toml ``readme``), so a stale
version string there is the first thing a new user sees. The fix is not to keep
the number updated but to forbid the number: the version is single-sourced from
pyproject.toml and surfaced by the PyPI badge and ``plate --version``.
"""

import re
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
