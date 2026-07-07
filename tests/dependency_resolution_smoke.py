#!/usr/bin/env python3
"""Verify advertised Python support can resolve install dependencies."""
import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_FLOOR = "3.9"
REQUIRED_PACKAGES = {"paho-mqtt", "zeroconf"}


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
    section = re.search(r'^\[project\]\s*$(.*?)(?:^\[|\Z)', text, re.MULTILINE | re.DOTALL)
    if not section:
        raise SystemExit("pyproject.toml is missing [project]")
    dependencies = re.search(r'^dependencies\s*=\s*\[(.*?)^\]', section.group(1), re.MULTILINE | re.DOTALL)
    if not dependencies:
        raise SystemExit("pyproject.toml is missing project dependencies")
    return {
        match.group(1).strip()
        for match in re.finditer(r'^\s*"([^"]+)"\s*,?\s*$', dependencies.group(1), re.MULTILINE)
    }


def _requirements_dependencies():
    dependencies = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            dependencies.add(line)
    return dependencies


def check_declared_dependencies_match():
    project_deps = _project_dependencies()
    requirements_deps = _requirements_dependencies()
    if project_deps != requirements_deps:
        raise SystemExit(
            "pyproject.toml dependencies do not match requirements.txt: "
            f"pyproject={sorted(project_deps)}, requirements={sorted(requirements_deps)}"
        )
    lowered = {dependency.lower().split(">", 1)[0].split("=", 1)[0].split("<", 1)[0] for dependency in project_deps}
    missing = sorted(package for package in REQUIRED_PACKAGES if package not in lowered)
    if missing:
        raise SystemExit(f"declared dependencies missing required packages: {missing}")


def check_with_uv():
    # Use a temp *directory* rather than NamedTemporaryFile: on Windows the latter
    # holds an exclusive lock on the open handle, so uv cannot persist/rename its
    # output over it ("Access is denied. (os error 5)"). A path inside a temp dir
    # is not held open, so uv can write it freely on every platform.
    with tempfile.TemporaryDirectory(prefix="bambu-deps-py38-") as tmpdir:
        output_path = Path(tmpdir) / "requirements.txt"
        result = run_command([
            "uv",
            "pip",
            "compile",
            "pyproject.toml",
            "--python-version",
            PYTHON_FLOOR,
            "--universal",
            "--no-emit-package",
            "bambu-local-cli",
            "--no-header",
            "--no-annotate",
            "--output-file",
            str(output_path),
        ])
        if result.returncode != 0:
            raise SystemExit(result.stderr + result.stdout)
        text = output_path.read_text(encoding="utf-8")
    check_output_contains_dependencies(text)


def check_with_pip():
    result = run_command([
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
    ])
    if result.returncode != 0:
        raise SystemExit(result.stderr + result.stdout)
    check_output_contains_dependencies(result.stderr + result.stdout)


def main():
    check_declared_dependencies_match()
    if shutil.which("uv"):
        check_with_uv()
    else:
        check_with_pip()
    print("dependency resolution smoke ok")


if __name__ == "__main__":
    main()
