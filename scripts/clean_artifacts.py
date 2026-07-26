#!/usr/bin/env python3
"""Remove generated build/test artifacts from the working tree.

Run from the repository root; everything is resolved relative to the current
directory. Removed by default:

  * every ``__pycache__`` directory and every ``*.pyc`` file
  * the tool caches ``.pytest_cache``, ``.mypy_cache``, ``.ruff_cache``
  * the build outputs ``build``, ``dist``, ``wheelhouse``
  * ``bambu_cli.egg-info``, ``bambu_local_cli.egg-info``, ``platecli.egg-info``
  * root-level ``.bambu-download-*.zip`` leftovers

The walk never descends into ``.git``, ``.venv``, ``venv``, ``.claude`` or
``node_modules``, so a virtualenv's own caches are left alone.

**The developer virtualenv is not touched unless you ask for it.** Pass
``--venv`` (or ``--all``) to remove ``.venv``; interactive runs confirm first.
Removal is verified: a directory that survives (a locked file on Windows is the
usual cause) is reported with a non-zero exit rather than silently left in a
half-deleted state.

This script is used by CI (see .github/workflows/ci.yml) to clear generated
paths before the release readiness smoke. CI does not pass ``--venv``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Directories whose contents are never artifacts we own.
SKIP_DIRS = frozenset({".git", ".venv", "venv", ".claude", "node_modules"})

# Fixed-name directories removed from the repo root.
ARTIFACT_DIRS = (
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "wheelhouse",
    "bambu_cli.egg-info",
    "bambu_local_cli.egg-info",
    "platecli.egg-info",
)

VENV_DIR = ".venv"


def _exists(path: Path) -> bool:
    """True for anything present, including a broken symlink."""
    return path.exists() or path.is_symlink()


def _clear_readonly(path: Path) -> None:
    """Best-effort: drop read-only bits that block rmtree on Windows."""
    for target in (path, *path.rglob("*")):
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass


def remove_tree(path: Path) -> str | None:
    """Remove a directory tree. Return an error string, or None on success.

    Deliberately not ``ignore_errors=True``: a partially removed tree (most
    often ``.venv`` with a locked ``python.exe``) is worse than no removal at
    all, so failures are surfaced instead of swallowed.
    """
    if not _exists(path):
        return None
    try:
        shutil.rmtree(path)
    except OSError:
        _clear_readonly(path)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
    if _exists(path):
        return "still present after removal (files may be in use)"
    return None


def remove_file(path: Path) -> str | None:
    """Remove a single file. Return an error string, or None on success."""
    if not _exists(path):
        return None
    try:
        path.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def find_bytecode(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (``__pycache__`` dirs, loose ``*.pyc`` files) under *root*."""
    caches: list[Path] = []
    pyc_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        pruned = []
        for name in dirnames:
            if name in SKIP_DIRS:
                continue
            if name == "__pycache__":
                caches.append(Path(dirpath) / name)
                continue  # collected, do not descend
            pruned.append(name)
        dirnames[:] = pruned
        for name in filenames:
            if name.endswith(".pyc"):
                pyc_files.append(Path(dirpath) / name)
    return caches, pyc_files


def collect_targets(root: Path, include_venv: bool) -> tuple[list[Path], list[Path]]:
    """Return (directories, files) that exist and are slated for removal."""
    caches, pyc_files = find_bytecode(root)

    dirs = list(caches)
    names = list(ARTIFACT_DIRS)
    if include_venv:
        names.append(VENV_DIR)
    dirs.extend(root / name for name in names if _exists(root / name))

    files = list(pyc_files)
    files.extend(p for p in sorted(root.glob(".bambu-download-*.zip")) if p.is_file())
    return dirs, files


def describe(root: Path, dirs: list[Path], files: list[Path]) -> None:
    total = len(dirs) + len(files)
    if not total:
        print("clean_artifacts: nothing to remove")
        return
    print(f"clean_artifacts: removing {total} path(s) under {root}:")
    for path in dirs:
        print(f"  [dir]  {_display(root, path)}")
    for path in files:
        print(f"  [file] {_display(root, path)}")


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def confirm_venv() -> bool:
    """Ask before deleting the virtualenv; True means go ahead.

    No ``isatty()`` gate: on Windows ``sys.stdin.isatty()`` is True even for the
    NUL device, so it does not distinguish a human from a scripted run. Instead
    the prompt is always offered and EOF (nobody there to answer) falls back to
    the explicit ``--venv``/``--all`` flag — a scripted run should not silently
    do nothing. Pass ``--yes`` to skip the prompt entirely.
    """
    print(
        f"\n{VENV_DIR} is your development virtualenv. Removing it means re-running "
        "`uv sync` (or `uv sync --extra test`) before the next `uv run`."
    )
    try:
        answer = input(f"Remove {VENV_DIR}? [y/N] ")
    except EOFError:
        print("no terminal attached; proceeding on the explicit --venv flag")
        return True
    return answer.strip().lower() in {"y", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clean_artifacts.py",
        description=(
            "Remove generated build/test artifacts (__pycache__, *.pyc, tool caches, "
            "build/dist/wheelhouse, *.egg-info, .bambu-download-*.zip). "
            f"{VENV_DIR} is left alone unless --venv/--all is given."
        ),
    )
    parser.add_argument(
        "--venv",
        action="store_true",
        help=f"also remove {VENV_DIR} (asks for confirmation on a terminal)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_",
        help=f"remove everything, including {VENV_DIR}; same as --venv",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=f"skip the interactive {VENV_DIR} confirmation",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="list what would be removed and exit without deleting anything",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    include_venv = args.venv or args.all_
    root = Path(".").resolve()

    dirs, files = collect_targets(root, include_venv)
    describe(root, dirs, files)

    if args.dry_run:
        print("clean_artifacts: dry run, nothing removed")
        return 0

    venv_path = root / VENV_DIR
    if include_venv and venv_path in dirs and not args.yes:
        if not confirm_venv():
            dirs = [p for p in dirs if p != venv_path]
            print(f"clean_artifacts: keeping {VENV_DIR}")

    failures: list[str] = []
    for path in dirs:
        error = remove_tree(path)
        if error:
            failures.append(f"{_display(root, path)}: {error}")
    for path in files:
        error = remove_file(path)
        if error:
            failures.append(f"{_display(root, path)}: {error}")

    if failures:
        sys.stderr.write("clean_artifacts: failed to remove:\n")
        for line in failures:
            sys.stderr.write(f"  {line}\n")
        if any(line.startswith(VENV_DIR) for line in failures):
            sys.stderr.write(
                f"\n{VENV_DIR} may now be incomplete. Close anything using it "
                "(running python.exe, an editor, another shell) and recreate it:\n"
                f"  Remove-Item {VENV_DIR} -Recurse -Force   # PowerShell\n"
                f"  rm -rf {VENV_DIR}                        # POSIX\n"
                "  uv sync --extra test\n"
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
