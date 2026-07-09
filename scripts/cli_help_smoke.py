#!/usr/bin/env python3
"""Run --help for every subcommand registered on the CLI parser.

The argparse tree (bambu_cli.cli.build_parser) is the single source of truth
for command names. Adding a subcommand automatically covers it here — no
parallel list in ci.yml or ci_workflow_smoke.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cli_subcommand_names() -> list[str]:
    """Return sorted top-level subcommand names from the live argparse tree."""
    # Import after path is usable as package root when run from repo.
    sys.path.insert(0, str(ROOT))
    from bambu_cli.cli import build_parser  # noqa: E402

    parser = build_parser()
    # argparse stores subparsers under _subparsers action
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" or action.__class__.__name__ == "_SubParsersAction":
            choices = getattr(action, "choices", None) or {}
            return sorted(choices.keys())
    raise SystemExit("cli_help_smoke: could not locate subparsers on build_parser()")


def main() -> int:
    script = ROOT / "scripts" / "bambu.py"
    python = sys.executable
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    # Ensure repo checkout is importable even without an editable install.
    env["PYTHONPATH"] = str(ROOT) + (
        __import__("os").pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    # Top-level help + version first.
    for args in ([str(script), "--help"], [str(script), "--version"]):
        result = subprocess.run([python, *args], cwd=str(ROOT), capture_output=True, text=True, env=env)
        if result.returncode != 0:
            sys.stderr.write(f"cli help smoke failed: {' '.join(args)}\n{result.stderr}\n")
            return 1

    names = cli_subcommand_names()
    for name in names:
        result = subprocess.run(
            [python, str(script), name, "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            sys.stderr.write(f"cli help smoke failed: {name} --help\n{result.stderr}\n")
            return 1
        # Basic sanity: help mentions the command name or Usage
        out = (result.stdout or "") + (result.stderr or "")
        if "usage" not in out.lower() and name not in out:
            sys.stderr.write(f"cli help smoke: empty/odd help for {name}\n{out}\n")
            return 1

    print(f"cli help smoke ok ({len(names)} subcommands: {', '.join(names)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
