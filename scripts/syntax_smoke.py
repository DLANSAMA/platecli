#!/usr/bin/env python3
"""Compile every runtime module under bambu_cli/ (auto-discovered).

Also compiles the legacy scripts/bambu.py entry and a fixed set of CI smoke
modules under tests/. Replaces the hand-maintained py_compile file list in
ci.yml — adding a package module no longer requires editing the workflow.
"""

from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Extra non-package paths still syntax-checked in CI (not auto-discoverable
# as package modules, but required for release/agent smoke).
EXTRA_PATHS = (
    "scripts/bambu.py",
    "scripts/__init__.py",
    "tests/bambu_test_base.py",
    "tests/agent_cli_smoke.py",
    "tests/ci_workflow_smoke.py",
    "tests/dependency_resolution_smoke.py",
    "tests/live_printer_smoke.py",
    "tests/package_contents_smoke.py",
    "tests/privacy_smoke.py",
    "tests/python_compat_smoke.py",
    "tests/release_readiness_smoke.py",
    "tests/test_config_and_logging.py",
    "tests/test_protocol_clients.py",
    "tests/test_cli_entry.py",
    "tests/test_printer_commands.py",
    "tests/test_slice_cmd.py",
    "tests/test_download_cmd.py",
    "tests/test_camera_cmd.py",
    "tests/test_doctor_and_safety.py",
)


def package_modules() -> list[Path]:
    """All .py files under bambu_cli/, sorted for stable output."""
    root = ROOT / "bambu_cli"
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def all_targets() -> list[Path]:
    targets = package_modules()
    for rel in EXTRA_PATHS:
        path = ROOT / rel
        if path.is_file():
            targets.append(path)
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
