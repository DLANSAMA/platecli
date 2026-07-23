#!/usr/bin/env python3
"""Fail release checks if local personal identifiers leak into source or release archives."""

import argparse
import getpass
import os
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

BASE_EXCLUDED_DIRS = {
    ".claude",
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".mutmut-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bambu_cli.egg-info",
    "build",
    "dist",
    "mutants",
    "wheelhouse",
}

GENERIC_LOCAL_NAMES = {
    "admin",
    "administrator",
    "builder",
    "ci",
    "github",
    "runner",
    "root",
    "test",
    "user",
    "jules",
}

FORBIDDEN_LOCAL_ARTIFACT_NAMES = {
    "access_code",
    "config.json",
    "printer_capabilities.json",
    "printer_snapshot.jpg",
}

FORBIDDEN_LOCAL_ARTIFACT_SUFFIXES = {
    ".3mf",
    ".gcode",
}


def local_identity_patterns():
    """Build local-identity patterns without storing a developer's identity in repo."""
    names = {
        getpass.getuser(),
        os.environ.get("USER", ""),
        os.environ.get("USERNAME", ""),
        os.environ.get("LOGNAME", ""),
        Path.home().name,
    }
    for key in ("user.name", "user.email"):
        try:
            value = subprocess.run(
                ["git", "config", "--get", key],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            value = ""
        if value:
            names.add(value)
            names.update(part for part in re.split(r"[^A-Za-z0-9_-]+", value) if part)

    # Exclude repository owner from checks to avoid false positives on repo URLs
    try:
        remote_url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        remote_url = ""

    excluded_names = set()
    if remote_url:
        match = re.search(r"github\.com[:/]([^/]+)", remote_url)
        if match:
            excluded_names.add(match.group(1).lower())

    names = {
        name.strip()
        for name in names
        if name
        and len(name.strip()) >= 4
        and name.strip().lower() not in GENERIC_LOCAL_NAMES
        and name.strip().lower() not in excluded_names
    }

    patterns = {
        "email address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "credential-bearing URL": re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/@]+:[^\s/@]+@[^\s/]+\b"),
        "OpenAI API key": re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"),
        "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "private key": re.compile("BEGIN " + r"(?:RSA|OPENSSH|EC|PRIVATE) KEY"),
    }
    if names:
        patterns["local account name"] = re.compile("|".join(re.escape(name) for name in names), re.IGNORECASE)
        patterns["absolute local home path"] = re.compile(
            "|".join(re.escape(prefix + name) for name in names for prefix in ("/home/", "/Users/", "\\Users\\")),
            re.IGNORECASE,
        )
    return patterns


def iter_files(include_dist=False):
    excluded_dirs = set(BASE_EXCLUDED_DIRS)
    if include_dist:
        excluded_dirs.discard("dist")
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in excluded_dirs]
        for filename in filenames:
            if filename == ".git":
                # In a git worktree, .git is a file pointing at the local checkout path.
                continue
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def _artifact_name_from_label(label):
    """Return the final path component for source paths or archive member labels."""
    return str(label).rsplit("!", 1)[-1].replace("\\", "/").rsplit("/", 1)[-1]


def check_forbidden_local_artifact(label, findings):
    name = _artifact_name_from_label(label)
    suffix = Path(name).suffix.lower()
    if name in FORBIDDEN_LOCAL_ARTIFACT_NAMES:
        findings.append(f"{label}: local printer config/secret/artifact should not be committed")
    if suffix in FORBIDDEN_LOCAL_ARTIFACT_SUFFIXES:
        findings.append(f"{label}: generated printer-ready file should not be committed")


def scan_text(label, text, patterns, findings):
    for pattern_label, pattern in patterns.items():
        if pattern.search(label):
            findings.append(f"{label}: path looks like {pattern_label}")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{label}:{line}: content looks like {pattern_label}")


def iter_archive_members(dist_dir):
    archive_paths = sorted(dist_dir.glob("*.tar.gz")) + sorted(dist_dir.glob("*.whl"))
    if not archive_paths:
        raise SystemExit(f"--include-dist requested but no release archives were found in {dist_dir}")
    for archive_path in sorted(dist_dir.glob("*.tar.gz")):
        try:
            with tarfile.open(archive_path) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        continue
                    label = f"{archive_path.relative_to(ROOT)}!{member.name}"
                    try:
                        text = stream.read().decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    yield label, text
        except tarfile.ReadError:
            continue
    for archive_path in sorted(dist_dir.glob("*.whl")):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    label = f"{archive_path.relative_to(ROOT)}!{name}"
                    try:
                        text = archive.read(name).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    yield label, text
        except zipfile.BadZipFile:
            continue


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-dist",
        action="store_true",
        help="Also scan built sdist/wheel archive contents under dist/",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    findings = []
    patterns = local_identity_patterns()
    for path in iter_files(include_dist=args.include_dist):
        relpath = path.relative_to(ROOT)
        check_forbidden_local_artifact(relpath.as_posix(), findings)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scan_text(relpath.as_posix(), text, patterns, findings)
    if args.include_dist:
        for label, text in iter_archive_members(ROOT / "dist"):
            check_forbidden_local_artifact(label, findings)
            scan_text(label, text, patterns, findings)

    if findings:
        preview = "\n".join(findings[:25])
        extra = "" if len(findings) <= 25 else f"\n... and {len(findings) - 25} more"
        raise SystemExit(f"Personal-info smoke failed:\n{preview}{extra}")
    print("privacy smoke ok")


if __name__ == "__main__":
    main()
