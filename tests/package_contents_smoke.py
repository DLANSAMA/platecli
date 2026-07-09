#!/usr/bin/env python3
"""Verify release archives contain the files agents and users need."""

import re
import tarfile
import zipfile
from pathlib import Path


REQUIRED_SDIST_FILES = {
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "bambu_cli/__init__.py",
    "bambu_cli/bambu.py",
    "bambu_cli/cli.py",
    "bambu_cli/config.py",
    "bambu_cli/slicer/__init__.py",
    "bambu_cli/slicer/cmd.py",
    "bambu_cli/commands/__init__.py",
    "bambu_cli/commands/status.py",
    "bambu_cli/job/__init__.py",
    "bambu_cli/job/orchestrate.py",
    "bambu_cli/protocols/ftps.py",
    "bambu_cli/protocols/mqtt.py",
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

REQUIRED_WHEEL_FILES = {
    "bambu_cli/__init__.py",
    "bambu_cli/bambu.py",
    "bambu_cli/cli.py",
    "bambu_cli/config.py",
    "bambu_cli/slicer/__init__.py",
    "bambu_cli/slicer/cmd.py",
    "bambu_cli/commands/__init__.py",
    "bambu_cli/commands/status.py",
    "bambu_cli/job/__init__.py",
    "bambu_cli/job/orchestrate.py",
    "bambu_cli/protocols/ftps.py",
    "bambu_cli/protocols/mqtt.py",
}

FORBIDDEN_WHEEL_FILES = {
    "scripts/__init__.py",
    "scripts/bambu.py",
}

# Non-runtime files must not ship in the wheel (they bloat user installs).
FORBIDDEN_WHEEL_DATA_SUFFIXES = {
    "bambu_cli/README.md",
    "bambu_cli/AGENTS.md",
}

STATIC_METADATA_SNIPPETS = {
    "Summary: Unofficial local Bambu Lab printer control for agents and humans (not affiliated with Bambu Lab)",
    "Keywords: bambu,3d-printing,agent,cli,orcaslicer",
    "Requires-Python: >=3.9",
    "Requires-Dist: paho-mqtt",
    "Requires-Dist: zeroconf",
    "Classifier: Operating System :: MacOS",
    "Classifier: Operating System :: Microsoft :: Windows",
    "Classifier: Operating System :: POSIX :: Linux",
    "Classifier: Programming Language :: Python :: 3.9",
    "Classifier: Programming Language :: Python :: 3.12",
    "Classifier: Programming Language :: Python :: 3.13",
    "Classifier: Programming Language :: Python :: 3.14",
}

EXPECTED_TOP_LEVEL_NAMES = {"bambu_cli"}

REQUIRED_GITIGNORE_SNIPPETS = {
    "bin/",
    "lib/",
    "lib64",
    "pyvenv.cfg",
    # uv.lock is deliberately NOT here: it must stay committed because CI
    # installs with `uv sync --frozen`, which requires the tracked lockfile.
    "wheelhouse/",
    "dist/",
    "build/",
    "*.egg-info/",
    "__pycache__/",
    "config.json",
    "access_code",
    "printer_capabilities.json",
    "printer_snapshot.jpg",
    "*.3mf",
    "*.gcode",
}

REQUIRED_DOC_SNIPPETS = {
    "README.md": {
        "`--json` | Emit JSON for commands that support it; may appear before the subcommand",
        "`bambu-cli --json --version` emits",
        "STL > STEP > OBJ > 3MF > G-code",
        "--max-download-mb",
        "zero-or-positive slot indexes",
        "Runtime package used by installed command",
        "Compatibility wrapper for direct script usage",
        "ci_workflow_smoke.py",
        "python_compat_smoke.py",
        "release_readiness_smoke.py",
    },
    "AGENTS.md": {
        "STL > STEP/STP > OBJ > 3MF > G-code",
        "Agents may place `--json` before or after the subcommand",
        "bambu-cli --json --version",
        "--max-download-mb",
        "zero-or-positive integers",
    },
}

LICENSE_SNIPPETS = {
    "MIT License",
    "bambu-cli contributors",
    'THE SOFTWARE IS PROVIDED "AS IS"',
}


def _metadata_version():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml is missing project version")
    return match.group(1)


def _metadata_name():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml is missing project name")
    return match.group(1)


def _cli_version():
    """Runtime VERSION resolved from package metadata / pyproject (single source)."""
    from bambu_cli.constants import VERSION

    return VERSION


def _project_script_entry():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"^\[project\.scripts\]\s*$(.*?)(?:^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not section:
        raise SystemExit("pyproject.toml is missing [project.scripts]")
    match = re.search(r'^([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"', section.group(1), re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml is missing a console script entry")
    return match.group(1), match.group(2)


def check_version_consistency():
    # Canonical version lives only in pyproject.toml; constants.VERSION must resolve to it.
    metadata_version = _metadata_version()
    cli_version = _cli_version()
    if metadata_version != cli_version:
        raise SystemExit(
            f"version mismatch: pyproject.toml has {metadata_version}, bambu_cli.constants.VERSION is {cli_version}"
        )


def expected_metadata_snippets():
    return STATIC_METADATA_SNIPPETS | {
        f"Name: {_metadata_name()}",
        f"Version: {_metadata_version()}",
    }


def expected_entry_point_snippets():
    script_name, script_target = _project_script_entry()
    return {
        "[console_scripts]",
        f"{script_name} = {script_target}",
    }


def check_gitignore_release_artifacts():
    text = Path(".gitignore").read_text(encoding="utf-8")
    missing = sorted(snippet for snippet in REQUIRED_GITIGNORE_SNIPPETS if snippet not in text)
    if missing:
        raise SystemExit(f".gitignore missing generated artifact patterns: {missing}")


def check_agent_docs_current():
    missing = []
    for filename, snippets in REQUIRED_DOC_SNIPPETS.items():
        text = Path(filename).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                missing.append(f"{filename}: {snippet}")
    if missing:
        raise SystemExit(f"agent docs missing required snippets: {missing}")


def _archive_file_names(archive_path):
    try:
        with tarfile.open(archive_path) as tf:
            return {Path(name).as_posix().split("/", 1)[1] for name in tf.getnames() if "/" in name}
    except tarfile.ReadError:
        return set()


def _check_license_text(label, text):
    missing = sorted(snippet for snippet in LICENSE_SNIPPETS if snippet not in text)
    if missing:
        raise SystemExit(f"{label} license text missing required snippets: {missing}")


def check_sdist(dist_dir):
    tarballs = sorted(dist_dir.glob("bambu_local_cli-*.tar.gz"))
    if not tarballs:
        raise SystemExit(f"No source distribution found in {dist_dir}")
    archive_path = tarballs[-1]
    names = _archive_file_names(archive_path)
    missing = sorted(REQUIRED_SDIST_FILES - names)
    if missing:
        raise SystemExit(f"sdist missing required files: {missing}")
    try:
        with tarfile.open(archive_path) as tf:
            license_name = next((name for name in tf.getnames() if name.endswith("/LICENSE")), None)
            if license_name is None:
                raise SystemExit("sdist missing LICENSE")
            license_member = tf.extractfile(license_name)
            if license_member is None:
                raise SystemExit("sdist LICENSE could not be read")
            try:
                _check_license_text("sdist", license_member.read().decode("utf-8"))
            except UnicodeDecodeError:
                raise SystemExit("sdist LICENSE is not valid utf-8")
    except tarfile.ReadError:
        raise SystemExit(f"failed to read sdist archive {archive_path}")


def check_wheel(dist_dir):
    wheels = sorted(dist_dir.glob("bambu_local_cli-*.whl"))
    if not wheels:
        raise SystemExit(f"No wheel found in {dist_dir}")
    try:
        with zipfile.ZipFile(wheels[-1]) as zf:
            names = set(zf.namelist())
            metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
            entry_points_name = next((name for name in names if name.endswith(".dist-info/entry_points.txt")), None)
            top_level_name = next((name for name in names if name.endswith(".dist-info/top_level.txt")), None)
            # Accept both the legacy flat location and the PEP 639 location
            # (setuptools >= 77 writes license files under .dist-info/licenses/).
            license_name = next(
                (
                    name
                    for name in names
                    if name.endswith(".dist-info/LICENSE") or name.endswith(".dist-info/licenses/LICENSE")
                ),
                None,
            )
            if metadata_name is None:
                raise SystemExit("wheel missing METADATA")
            if entry_points_name is None:
                raise SystemExit("wheel missing entry_points.txt")
            if top_level_name is None:
                raise SystemExit("wheel missing top_level.txt")
            if license_name is None:
                raise SystemExit("wheel missing dist-info LICENSE")
            try:
                metadata = zf.read(metadata_name).decode("utf-8")
                entry_points = zf.read(entry_points_name).decode("utf-8")
                top_level = {
                    line.strip() for line in zf.read(top_level_name).decode("utf-8").splitlines() if line.strip()
                }
                license_text = zf.read(license_name).decode("utf-8")
            except UnicodeDecodeError:
                raise SystemExit("wheel metadata/license is not valid utf-8")
    except zipfile.BadZipFile:
        raise SystemExit(f"failed to read wheel archive {wheels[-1]}")
    missing = sorted(REQUIRED_WHEEL_FILES - names)
    if missing:
        raise SystemExit(f"wheel missing required files: {missing}")
    forbidden = sorted(FORBIDDEN_WHEEL_FILES & names)
    if forbidden:
        raise SystemExit(f"wheel contains source-only compatibility files: {forbidden}")
    forbidden_data = sorted(
        suffix for suffix in FORBIDDEN_WHEEL_DATA_SUFFIXES if any(name.endswith(suffix) for name in names)
    )
    if forbidden_data:
        raise SystemExit(f"wheel contains non-runtime data files: {forbidden_data}")
    missing_metadata = sorted(snippet for snippet in expected_metadata_snippets() if snippet not in metadata)
    if missing_metadata:
        raise SystemExit(f"wheel metadata missing required snippets: {missing_metadata}")
    missing_entry_points = sorted(snippet for snippet in expected_entry_point_snippets() if snippet not in entry_points)
    if missing_entry_points:
        raise SystemExit(f"wheel entry points missing required snippets: {missing_entry_points}")
    if top_level != EXPECTED_TOP_LEVEL_NAMES:
        raise SystemExit(f"wheel exposes unexpected top-level packages: {sorted(top_level)}")
    _check_license_text("wheel", license_text)


def main():
    dist_dir = Path("dist")
    check_version_consistency()
    check_gitignore_release_artifacts()
    check_agent_docs_current()
    check_sdist(dist_dir)
    check_wheel(dist_dir)
    print("package contents smoke ok")


if __name__ == "__main__":
    main()
