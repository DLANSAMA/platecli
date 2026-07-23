#!/usr/bin/env python3
"""Opt-in live-printer smoke for pre-release validation (Phase 0 safety round-trips).

**Never runs in CI or the default local suite.**

Gates:
  - ``BAMBU_LIVE=1`` (or true/yes/on) required; otherwise script exits / pytest skips.
  - Tests are marked ``@pytest.mark.live``; CI uses ``-m "not live"``.
  - Real ``config.json`` + ``BAMBU_LIVE_SOURCE`` required for printer checks.

Default path is read-mostly (preflight, doctor, gcode confirm refusal, upload-only,
download SIZE integrity). Motion / print start require extra explicit env flags.

Full docs: ``docs/live-printer-smoke.md``.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

import pytest

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

LIVE_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
PRINT_CONFIRM_TRUE_VALUES = LIVE_TRUE_VALUES
LIVE_CLEANUP_TRUE_VALUES = LIVE_TRUE_VALUES

# Collectable by pytest only when listed in python_files; always marked live.
pytestmark = pytest.mark.live


def live_enabled() -> bool:
    return os.environ.get("BAMBU_LIVE", "").strip().lower() in LIVE_TRUE_VALUES


def require_live_env() -> None:
    """Fail fast when the opt-in gate is not set (script entry)."""
    if not live_enabled():
        raise SystemExit(
            "Live-printer smoke is opt-in. Set BAMBU_LIVE=1 (and a real config + "
            "BAMBU_LIVE_SOURCE) to run. See docs/live-printer-smoke.md."
        )


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
    except FileNotFoundError:
        redacted_command = redact_sequence(command)
        assert False, (
            "Configured CLI executable was not found: "
            f"{redact_url_credentials(command[0])!r}. "
            "Set BAMBU_CLI to an installed plate executable or leave it unset "
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
    except json.JSONDecodeError:
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


def validate_gcode_requires_confirm():
    """Phase 0: raw gcode must not be sent without --confirm."""
    # Intentionally omit --confirm. Expect confirmation_required (JSON) or non-success
    # that does not claim sent=true.
    command = CLI + ["gcode", "M105", "--json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    # Must not claim the gcode was sent.
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            if payload.get("sent") is True:
                assert False, f"gcode without --confirm claimed sent=true: {payload}"
            status = payload.get("status")
            if status not in ("confirmation_required", "error", None) and payload.get("sent") is True:
                assert False, f"unexpected gcode JSON without confirm: {payload}"
            if status == "confirmation_required" or payload.get("sent") is False:
                print("gcode-requires-confirm live smoke ok")
                return
    # Non-zero exit without a "sent" claim is also acceptable refusal.
    if result.returncode != 0:
        print("gcode-requires-confirm live smoke ok (non-zero without confirm)")
        return
    # Zero exit with empty/unknown output is suspicious — re-check stdout.
    if result.stdout.strip():
        payload = json_stdout(result)
        assert payload.get("status") == "confirmation_required" or payload.get("sent") is False, payload
        print("gcode-requires-confirm live smoke ok")
        return
    assert False, "gcode without --confirm did not clearly refuse (empty success?)"


def validate_optional_gcode_confirm_send():
    """Extra opt-in: send harmless M105 with --confirm (temperature query only)."""
    confirm = os.environ.get("BAMBU_LIVE_GCODE_CONFIRM", "").strip().lower()
    if confirm not in LIVE_TRUE_VALUES:
        print("gcode-with-confirm skipped; set BAMBU_LIVE_GCODE_CONFIRM=1 to send M105")
        return
    print("WARNING: BAMBU_LIVE_GCODE_CONFIRM set — sending M105 (temperature query) to the printer.")
    result = run_cli(["gcode", "M105", "--confirm", "--json"], timeout=60)
    payload = json_stdout(result)
    if payload.get("command") != "gcode" or payload.get("sent") is not True:
        assert False, f"gcode M105 --confirm failed: {payload}"
    print("gcode-confirm-send live smoke ok")


def validate_upload_only(source):
    result = run_cli(["job", source, "--upload-only", "--json"], timeout=300)
    payload = json_stdout(result)
    if payload.get("command") != "job" or payload.get("status") != "uploaded" or payload.get("uploaded") is not True:
        assert False, f"upload-only job failed: {payload}"
    if payload.get("printed"):
        assert False, f"upload-only job unexpectedly printed: {payload}"
    remote_name = validate_reported_remote_name(payload.get("remote_name"))
    print("job-upload-only-json live smoke ok")
    return remote_name, payload


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


def validate_upload_download_integrity(remote_name: str, upload_payload: dict) -> None:
    """Round-trip download via library FTPS; SIZE mismatch would fail download_file."""
    reported_bytes = upload_payload.get("bytes")
    # Prefer library API so we exercise printer.download_file SIZE check.
    # Import only when live — keeps default collection free of printer config load.
    from bambu_cli.config import apply_config, load_config
    from bambu_cli.printer import get_printer

    apply_config(load_config())
    printer = get_printer()
    with tempfile.TemporaryDirectory(prefix="bambu-live-dl-") as tmp:
        local_path = os.path.join(tmp, portable_basename(remote_name) or "dl.bin")
        remote_path = f"/model/{remote_name}"
        ok = printer.download_file(remote_path, local_path)
        if not ok:
            assert False, f"download_file failed for {remote_path!r} (SIZE mismatch would surface here)"
        written = os.path.getsize(local_path)
        if written <= 0:
            assert False, f"downloaded empty file from {remote_path!r}"
        if isinstance(reported_bytes, int) and reported_bytes > 0 and written != reported_bytes:
            assert False, (
                f"download size {written} != upload-reported bytes {reported_bytes} "
                f"for {remote_name!r} (integrity / SIZE path)"
            )
    print("upload-download-integrity live smoke ok")


def validate_slice_produces_valid_3mf() -> None:
    """Local slice (Orca) produces a structurally valid .3mf when a mesh source is provided."""
    slice_src = os.environ.get("BAMBU_LIVE_SLICE_SOURCE", "").strip()
    job_src = os.environ.get("BAMBU_LIVE_SOURCE", "").strip()
    candidate = slice_src or job_src
    if not candidate or looks_like_url(candidate):
        print("slice-valid-3mf skipped; set BAMBU_LIVE_SLICE_SOURCE to a local mesh (.stl/.step/.obj)")
        return
    path = pathlib.Path(candidate).expanduser()
    if not path.is_file():
        print(f"slice-valid-3mf skipped; not a local file: {candidate!r}")
        return
    suffix = path.suffix.lower()
    if suffix not in SLICEABLE_EXTENSIONS:
        print(f"slice-valid-3mf skipped; {suffix!r} is not sliceable (use BAMBU_LIVE_SLICE_SOURCE)")
        return

    with tempfile.TemporaryDirectory(prefix="bambu-live-slice-") as tmp:
        result = run_cli(
            ["slice", str(path), "--output", tmp, "--json"],
            timeout=600,
        )
        payload = json_stdout(result)
        out = payload.get("path") or payload.get("output") or payload.get("outfile")
        if not out or not os.path.isfile(out):
            # Some slice JSON shapes nest the path; accept any .3mf written under tmp.
            produced = list(pathlib.Path(tmp).rglob("*.3mf"))
            if not produced:
                assert False, f"slice did not produce a .3mf: {payload}"
            out = str(produced[0])
        # Structural validation (same gate the CLI uses before treating slice as success).
        from bambu_cli.slicer.output import _is_valid_sliced_3mf

        if not _is_valid_sliced_3mf(out):
            assert False, f"slice output failed _is_valid_sliced_3mf: {out!r} payload={payload}"
    print("slice-valid-3mf live smoke ok")


def validate_print(remote_name):
    confirm = os.environ.get("BAMBU_LIVE_PRINT_CONFIRM", "").strip().lower()
    if confirm not in PRINT_CONFIRM_TRUE_VALUES:
        print("print start skipped; set BAMBU_LIVE_PRINT_CONFIRM=1,true,yes,on to verify print ACK")
        return False
    print("WARNING: BAMBU_LIVE_PRINT_CONFIRM set — this will START A PRINT on the real printer.")
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


def run_live_suite():
    """Full pre-release live path (also used by pytest and ``__main__``)."""
    require_live_env()
    source = require_source()
    validate_preflight()
    validate_doctor()
    validate_gcode_requires_confirm()
    validate_optional_gcode_confirm_send()
    validate_slice_produces_valid_3mf()
    before_names = listed_remote_names("before upload")
    predicted_name = validate_remote_name_not_preexisting(source, before_names)
    remote_name, upload_payload = validate_upload_only(source)
    if predicted_name and remote_name != predicted_name:
        assert False, f"upload reported remote_name {remote_name!r}, expected {predicted_name!r}"
    validate_uploaded_file_was_new(remote_name, before_names)
    validate_uploaded_file_visible(remote_name)
    validate_upload_download_integrity(remote_name, upload_payload)
    printed = validate_print(remote_name)
    cleanup_uploaded_file(remote_name, printed)


# ---------------------------------------------------------------------------
# Pytest entry (excluded by default via ``-m "not live"``)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _live_gate():
    if not live_enabled():
        pytest.skip("Set BAMBU_LIVE=1 and a real printer config to run live smoke (docs/live-printer-smoke.md)")
    if not os.environ.get("BAMBU_LIVE_SOURCE"):
        pytest.skip("Set BAMBU_LIVE_SOURCE for live printer smoke")


def test_live_printer_pre_release_suite(_live_gate):
    """Single opt-in suite: connectivity, gcode confirm, upload, SIZE integrity, optional slice."""
    run_live_suite()


# CLI is resolved at import for the script path; keep after helpers.
CLI = default_cli()


def main():
    run_live_suite()


if __name__ == "__main__":
    main()
