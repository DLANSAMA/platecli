#!/usr/bin/env python3
"""Guard the objective-level release checks for the Bambu CLI skill."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    ".github/workflows/ci.yml",
    ".gitignore",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "AGENTS.md",
    "bambu_cli/__init__.py",
    "bambu_cli/bambu.py",
    "pyproject.toml",
    "scripts/__init__.py",
    "scripts/bambu.py",
    "tests/agent_cli_smoke.py",
    "tests/ci_workflow_smoke.py",
    "tests/dependency_resolution_smoke.py",
    "tests/live_printer_smoke.py",
    "tests/package_contents_smoke.py",
    "tests/privacy_smoke.py",
    "tests/python_compat_smoke.py",
    "tests/release_readiness_smoke.py",
    "tests/bambu_test_base.py",
    "tests/test_config_and_logging.py",
    "tests/test_protocol_clients.py",
    "tests/test_cli_entry.py",
    "tests/test_printer_commands.py",
    "tests/test_slice_cmd.py",
    "tests/test_download_cmd.py",
    "tests/test_camera_cmd.py",
    "tests/test_doctor_and_safety.py",
}

FORBIDDEN_RELEASE_FILES = {
    # This repository used to carry a stale PR blurb for a tiny historical
    # lint cleanup. A release should not ship misleading change metadata.
    "pr_description.md",
}

FORBIDDEN_GENERATED_PATHS = {
    "bin",
    "lib",
    "lib64",
    "pyvenv.cfg",
    ".venv",
    "build",
    "dist",
    "wheelhouse",
    "bambu_cli.egg-info",
    "platecli.egg-info",
}

FORBIDDEN_GENERATED_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "wheelhouse",
    # uv.lock is intentionally allowed: it is committed so CI's
    # `uv sync --frozen` has a lockfile to install from.
}

OBJECTIVE_SNIPPETS = {
    "README.md": {
        "Runs on **Linux, macOS, and Windows**",
        "Use `job` when an agent or user gives either a website URL or a local file path",
    },
    "AGENTS.md": {"Runs on Linux, macOS, and Windows."},
    "pyproject.toml": {'plate = "bambu_cli.bambu:main"'},
}

FORBIDDEN_SNIPPETS = {
    "AGENTS.md": {"/tmp/", "~/.bambu-cli"},
    "README.md": {"python %USERPROFILE%", "python3 ~/.bambu-cli/workspace/skills/bambu-cli/scripts/bambu.py"},
}


def read_relpath(relpath):
    return (ROOT / relpath).read_text(encoding="utf-8")


def iter_generated_paths():
    import os

    is_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or ".venv" in path.parts or "venv" in path.parts or ".claude" in path.parts:
            continue
        relpath = path.relative_to(ROOT).as_posix()
        name = path.name
        if is_ci and (relpath == ".venv" or name == ".venv"):
            continue
        if relpath in FORBIDDEN_GENERATED_PATHS:
            yield relpath
        elif name in FORBIDDEN_GENERATED_NAMES:
            yield relpath
        elif name.endswith(".egg-info"):
            yield relpath
        elif name.endswith(".pyc"):
            yield relpath
        elif name.startswith(".bambu-download-") and name.endswith(".zip"):
            yield relpath


def main():
    missing_files = sorted(relpath for relpath in REQUIRED_FILES if not (ROOT / relpath).is_file())
    if missing_files:
        print(f"Missing required files: {missing_files}")
        import sys

        sys.exit(1)
    forbidden_files = sorted(relpath for relpath in FORBIDDEN_RELEASE_FILES if (ROOT / relpath).exists())
    generated_paths = sorted(set(iter_generated_paths()))
    missing_snippets = []
    forbidden_snippets = []
    for relpath, snippets in OBJECTIVE_SNIPPETS.items():
        text = read_relpath(relpath)
        for snippet in snippets:
            if snippet not in text:
                missing_snippets.append(f"{relpath}: {snippet}")
    for relpath, snippets in FORBIDDEN_SNIPPETS.items():
        text = read_relpath(relpath)
        for snippet in snippets:
            if snippet in text:
                forbidden_snippets.append(f"{relpath}: {snippet}")

    if missing_files or forbidden_files or generated_paths or missing_snippets or forbidden_snippets:
        lines = []
        if missing_files:
            lines.append(f"missing release files: {missing_files}")
        if forbidden_files:
            lines.append(f"forbidden stale release files present: {forbidden_files}")
        if generated_paths:
            lines.append(f"generated local artifacts present: {generated_paths}")
        if missing_snippets:
            lines.append(f"missing objective snippets: {missing_snippets}")
        if forbidden_snippets:
            lines.append(f"forbidden objective snippets: {forbidden_snippets}")
        raise SystemExit("; ".join(lines))
    print("release readiness smoke ok")


if __name__ == "__main__":
    main()
