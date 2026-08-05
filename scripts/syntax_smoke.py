#!/usr/bin/env python3
"""Compile every Python file in the repo that ships or runs in CI.

All of bambu_cli/, scripts/ and tests/ are auto-discovered. The tests/ list used
to be a hand-curated sample, which went stale the moment a test module was
renamed or split — the same failure mode CLAUDE.md warns about for package and
help-command inventories. Nothing here is hand-maintained now.
"""

from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _discover(*dirs: str) -> list[Path]:
    """Every .py under the given top-level directories, sorted."""
    out: list[Path] = []
    for name in dirs:
        base = ROOT / name
        if base.is_dir():
            out += [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def package_modules() -> list[Path]:
    """All .py files under bambu_cli/, sorted for stable output."""
    root = ROOT / "bambu_cli"
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def all_targets() -> list[Path]:
    targets = package_modules() + _discover("scripts", "tests")
    # de-dupe while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in targets:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def main() -> int:
    failed: list[str] = []
    compiled = 0
    for path in all_targets():
        try:
            py_compile.compile(str(path), doraise=True)
            compiled += 1
        except py_compile.PyCompileError as exc:
            failed.append(f"{path.relative_to(ROOT)}: {exc}")
    if failed:
        sys.stderr.write("syntax smoke failed:\n")
        for line in failed:
            sys.stderr.write(f"  {line}\n")
        return 1
    print(f"syntax smoke ok ({compiled} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
