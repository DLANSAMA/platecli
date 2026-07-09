#!/usr/bin/env python3
"""Opt-in live-printer smoke test for release validation.

This script intentionally does not run in normal CI. By default it runs this
checkout's `scripts/bambu.py` so release proof covers the code being reviewed.
Set BAMBU_CLI explicitly when validating an installed command. It requires the
user's real config to already be set up. Printing is disabled unless
BAMBU_LIVE_PRINT_CONFIRM is an explicit truthy value such as 1, true, or yes.
"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from urllib.parse import urlparse


PRINT_READY_EXTENSIONS = {".3mf", ".gcode"}
SLICEABLE_EXTENSIONS = {".stl", ".step", ".stp", ".obj"}
ARCHIVE_EXTENSIONS = {".zip"}
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_REMOTE_NAME_LENGTH = 160


def split_configured_cli(configured, platform=None):
    """Split BAMBU_CLI without corrupting plain Windows executable paths."""
    active_platform = sys.platform if platform is None else platform
    value = configured.strip()
    if active_platform == "win32":
        unquoted = value.strip('"')
        if (
            value == unquoted
            and re.match(r"^[A-Za-z]:\\", unquoted)
            and unquoted.lower().endswith((".exe", ".cmd", ".bat", ".py"))
        ):
            return [unquoted]
    return shlex.split(value)


def default_cli():
    configured = os.environ.get("BAMBU_CLI")
    if configured and configured.strip():
        cli = split_configured_cli(configured)
        reject_simulated_cli(cli)
        return cli
    # Release proof should exercise this checkout by default. Use BAMBU_CLI
    # explicitly when validating an installed command.
    return [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "bambu.py")]


def reject_simulated_cli(cli):
    if "--sim" in cli:
        raise SystemExit("Live-printer smoke refuses BAMBU_CLI commands that include --sim.")


def redact_url_credentials(value):
    """Remove URL userinfo before echoing commands or subprocess output."""
    text = str(value or "")
    text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1", text)
    return re.sub(
        r"(?<![\w./:-])[^@\s/:]+:[^@\s/]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:[/?#][^\s]*)?)",
        r"\1",
        text,
    )


def redact_sequence(values):
    return [redact_url_credentials(value) for value in values]


CLI = default_cli()
PRINT_CONFIRM_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
LIVE_CLEANUP_TRUE_VALUES = PRINT_CONFIRM_TRUE_VALUES


def run_cli(args, expected_returncode=0, timeout=180):
    command = CLI + list(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        redacted_command = redact_sequence(command)
        assert False, f"{subprocess.list2cmdline(redacted_command)} timed out after {exc.timeout} seconds"
    except FileNotFoundError as exc:
        redacted_command = redact_sequence(command)
        assert False, (
            "Configured CLI executable was not found: "
            f"{redact_url_credentials(command[0])!r}. "
            "Set BAMBU_CLI to an installed bambu-cli executable or leave it unset "
            f"to exercise this checkout. Command: {subprocess.list2cmdline(redacted_command)}"
        )
    if result.returncode != expected_returncode:
        sys.stderr.write(redact_url_credentials(result.stderr))
        sys.stderr.write(redact_url_credentials(result.stdout))
        redacted_command = redact_sequence(command)
        assert False, (
            f"{subprocess.list2cmdline(redacted_command)} exited {result.returncode}, expected {expected_returncode}"
        )
    return result


def json_stdout(result):
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(redact_url_credentials(result.stderr))
        assert False, f"stdout was not a single JSON document: {redact_url_credentials(result.stdout)!r}"
    if not isinstance(payload, dict):
        assert False, f"expected JSON object on stdout, got {type(payload).__name__}"
    return payload


def require_source():
    source = os.environ.get("BAMBU_LIVE_SOURCE")
    if not source:
        raise SystemExit(
            "Set BAMBU_LIVE_SOURCE to a printer-ready .3mf/.gcode file or supported model URL/path before running."
        )
    return source


def portable_basename(path):
    return pathlib.PurePosixPath(str(path or "").replace("\\", "/")).name


def looks_like_url(source):
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        return True
    return bool(re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]|$)", str(source or "")))


def is_safe_remote_name(remote_name):
    if not isinstance(remote_name, str) or not remote_name.strip():
        return False
    if remote_name != portable_basename(remote_name):
        return False
    if remote_name in (".", "..") or any(ord(char) < 32 for char in remote_name):
        return False
    if any(char in remote_name for char in '<>:"/\\|?*'):
        return False
    if remote_name != remote_name.strip(" ."):
        return False
    if len(remote_name) > MAX_REMOTE_NAME_LENGTH:
        return False
    stem = pathlib.PurePosixPath(remote_name).stem
    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        return False
    return True


def validate_reported_remote_name(remote_name):
    if not is_safe_remote_name(remote_name):
        assert False, f"upload-only job reported unsafe remote_name: {remote_name!r}"
    return remote_name


def predicted_remote_name(source):
    """Return the remote name before upload when it is knowable without side effects."""
    expected_value = os.environ.get("BAMBU_LIVE_EXPECT_REMOTE_NAME", "")
    expected = expected_value.strip()
    if expected_value:
        if expected != expected_value:
            assert False, f"BAMBU_LIVE_EXPECT_REMOTE_NAME is not printer-safe portable: {expected_value!r}"
        if not is_safe_remote_name(expected):
            assert False, f"BAMBU_LIVE_EXPECT_REMOTE_NAME is not printer-safe portable: {expected!r}"
        return expected
    if looks_like_url(source):
        parsed = urlparse(source if urlparse(source).scheme else "https://" + source)
        name = portable_basename(parsed.path)
        suffix = pathlib.PurePosixPath(name).suffix.lower()
        if suffix in PRINT_READY_EXTENSIONS and is_safe_remote_name(name):
            return name
        if suffix in SLICEABLE_EXTENSIONS:
            remote_name = f"{pathlib.PurePosixPath(name).stem}_sliced.3mf"
            return remote_name if is_safe_remote_name(remote_name) else None
        return None
    path = pathlib.Path(source).expanduser()
    if not path.exists():
        return None
    name = portable_basename(source)
    suffix = pathlib.PurePosixPath(name).suffix.lower()
    if suffix in PRINT_READY_EXTENSIONS:
        return name
    if suffix in SLICEABLE_EXTENSIONS:
        return f"{pathlib.PurePosixPath(name).stem}_sliced.3mf"
    if suffix in ARCHIVE_EXTENSIONS:
        if path.exists():
            import zipfile

            try:
                with zipfile.ZipFile(path) as archive:
                    member_filename = None
                    for name in archive.namelist():
                        ext = pathlib.PurePosixPath(name).suffix.lower()
                        if ext in PRINT_READY_EXTENSIONS or ext in SLICEABLE_EXTENSIONS:
                            member_filename = portable_basename(name)
                            break
                    if member_filename:
                        member_ext = pathlib.PurePosixPath(member_filename).suffix.lower()
                        if member_ext in PRINT_READY_EXTENSIONS:
                            return member_filename
                        if member_ext in SLICEABLE_EXTENSIONS:
                            remote_name = f"{pathlib.PurePosixPath(member_filename).stem}_sliced.3mf"
                            return remote_name if is_safe_remote_name(remote_name) else None
            except Exception:
                pass
        return None
    return None


def validate_remote_name_not_preexisting(source, before_names):
    remote_name = predicted_remote_name(source)
    if remote_name and not is_safe_remote_name(remote_name):
        assert False, (
            f"predicted remote file {remote_name!r} is not printer-safe portable; "
            "use a source filename that works on Linux/macOS/Windows printer workflows "
            "or set BAMBU_LIVE_EXPECT_REMOTE_NAME to the exact safe uploaded name"
        )
    if remote_name and remote_name in before_names:
        assert False, (
            f"expected remote file {remote_name!r} is already present before upload; "
            "use a uniquely named live-smoke source, set BAMBU_LIVE_EXPECT_REMOTE_NAME for URL/ZIP sources, "
            "or delete the old file first"
        )
    return remote_name


def validate_preflight():
    result = run_cli(["preflight", "--strict", "--json"])
    payload = json_stdout(result)
    if payload.get("command") != "preflight" or payload.get("status") != "ok":
        assert False, f"preflight failed: {payload}"
    print("preflight-strict-json live smoke ok")


def validate_doctor():
    result = run_cli(["doctor", "--json"], timeout=60)
    payload = json_stdout(result)
    if payload.get("command") != "doctor" or payload.get("status") != "ok":
        assert False, f"doctor failed: {payload}"
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        assert False, f"doctor JSON did not include capabilities: {payload}"
    serial = capabilities.get("serial")
    if serial not in ("<redacted>", "UNKNOWN", None):
        assert False, "doctor JSON exposed an unredacted printer serial"
    print("doctor-json live smoke ok")


def validate_upload_only(source):
    result = run_cli(["job", source, "--upload-only", "--json"], timeout=300)
    payload = json_stdout(result)
    if payload.get("command") != "job" or payload.get("status") != "uploaded" or payload.get("uploaded") is not True:
        assert False, f"upload-only job failed: {payload}"
    if payload.get("printed"):
        assert False, f"upload-only job unexpectedly printed: {payload}"
    remote_name = validate_reported_remote_name(payload.get("remote_name"))
    print("job-upload-only-json live smoke ok")
    return remote_name


def listed_remote_names(context):
    result = run_cli(["files", "--json"], timeout=60)
    payload = json_stdout(result)
    if payload.get("command") != "files" or payload.get("status") != "ok":
        assert False, f"files listing failed {context}: {payload}"
    files = payload.get("files")
    if not isinstance(files, list):
        assert False, f"files JSON did not include a file list: {payload}"
    return {item.get("name") for item in files if isinstance(item, dict)}


def validate_uploaded_file_visible(remote_name):
    names = listed_remote_names("after upload")
    if remote_name not in names:
        assert False, f"uploaded file {remote_name!r} was not visible in files --json"
    print("files-json-upload-visible live smoke ok")


def validate_uploaded_file_was_new(remote_name, before_names):
    if remote_name in before_names:
        assert False, (
            f"uploaded file {remote_name!r} was already present before upload; "
            "use a uniquely named live-smoke source or delete the old file first"
        )
    print("files-json-upload-new live smoke ok")


def validate_uploaded_file_absent(remote_name):
    names = listed_remote_names("after cleanup")
    if remote_name in names:
        assert False, f"cleanup delete reported success but {remote_name!r} is still visible in files --json"
    print("files-json-cleanup-absent live smoke ok")


def validate_print(remote_name):
    confirm = os.environ.get("BAMBU_LIVE_PRINT_CONFIRM", "").strip().lower()
    if confirm not in PRINT_CONFIRM_TRUE_VALUES:
        print("print start skipped; set BAMBU_LIVE_PRINT_CONFIRM=1,true,yes,on to verify print ACK")
        return False
    result = run_cli(["print", remote_name, "--confirm", "--json"], timeout=120)
    payload = json_stdout(result)
    if (
        payload.get("command") != "print"
        or payload.get("status") != "print_started"
        or payload.get("printed") is not True
    ):
        assert False, f"print ACK validation failed: {payload}"
    print("print-ack-json live smoke ok")
    return True


def cleanup_uploaded_file(remote_name, printed):
    cleanup = os.environ.get("BAMBU_LIVE_CLEANUP", "").strip().lower()
    if cleanup not in LIVE_CLEANUP_TRUE_VALUES:
        print("cleanup skipped; set BAMBU_LIVE_CLEANUP=1,true,yes,on to delete the uploaded test file")
        return
    if printed:
        print("cleanup skipped because the uploaded file was started as a print")
        return
    result = run_cli(["delete", remote_name, "--confirm", "--json"], timeout=60)
    payload = json_stdout(result)
    if payload.get("command") != "delete" or payload.get("status") != "deleted" or payload.get("deleted") is not True:
        assert False, f"cleanup delete failed: {payload}"
    print("cleanup-delete-json live smoke ok")
    validate_uploaded_file_absent(remote_name)


def main():
    source = require_source()
    validate_preflight()
    validate_doctor()
    before_names = listed_remote_names("before upload")
    predicted_name = validate_remote_name_not_preexisting(source, before_names)
    remote_name = validate_upload_only(source)
    if predicted_name and remote_name != predicted_name:
        assert False, f"upload reported remote_name {remote_name!r}, expected {predicted_name!r}"
    validate_uploaded_file_was_new(remote_name, before_names)
    validate_uploaded_file_visible(remote_name)
    printed = validate_print(remote_name)
    cleanup_uploaded_file(remote_name, printed)


if __name__ == "__main__":
    main()
