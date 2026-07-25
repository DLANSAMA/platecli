#!/usr/bin/env python3
"""Guard the advertised Python 3.9+ support from the current interpreter."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY39_FEATURE_VERSION = (3, 9)

SOURCE_FILES = [
    *(ROOT / "bambu_cli").rglob("*.py"),
    ROOT / "scripts" / "bambu.py",
    ROOT / "scripts" / "__init__.py",
    *(ROOT / "tests").glob("*.py"),
]

# AST node types that are valid type-annotation operands for the BitOr check.
_TYPE_NODE_TYPES = (ast.Name, ast.Attribute, ast.Subscript, ast.Constant)


def _has_future_annotations(tree: ast.Module) -> bool:
    """Return True if the module starts with 'from __future__ import annotations'."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
        ):
            return True
    return False


def _annotation_nodes(tree: ast.Module):
    """Yield (annotation_node, lineno) for every annotation in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation is not None:
                    yield arg.annotation, arg.annotation.lineno
            if node.args.vararg and node.args.vararg.annotation:
                yield node.args.vararg.annotation, node.args.vararg.annotation.lineno
            if node.args.kwarg and node.args.kwarg.annotation:
                yield node.args.kwarg.annotation, node.args.kwarg.annotation.lineno
            if node.returns is not None:
                yield node.returns, node.returns.lineno
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation, node.annotation.lineno


def _contains_bitor_union(node: ast.expr) -> bool:
    """Return True if this annotation (sub)node is a BitOr union like X | Y."""
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.BitOr)
        and isinstance(node.left, _TYPE_NODE_TYPES)
        and isinstance(node.right, _TYPE_NODE_TYPES)
    ):
        return True
    # Recurse into nested BinOps (e.g. int | str | None)
    return any(isinstance(child, ast.expr) and _contains_bitor_union(child) for child in ast.iter_child_nodes(node))


def _check_pep604_annotations(path: Path, text: str) -> list[str]:
    """Return a list of error strings for runtime-evaluated PEP 604 annotations."""
    try:
        tree = ast.parse(text, filename=str(path.relative_to(ROOT)))
    except SyntaxError:
        # Syntax errors are already caught by the main loop
        return []

    if _has_future_annotations(tree):
        return []

    errors = []
    rel = path.relative_to(ROOT)
    for ann_node, lineno in _annotation_nodes(tree):
        if _contains_bitor_union(ann_node):
            errors.append(
                f"{rel}:{lineno}: PEP 604 'X | Y' annotation requires Python 3.10+ "
                "at runtime — add 'from __future__ import annotations' or use "
                "typing.Optional / typing.Union"
            )
    return errors


def main():
    failures = []
    for path in sorted(SOURCE_FILES):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(
                text,
                filename=str(path.relative_to(ROOT)),
                feature_version=PY39_FEATURE_VERSION,
            )
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
            continue

        failures.extend(_check_pep604_annotations(path, text))

    if failures:
        raise SystemExit("Python 3.9 syntax compatibility failed:\n" + "\n".join(failures))
    print("python compatibility smoke ok")


if __name__ == "__main__":
    main()
