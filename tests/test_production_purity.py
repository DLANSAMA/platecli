"""Production code must not know it is being tested.

The CI grep catches the two classic shapes (``isinstance(..., Mock)`` and
``"unittest" in sys.modules``). This test closes the rest on the AST, so a
docstring or comment may still *mention* a test double but code cannot import
or name one: no import of ``unittest`` / ``pytest`` / ``mock``, no use of a
``Mock`` class, and no ``sys.modules`` probe for a test framework.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "bambu_cli"

TEST_MODULES = {"unittest", "pytest", "mock", "_pytest"}
TEST_NAMES = {"Mock", "MagicMock", "AsyncMock", "NonCallableMock", "PropertyMock", "patch", "create_autospec"}


def _is_modules_probe(node: ast.Constant) -> bool:
    parent = getattr(node, "_parent", None)
    return isinstance(parent, (ast.Compare, ast.Subscript))


def _scan(source: str) -> list[str]:
    tree = ast.parse(source)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in TEST_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in TEST_MODULES:
                found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Name) and node.id in TEST_NAMES:
            found.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in TEST_NAMES:
            found.append(f"attribute {node.attr}")
        elif isinstance(node, ast.Constant) and node.value in TEST_MODULES and _is_modules_probe(node):
            found.append(f"probe for {node.value!r}")
    return found


def test_no_production_module_references_a_test_framework():
    bad = {}
    for path in sorted(PKG.rglob("*.py")):
        hits = _scan(path.read_text(encoding="utf-8"))
        if hits:
            bad[str(path.relative_to(PKG.parent))] = hits
    assert not bad, f"test-awareness in production code: {bad}"


@pytest.mark.parametrize(
    "source",
    [
        "import unittest",
        "from unittest.mock import MagicMock",
        "import pytest",
        "x = MagicMock()",
        "import unittest.mock as m\nm.patch('a')",
        "import sys\nif 'unittest' in sys.modules: pass",
        "import sys\nsys.modules['pytest']",
    ],
)
def test_scanner_flags_the_shapes_it_exists_to_catch(source):
    assert _scan(source)


def test_scanner_ignores_docstrings_and_comments():
    assert not _scan('"""Tests may install a MagicMock here."""\n# pytest capture\nx = 1\n')
