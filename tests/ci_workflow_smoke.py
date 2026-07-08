#!/usr/bin/env python3
"""Verify CI keeps the release-critical cross-platform checks."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


REQUIRED_SNIPPETS = {
    "linux runner": "ubuntu-latest",
    "macos runner": "macos-latest",
    "windows runner": "windows-latest",
    "oldest supported python": '"3.9"',
    "current smoke python": '"3.14"',
    "unit tests": "python -W error::ResourceWarning -m pytest tests/ -m \"not live\" --cov=bambu_cli --cov-report=term-missing --cov-fail-under=92",
    "runtime package syntax": "bambu_cli/bambu.py",
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
    "installed agent smoke env": "BAMBU_CLI: bambu-cli",
    "package contents smoke": "python tests/package_contents_smoke.py",
    "script version smoke": "python scripts/bambu.py --version",
    "installed version smoke": "bambu-cli --version",
    "wheel no-deps reinstall": "--force-reinstall --no-deps --no-index --find-links wheelhouse",
    "sdist and wheel package smoke": "python -m build --sdist --wheel --outdir dist",
}

REQUIRED_HELP_COMMANDS = {
    "config",
    "delete",
    "doctor",
    "download",
    "files",
    "gcode",
    "job",
    "light",
    "pause",
    "preflight",
    "print",
    "resume",
    "send",
    "setup",
    "slice",
    "snapshot",
    "status",
    "stop",
    "upload",
}

FORBIDDEN_SNIPPETS = {
    "non-portable Python heredoc": "python - <<",
}


def main():
    text = WORKFLOW.read_text(encoding="utf-8")
    missing = [label for label, snippet in REQUIRED_SNIPPETS.items() if snippet not in text]
    missing_help = [
        command for command in sorted(REQUIRED_HELP_COMMANDS)
        if f"python scripts/bambu.py {command} --help" not in text
    ]
    forbidden = [label for label, snippet in FORBIDDEN_SNIPPETS.items() if snippet in text]
    if missing or missing_help or forbidden:
        lines = []
        if missing:
            lines.append(f"missing required CI checks: {', '.join(missing)}")
        if missing_help:
            lines.append(f"missing CLI help checks: {', '.join(missing_help)}")
        if forbidden:
            lines.append(f"forbidden CI patterns present: {', '.join(forbidden)}")
        raise SystemExit("; ".join(lines))
    print("ci workflow smoke ok")


if __name__ == "__main__":
    main()
