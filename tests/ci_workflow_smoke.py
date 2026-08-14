#!/usr/bin/env python3
"""Verify CI keeps the release-critical cross-platform checks.

Module/package inventory and CLI subcommand lists are derived — not
hand-maintained here. See scripts/syntax_smoke.py and scripts/cli_help_smoke.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# Ensure repo root is importable when run as a script.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_SNIPPETS = {
    "linux runner": "ubuntu-latest",
    "macos runner": "macos-latest",
    "windows runner": "windows-latest",
    "oldest supported python": '"3.10"',
    "current smoke python": '"3.14"',
    "unit tests": 'python -W error::ResourceWarning -m pytest tests/ -m "not live" --cov=bambu_cli --cov-report=term-missing --cov-fail-under=86',
    "syntax smoke auto-discovery": "python scripts/syntax_smoke.py",
    "cli help smoke auto-discovery": "python scripts/cli_help_smoke.py",
    "release readiness smoke": "python tests/release_readiness_smoke.py",
    "python compatibility smoke": "python tests/python_compat_smoke.py",
    "dependency resolution smoke": "python tests/dependency_resolution_smoke.py",
    "privacy smoke": "python tests/privacy_smoke.py",
    "archive privacy smoke": "python tests/privacy_smoke.py --include-dist",
    "agent smoke": "python tests/agent_cli_smoke.py",
    "release readiness artifact cleanup": "Clean generated artifacts before release readiness",
    "release readiness removes pycache": "root.rglob('__pycache__')",
    "release readiness removes egg-info": "bambu_cli.egg-info",
    "release readiness removes build outputs": "'build', 'dist', 'wheelhouse'",
    "installed agent smoke env": "BAMBU_CLI: plate",
    "package contents smoke": "python tests/package_contents_smoke.py",
    "installed version smoke": "plate --version",
    "wheel no-deps reinstall": "--force-reinstall --no-deps --no-index --find-links wheelhouse",
    "sdist and wheel package smoke": "python -m build --sdist --wheel --outdir dist",
    # Typing: whole-package mypy (blocklist of residuals lives in pyproject.toml).
    # Version-pinned via `uvx mypy@X.Y.Z`, so match the invocation in two halves
    # instead of one literal that a version bump would break.
    "mypy whole-package blocklist gate": "mypy@",
    "mypy whole-package target": "-p bambu_cli",
    "weekly scheduled run": "schedule:",
    "reusable by release.yml": "workflow_call:",
}

FORBIDDEN_SNIPPETS = {
    "non-portable Python heredoc": "python - <<",
    # Hand-maintained module lists must not return (Phase 2 Stage A).
    "hand-maintained py_compile list": "python -m py_compile bambu_cli/",
}


def _cli_subcommand_names() -> list[str]:
    from bambu_cli.cli import build_parser

    parser = build_parser()
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" or action.__class__.__name__ == "_SubParsersAction":
            choices = getattr(action, "choices", None) or {}
            return sorted(choices.keys())
    raise RuntimeError("could not locate subparsers on build_parser()")


def _package_modules() -> list[Path]:
    root = ROOT / "bambu_cli"
    return sorted(p for p in root.rglob("*.py") if p.is_file())


USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)


def _unpinned_action_refs() -> list[str]:
    """Every third-party action must be pinned to a 40-hex commit SHA.

    Mutable tags/branches (notably pypa/gh-action-pypi-publish@release/v1 in the
    job holding `id-token: write`) let an upstream compromise reach our PyPI
    trusted-publishing token.
    """
    workflow_dir = ROOT / ".github" / "workflows"
    files = sorted(list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml")))
    bad = []
    for wf in files:
        for ref in USES_RE.findall(wf.read_text(encoding="utf-8")):
            if ref.startswith("./"):  # local reusable workflow: no SHA needed
                continue
            _, _, rev = ref.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", rev):
                bad.append(f"{wf.name}: {ref}")
    return bad


def main():
    text = WORKFLOW.read_text(encoding="utf-8")
    missing = [label for label, snippet in REQUIRED_SNIPPETS.items() if snippet not in text]
    forbidden = [label for label, snippet in FORBIDDEN_SNIPPETS.items() if snippet in text]

    # CLI subcommands: parser is SSOT; help smoke script walks the same tree.
    try:
        commands = _cli_subcommand_names()
    except Exception as exc:
        raise SystemExit(f"could not load CLI subcommands from parser: {exc}") from exc
    if not commands:
        raise SystemExit("CLI parser reported zero subcommands")

    modules = _package_modules()
    if not modules:
        raise SystemExit("discovered zero bambu_cli modules")

    unpinned = _unpinned_action_refs()
    if unpinned:
        raise SystemExit("unpinned GitHub Action refs (must be 40-hex SHA): " + ", ".join(unpinned))

    if missing or forbidden:
        lines = []
        if missing:
            lines.append(f"missing required CI checks: {', '.join(missing)}")
        if forbidden:
            lines.append(f"forbidden CI patterns present: {', '.join(forbidden)}")
        raise SystemExit("; ".join(lines))

    print(f"ci workflow smoke ok ({len(commands)} CLI commands from parser, {len(modules)} package modules discovered)")


if __name__ == "__main__":
    main()
