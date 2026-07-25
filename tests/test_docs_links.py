"""Guard that in-repo documentation links and heading anchors stay valid.

The repo links between docs with absolute GitHub blob/tree URLs so the links
survive PyPI's README rendering. That means a renamed or deleted doc produces a
404 for users instead of a broken relative link CI would otherwise notice.

Hermetic: pure pathlib + re against the checkout. No network, no filesystem
writes, no ordering dependence, no bambu_cli import (so coverage is unaffected).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SELF_LINK = re.compile(r"https://github\.com/DLANSAMA/platecli/(?:blob|tree)/main/([^)\s\"'>#]+)")
_INPAGE_ANCHOR = re.compile(r"\]\(#([^)]+)\)")

# Docs that are linked from other docs and must therefore exist.
DOC_FILES = [
    "README.md",
    "AGENTS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs/manual.md",
    "docs/api.md",
    "docs/troubleshooting.md",
]


def _github_slug(heading):
    """Mirror GitHub's heading-anchor slugger.

    Lowercase, drop everything that is not a word char / hyphen / space, then
    turn spaces into hyphens. Runs of dropped punctuation between spaces leave
    consecutive hyphens behind, exactly as GitHub does.
    """
    text = re.sub(r"[^\w\- ]+", "", heading.strip().lower())
    return text.replace(" ", "-")


def _read(relpath):
    return (ROOT / relpath).read_text(encoding="utf-8")


def _toc_block(text):
    """Everything above the first level-2 heading (where the Contents list lives)."""
    return text.split("\n## ", 1)[0]


@pytest.mark.parametrize("relpath", DOC_FILES)
def test_doc_exists(relpath):
    assert (ROOT / relpath).is_file(), "missing documentation file: {0}".format(relpath)


@pytest.mark.parametrize("relpath", DOC_FILES)
def test_self_links_resolve(relpath):
    broken = []
    for target in _SELF_LINK.findall(_read(relpath)):
        target = target.rstrip("/")
        if not (ROOT / target).exists():
            broken.append(target)
    assert not broken, "{0} links to nonexistent repo paths: {1}".format(relpath, sorted(set(broken)))


def test_troubleshooting_is_discoverable():
    """The troubleshooting guide must be linked from README and the user guide."""
    for relpath in ("README.md", "docs/manual.md"):
        assert "docs/troubleshooting.md" in _read(relpath), "{0} does not link to the troubleshooting guide".format(
            relpath
        )


def test_manual_toc_covers_every_section():
    """Every `## ` heading in the user guide is anchored from its Contents list."""
    text = _read("docs/manual.md")
    anchors = set(_INPAGE_ANCHOR.findall(_toc_block(text)))
    missing = [h for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE) if _github_slug(h) not in anchors]
    assert not missing, "headings missing from the manual's Contents list: {0}".format(missing)


def test_troubleshooting_toc_covers_every_section():
    text = _read("docs/troubleshooting.md")
    anchors = set(_INPAGE_ANCHOR.findall(_toc_block(text)))
    missing = [h for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE) if _github_slug(h) not in anchors]
    assert not missing, "headings missing from the troubleshooting Contents list: {0}".format(missing)


def test_troubleshooting_anchors_resolve():
    """Every in-page `](#anchor)` matches a real heading's GitHub slug."""
    text = _read("docs/troubleshooting.md")
    slugs = {_github_slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, flags=re.MULTILINE)}
    broken = sorted({a for a in _INPAGE_ANCHOR.findall(text) if a not in slugs})
    assert not broken, "docs/troubleshooting.md links to nonexistent anchors: {0}".format(broken)
