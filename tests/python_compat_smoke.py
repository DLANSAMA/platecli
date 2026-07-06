#!/usr/bin/env python3
"""Guard the advertised Python 3.9+ support from the current interpreter."""
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


def main():
    failures = []
    for path in sorted(SOURCE_FILES):
        try:
            ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path.relative_to(ROOT)),
                feature_version=PY39_FEATURE_VERSION,
            )
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    if failures:
        raise SystemExit("Python 3.9 syntax compatibility failed:\n" + "\n".join(failures))
    print("python compatibility smoke ok")


if __name__ == "__main__":
    main()
