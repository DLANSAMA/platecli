"""scripts/check_layers.py must see every module an import statement can bind.

A ``from`` import names a base and a list of names, and a name can itself be a
submodule. Judging the base alone let ``from bambu_cli import protocols`` (base
is the bare package, which owns no unit) and ``from bambu_cli.printables import
client`` (base is the public package, the name is the sealed module) pass the
checker. These tests pin the resolution on the AST, independent of the tree.
"""

import ast
from pathlib import Path

from scripts import check_layers

PKG = check_layers.PKG


def _from_node(src: str) -> ast.ImportFrom:
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.ImportFrom)
    return node


def test_relative_dotdot_import_of_a_sibling_package_is_an_edge():
    file = PKG / "slicer" / "estimate.py"
    targets = check_layers.import_targets(file, _from_node("from .. import protocols"))
    assert "bambu_cli.protocols" in targets
    assert {check_layers.unit_of(t) for t in targets} >= {"protocols"}


def test_absolute_import_of_a_sibling_package_from_the_bare_package_is_an_edge():
    file = PKG / "slicer" / "estimate.py"
    targets = check_layers.import_targets(file, _from_node("from bambu_cli import protocols"))
    assert check_layers.unit_of("bambu_cli") is None  # the base alone owns no unit
    assert "protocols" in {check_layers.unit_of(t) for t in targets}


def test_sealed_module_imported_by_name_from_its_package_is_visible():
    file = PKG / "download" / "naming.py"
    targets = check_layers.import_targets(file, _from_node("from bambu_cli.printables import client"))
    assert "bambu_cli.printables.client" in targets
    assert "bambu_cli.printables.client" in check_layers.SEALED


def test_same_package_relative_import_stays_inside_its_unit():
    file = PKG / "slicer" / "estimate.py"
    targets = check_layers.import_targets(file, _from_node("from . import cmd"))
    assert {check_layers.unit_of(t) for t in targets} == {"slicer"}


def test_relative_import_from_a_subpackage_init_resolves_to_that_package():
    file = PKG / "slicer" / "__init__.py"
    assert check_layers.absolute_module(file, _from_node("from .cmd import x")) == "bambu_cli.slicer.cmd"
    assert check_layers.absolute_module(file, _from_node("from ..protocols import ftps")) == "bambu_cli.protocols"


def test_relative_import_escaping_the_tree_is_not_an_edge():
    file = PKG / "slicer" / "estimate.py"
    assert check_layers.absolute_module(file, _from_node("from .... import x")) is None
    assert check_layers.import_targets(file, _from_node("from .... import x")) == []


def test_plain_import_statements_are_unchanged():
    file = Path(PKG / "cli.py")
    node = ast.parse("import bambu_cli.printer, os").body[0]
    assert check_layers.import_targets(file, node) == ["bambu_cli.printer"]


def test_the_checker_passes_on_the_real_tree():
    assert check_layers.main() == 0
