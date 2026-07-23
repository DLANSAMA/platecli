#!/usr/bin/env python3
"""Verify advertised Python support can resolve install dependencies from pyproject.toml."""

import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_FLOOR = "3.9"
REQUIRED_PACKAGES = {"paho-mqtt", "zeroconf", "rich"}


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def check_output_contains_dependencies(output):
    lowered = output.lower()
    missing = sorted(package for package in REQUIRED_PACKAGES if package not in lowered)
    if missing:
        raise SystemExit(f"dependency resolver output did not include required packages: {missing}")


def _project_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"^\[project\]\s*$(.*?)(?:^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not section:
        raise SystemExit("pyproject.toml is missing [project]")
    dependencies = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", section.group(1), re.MULTILINE | re.DOTALL)
    if not dependencies:
        raise SystemExit("pyproject.toml is missing project dependencies")
    return {
        match.group(1).strip() for match in re.finditer(r'^\s*"([^"]+)"\s*,?\s*$', dependencies.group(1), re.MULTILINE)
    }


def check_declared_dependencies():
    project_deps = _project_dependencies()
    lowered = {dependency.lower().split(">", 1)[0].split("=", 1)[0].split("<", 1)[0] for dependency in project_deps}
    missing = sorted(package for package in REQUIRED_PACKAGES if package not in lowered)
    if missing:
        raise SystemExit(f"pyproject.toml dependencies missing required packages: {missing}")


def check_with_uv():
    # Use a temp *directory* rather than NamedTemporaryFile: on Windows the latter
    # holds an exclusive lock on the open handle, so uv cannot persist/rename its
    # output over it ("Access is denied. (os error 5)"). A path inside a temp dir
    # is not held open, so uv can write it freely on every platform.
    with tempfile.TemporaryDirectory(prefix="bambu-deps-py38-") as tmpdir:
        output_path = Path(tmpdir) / "requirements.txt"
        result = run_command(
            [
                "uv",
                "pip",
                "compile",
                "pyproject.toml",
                "--python-version",
                PYTHON_FLOOR,
                "--universal",
                "--no-emit-package",
                "platecli",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(output_path),
            ]
        )
        if result.returncode != 0:
            raise SystemExit(result.stderr + result.stdout)
        text = output_path.read_text(encoding="utf-8")
    check_output_contains_dependencies(text)


def check_with_pip():
    result = run_command(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--python-version",
            PYTHON_FLOOR,
            "--only-binary=:all:",
            ".",
        ]
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr + result.stdout)
    check_output_contains_dependencies(result.stderr + result.stdout)


def main():
    check_declared_dependencies()
    if shutil.which("uv"):
        check_with_uv()
    else:
        check_with_pip()
    print("dependency resolution smoke ok")


if __name__ == "__main__":
    main()
