#!/usr/bin/env python3
"""CLI smoke tests for agent-facing workflows.

This script is intentionally subprocess-based. By default it exercises this
checkout's script entry point so source validation cannot accidentally use a
stale installed `bambu-cli` from PATH. Set BAMBU_CLI explicitly when validating
an installed command.
"""
import json
import os
# Keep job/send temp workdirs so tests can inspect extracted/sliced outputs.
os.environ["BAMBU_KEEP_WORKDIR"] = "1"
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile


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
        return split_configured_cli(configured)

    return [sys.executable, "-m", "bambu_cli.bambu"]




CLI = default_cli()
ROOT = pathlib.Path(__file__).resolve().parents[1]

HELP_COMMANDS = [
    "download",
    "upload",
    "print",
    "files",
    "light",
    "pause",
    "resume",
    "stop",
    "delete",
    "gcode",
    "snapshot",
    "preflight",
    "doctor",
    "job",
    "send",
    "setup",
    "slice",
    "status",
]


def project_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        assert False, ("pyproject.toml is missing project version")
    return match.group(1)


def isolated_env(root):
    home = root / "home"
    xdg = root / "xdg"
    appdata = root / "appdata"
    localappdata = root / "localappdata"
    for path in (home, xdg, appdata, localappdata):
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(localappdata),
        # Force UTF-8 stdio in the child so emoji output is emitted as UTF-8 even
        # on Windows consoles that default to cp1252 (matches the parent decode).
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def platform_config_path(root):
    if sys.platform == "win32":
        return root / "appdata" / "bambu" / "config.json"
    if sys.platform == "darwin":
        return root / "home" / "Library" / "Application Support" / "bambu" / "config.json"
    return root / "xdg" / "bambu" / "config.json"


def redact_url_credentials(value):
    """Remove URL userinfo before echoing subprocess failures."""
    text = str(value or "")
    text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1", text)
    return re.sub(
        r"(?<![\w./:-])[^@\s/:]+:[^@\s/]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:[/?#][^\s]*)?)",
        r"\1",
        text,
    )


def redact_sequence(values):
    return [redact_url_credentials(value) for value in values]


def run_cli(args, env, expected_returncode=0, input_text=None):
    command = CLI + list(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            # Decode child output as UTF-8 rather than the platform default
            # (cp1252 on Windows), which cannot decode the CLI's emoji output and
            # would kill the reader thread, leaving result.stdout/stderr as None.
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            env=env,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        redacted_command = redact_sequence(command)
        assert False, (
            f"{subprocess.list2cmdline(redacted_command)} timed out after {exc.timeout} seconds"
        )
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
        assert False, (
            f"stdout was not a single JSON document: {redact_url_credentials(result.stdout)!r}"
        )
    if not isinstance(payload, dict):
        assert False, (f"expected JSON object on stdout, got {type(payload).__name__}")
    return payload


def smoke_help_surface(root):
    env = isolated_env(root)
    top = run_cli(["--help"], env)
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    top_stdout = ansi_escape.sub("", top.stdout)
    if "job" not in top_stdout or "send" not in top_stdout:
        assert False, ("top-level help does not expose job/send")
    normalized_help = " ".join(top_stdout.split())
    if "--json" not in top_stdout or "may appear before the subcommand" not in normalized_help:
        assert False, ("top-level help does not expose the agent-friendly global --json flag")
    version = run_cli(["--version"], env)
    expected_version = f"bambu-cli {project_version()}"
    if version.stdout.strip() != expected_version:
        assert False, (f"--version output {version.stdout!r} did not match {expected_version!r}")
    version_json = json_stdout(run_cli(["--json", "--version"], env))
    if version_json != {"status": "ok", "command": "version", "version": project_version()}:
        assert False, (f"--json --version payload was unexpected: {version_json}")
    no_command = run_cli([], env, expected_returncode=5)
    if "usage:" not in no_command.stderr.lower() or no_command.stdout.strip():
        assert False, ("missing subcommand should print usage to stderr and keep stdout empty")
    unknown_command = run_cli(["definitely-not-a-command"], env, expected_returncode=5)
    if "invalid choice" not in unknown_command.stderr or unknown_command.stdout.strip():
        assert False, ("unknown subcommand should be a command error on stderr with empty stdout")

    for command in HELP_COMMANDS:
        result = run_cli([command, "--help"], env)
        stdout_clean = ansi_escape.sub("", result.stdout)
        normalized = " ".join(stdout_clean.split())
        if "usage:" not in normalized.lower():
            assert False, (f"{command} --help did not print usage text")
        if command in ("job", "send") and f" {command} " not in normalized:
            assert False, (f"{command} --help usage did not identify the invoked command")
        if command == "status" and ("raw printer data" not in normalized or "'printer'" not in normalized):
            assert False, ("status --help does not describe the wrapped JSON payload")
        if command in ("job", "send") and ".zip" not in normalized:
            assert False, (f"{command} --help does not advertise local ZIP sources")
        if command in ("download", "job", "send") and "--max-download-mb" not in normalized:
            assert False, (f"{command} --help does not advertise the URL download size guard")
        if command in ("download", "job", "send") and "ZIP extraction" not in normalized:
            assert False, (f"{command} --help does not advertise the ZIP extraction size guard")
        if command in ("job", "send", "print") and "zero-or-positive indexes" not in normalized:
            assert False, (f"{command} --help does not describe AMS mapping index validation")
        if command == "download" and ("simple HTML page" not in normalized or "ZIP URL" not in normalized):
            assert False, ("download --help does not advertise HTML/ZIP URL support")
    print("help-surface smoke ok")


def smoke_setup_json(root):
    env = isolated_env(root)
    env["BAMBU_ACCESS_CODE"] = "agent-smoke-secret"
    access_code_file = root / "secrets" / "access_code"
    serial = "AGENTSMOKESERIAL"

    result = run_cli([
        "setup",
        "--printer-ip", "printer.local",
        "--serial", serial,
        "--access-code-env", "BAMBU_ACCESS_CODE",
        "--access-code-file", str(access_code_file),
        "--model", "P1P",
        "--nozzle", "0.4",
        "--json",
    ], env)
    payload = json_stdout(result)

    if payload.get("status") != "configured" or payload.get("command") != "setup":
        assert False, (f"unexpected setup payload: {payload}")
    combined_output = result.stdout + result.stderr
    if serial in combined_output or env["BAMBU_ACCESS_CODE"] in combined_output:
        assert False, ("setup --json leaked the serial or access code")
    if not platform_config_path(root).exists():
        assert False, ("setup did not write the platform config path")
    if not access_code_file.exists():
        assert False, ("setup did not write the access_code_file")
    print("setup-json smoke ok")


def smoke_setup_json_noninteractive_missing_values(root):
    env = isolated_env(root)

    for args in (["setup", "--json"], ["--json", "setup"]):
        result = run_cli(args, env, expected_returncode=1, input_text="")
        payload = json_stdout(result)
        if payload.get("status") != "error" or payload.get("command") != "setup":
            assert False, (f"unexpected setup missing-values payload: {payload}")
        if payload.get("failed_step") != "validate" or payload.get("exit_code") != 1:
            assert False, (f"setup missing-values payload lacks validation metadata: {payload}")
        missing = payload.get("missing", [])
        for expected in ("--printer-ip", "--serial", "--access-code, --access-code-env, or --access-code-file"):
            if expected not in missing:
                assert False, (f"setup missing-values payload omitted {expected}: {payload}")
        if "Traceback" in result.stderr or "EOF" in result.stderr:
            assert False, (f"{args} leaked an interactive failure: {result.stderr!r}")
    print("setup-json-missing-values smoke ok")


def smoke_setup_json_rejects_bad_access_code_file(root):
    env = isolated_env(root)
    env["BAMBU_ACCESS_CODE"] = "agent-smoke-secret"

    result = run_cli([
        "setup",
        "--printer-ip", "printer.local",
        "--serial", "AGENTSMOKESERIAL",
        "--access-code-env", "BAMBU_ACCESS_CODE",
        "--access-code-file", str(root),
        "--json",
    ], env, expected_returncode=1)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("command") != "setup":
        assert False, (f"bad access-code-file payload is not self-describing: {payload}")
    if payload.get("failed_step") != "validate" or "directory, not a file" not in payload.get("error", ""):
        assert False, (f"bad access-code-file payload did not explain validation failure: {payload}")
    combined_output = result.stdout + result.stderr
    if env["BAMBU_ACCESS_CODE"] in combined_output:
        assert False, ("bad access-code-file setup error leaked the access code")
    print("setup-json-bad-access-code-file smoke ok")


def smoke_invalid_config_json(root):
    env = isolated_env(root)
    config = platform_config_path(root)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "printer_ip": "invalid_host_for_agent_json_smoke.invalid",
        "serial": "BADCONFIGSERIAL",
        "access_code": "BADCONFIGCODE",
        "model": "P1P",
        "nozzle": "0.4",
    }), encoding="utf-8")

    result = run_cli(["status", "--json"], env, expected_returncode=1)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("failed_step") != "config":
        assert False, (f"unexpected config-error payload: {payload}")
    combined_output = result.stdout + result.stderr
    if "BADCONFIGSERIAL" in combined_output or "BADCONFIGCODE" in combined_output:
        assert False, ("config error JSON leaked serial or access code")
    print("config-error-json smoke ok")


def smoke_parse_error_json(root):
    env = isolated_env(root)
    result = run_cli(["job", "--json"], env, expected_returncode=5)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("failed_step") != "parse":
        assert False, (f"unexpected parse-error payload: {payload}")
    if payload.get("command") != "job" or payload.get("exit_code") != 5:
        assert False, (f"parse-error payload is missing command/exit code: {payload}")
    if result.stderr.strip():
        assert False, (f"parse-error --json wrote human usage to stderr: {result.stderr!r}")
    print("parse-error-json smoke ok")


def smoke_preflight_json(root):
    env = isolated_env(root)
    result = run_cli(["preflight", "--json"], env, expected_returncode=1)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("command") != "preflight":
        assert False, (f"unexpected preflight payload: {payload}")
    if payload.get("exit_code") != 1 or payload.get("ok") is not False:
        assert False, (f"preflight payload is missing exit metadata: {payload}")
    check_names = {check.get("name") for check in payload.get("checks", [])}
    if "config" not in check_names:
        assert False, (f"preflight payload did not include config check: {payload}")
    print("preflight-json smoke ok")


def smoke_local_job_dry_run_json(root):
    env = isolated_env(root)
    ready = root / "ready.3mf"
    ready.write_text("simulated 3mf content", encoding="utf-8")

    result = run_cli(["job", str(ready), "--confirm", "--dry-run", "--json"], env)
    payload = json_stdout(result)
    if payload.get("status") != "dry_run_local_skipped":
        assert False, (f"unexpected dry-run payload: {payload}")
    if payload.get("command") != "job":
        assert False, (f"local dry-run payload did not include command: {payload}")
    if payload.get("would_upload") is not True or payload.get("would_print") is not True:
        assert False, (f"dry-run payload did not report planned upload/print: {payload}")
    if payload.get("uploaded") or payload.get("printed"):
        assert False, (f"dry-run payload reported side effects: {payload}")

    # Use an over-length name rather than an embedded control character: names
    # with '\n'/'\r'/'\0' or reserved characters are rejected by the OS itself
    # when creating the fixture file on Windows, so they can't be exercised
    # portably here. An over-long name triggers the same "unsafe name"
    # rejection path in the CLI while remaining creatable on every platform.
    unsafe = root / ("bad_name_" + "x" * 200 + ".3mf")
    unsafe.write_text("simulated 3mf content", encoding="utf-8")
    rejected = run_cli(["job", str(unsafe), "--dry-run", "--json"], env, expected_returncode=3)
    rejected_payload = json_stdout(rejected)
    if rejected_payload.get("status") != "error" or rejected_payload.get("failed_step") != "validate":
        assert False, (f"unsafe printer filename was not rejected during dry-run: {rejected_payload}")
    if "unsafe name" not in rejected_payload.get("error", ""):
        assert False, (f"unsafe printer filename rejection was not clear: {rejected_payload}")
    print("local-job-dry-run-json smoke ok")


def smoke_url_job_dry_run_json(root):
    env = isolated_env(root)

    result = run_cli(["job", "printables.com/model/12345-agent-smoke", "--confirm", "--dry-run", "--json"], env)
    payload = json_stdout(result)
    if payload.get("status") != "dry_run_url_skipped":
        assert False, (f"unexpected URL dry-run payload: {payload}")
    if payload.get("command") != "job":
        assert False, (f"URL dry-run payload did not include command: {payload}")
    if payload.get("normalized_source") != "https://printables.com/model/12345-agent-smoke":
        assert False, (f"URL dry-run did not normalize scheme-less input: {payload}")
    if payload.get("would_download") is not True or payload.get("would_upload") is not True:
        assert False, (f"URL dry-run payload did not report planned download/upload: {payload}")
    if payload.get("would_print") is not True:
        assert False, (f"URL dry-run payload did not report planned print: {payload}")
    if payload.get("downloaded_path") or payload.get("uploaded") or payload.get("printed"):
        assert False, (f"URL dry-run payload reported side effects: {payload}")
    print("url-job-dry-run-json smoke ok")


def smoke_url_job_dry_run_plans_direct_sources(root):
    env = isolated_env(root)

    model = run_cli(["job", "https://example.com/model.stl", "--dry-run", "--json"], env)
    model_payload = json_stdout(model)
    if model_payload.get("status") != "dry_run_url_skipped":
        assert False, (f"unexpected direct model URL dry-run payload: {model_payload}")
    if model_payload.get("would_download") is not True:
        assert False, (f"direct model URL dry-run did not report planned download: {model_payload}")
    if model_payload.get("would_slice") is not True or model_payload.get("would_extract") is not False:
        assert False, (f"direct model URL dry-run did not report planned slice/extract steps: {model_payload}")
    if model_payload.get("remote_name") != "model_sliced.3mf":
        assert False, (f"direct model URL dry-run did not predict remote name: {model_payload}")
    if model_payload.get("downloaded_path") or model_payload.get("uploaded") or model_payload.get("printed"):
        assert False, (f"direct model URL dry-run reported side effects: {model_payload}")

    named = run_cli(["job", "https://example.com/download?id=1", "--name", "part.step", "--copies", "2", "--dry-run", "--json"], env)
    named_payload = json_stdout(named)
    if named_payload.get("remote_name") != "part_x2_sliced.3mf":
        assert False, (f"named direct URL dry-run did not predict sliced remote name: {named_payload}")

    archive = run_cli(["job", "https://example.com/bundle.zip", "--dry-run", "--json"], env)
    archive_payload = json_stdout(archive)
    if archive_payload.get("status") != "dry_run_url_skipped":
        assert False, (f"unexpected direct ZIP URL dry-run payload: {archive_payload}")
    if archive_payload.get("would_download") is not True:
        assert False, (f"direct ZIP URL dry-run did not report planned download: {archive_payload}")
    if archive_payload.get("would_extract") is not True or archive_payload.get("would_slice") is not False:
        assert False, (f"direct ZIP URL dry-run did not report planned extract/slice steps: {archive_payload}")
    if archive_payload.get("remote_name") is not None:
        assert False, (f"direct ZIP URL dry-run should not guess archive remote name: {archive_payload}")
    if archive_payload.get("downloaded_path") or archive_payload.get("uploaded") or archive_payload.get("printed"):
        assert False, (f"direct ZIP URL dry-run reported side effects: {archive_payload}")
    print("url-job-dry-run-direct-plan smoke ok")


def smoke_global_json_flag_json(root):
    env = isolated_env(root)

    result = run_cli(["--json", "--sim", "send", "example.com/model.stl", "--dry-run"], env)
    payload = json_stdout(result)
    if payload.get("status") != "dry_run_url_skipped" or payload.get("command") != "send":
        assert False, (f"global --json send dry-run payload was unexpected: {payload}")
    if payload.get("normalized_source") != "https://example.com/model.stl":
        assert False, (f"global --json send dry-run did not normalize source: {payload}")

    missing_slice = run_cli(["--json", "slice", str(root / "missing.stl")], env, expected_returncode=3)
    slice_payload = json_stdout(missing_slice)
    if slice_payload.get("status") != "error" or slice_payload.get("command") != "slice":
        assert False, (f"global --json slice error was not self-describing: {slice_payload}")
    if slice_payload.get("failed_step") != "validate":
        assert False, (f"global --json slice error did not identify validation: {slice_payload}")
    print("global-json-flag smoke ok")


def smoke_download_rejects_non_model_json(root):
    env = isolated_env(root)

    result = run_cli(["download", "https://example.com/archive.rar", "--json"], env, expected_returncode=3)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("command") != "download":
        assert False, (f"unexpected download rejection payload: {payload}")
    if payload.get("failed_step") != "validate" or payload.get("exit_code") != 3:
        assert False, (f"download rejection payload is missing failure metadata: {payload}")
    if payload.get("extension") != ".rar":
        assert False, (f"download rejection payload did not include rejected extension: {payload}")

    credentialed_url = "https://agent:secret" + "@example.com/model.stl"
    credential_result = run_cli(["download", credentialed_url, "--json"], env, expected_returncode=5)
    credential_output = credential_result.stdout + credential_result.stderr
    if "secret" in credential_output:
        assert False, ("credential-bearing URL rejection leaked the password")
    credential_payload = json_stdout(credential_result)
    if credential_payload.get("source") != "https://example.com/model.stl":
        assert False, (f"credential-bearing URL was not redacted: {credential_payload}")
    if credential_payload.get("failed_step") != "validate":
        assert False, (f"credential-bearing URL rejection lacked validation metadata: {credential_payload}")

    schemeless_credential_url = "agent:secret" + "@example.com/model.stl"
    schemeless_result = run_cli(["download", schemeless_credential_url, "--json"], env, expected_returncode=5)
    schemeless_output = schemeless_result.stdout + schemeless_result.stderr
    if "secret" in schemeless_output:
        assert False, ("scheme-less credential-bearing URL rejection leaked the password")
    schemeless_payload = json_stdout(schemeless_result)
    if schemeless_payload.get("source") != "example.com/model.stl":
        assert False, (f"scheme-less credential-bearing URL was not redacted: {schemeless_payload}")
    if schemeless_payload.get("normalized_source") != "https://example.com/model.stl":
        assert False, (f"scheme-less credential-bearing URL normalized source was not redacted: {schemeless_payload}")
    print("download-reject-json smoke ok")


def smoke_url_job_dry_run_rejects_non_model_before_output(root):
    env = isolated_env(root)
    output_dir = root / "unused-output"

    result = run_cli([
        "job",
        "https://example.com/archive.rar",
        "--dry-run",
        "--output",
        str(output_dir),
        "--json",
    ], env, expected_returncode=3)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("command") != "job":
        assert False, (f"unexpected job rejection payload: {payload}")
    if payload.get("failed_step") != "validate" or payload.get("extension") != ".rar":
        assert False, (f"job rejection did not identify the unsupported source: {payload}")
    if payload.get("workdir") is not None or "would_create_output_dir" in payload:
        assert False, (f"job rejection planned an irrelevant output directory: {payload}")
    if output_dir.exists():
        assert False, ("URL dry-run rejection created an output directory")
    print("url-job-dry-run-reject-json smoke ok")


def smoke_sim_local_zip_job_json(root):
    env = isolated_env(root)
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "agent-bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../agent-ready.3mf", "simulated 3mf content")
        archive.writestr(("a" * 300) + ".txt", "ignored")

    result = run_cli(["--sim", "job", str(archive_path), "--confirm", "--json"], env)
    payload = json_stdout(result)
    extracted_path = pathlib.Path(payload.get("extracted_path", ""))
    printable_path = pathlib.Path(payload.get("printable_path", ""))
    if payload.get("status") != "printed" or payload.get("command") != "job":
        assert False, (f"unexpected local ZIP job payload: {payload}")
    if extracted_path != printable_path or extracted_path.name != "agent-ready.3mf":
        assert False, (f"local ZIP job did not report extracted printable: {payload}")
    if pathlib.Path(payload.get("workdir", "")) != extracted_path.parent or extracted_path.parent == root:
        assert False, (f"local ZIP job did not report private workdir: {payload}")
    if payload.get("archive_entry") != "agent-ready.3mf":
        assert False, (f"local ZIP job did not report archive entry: {payload}")
    if payload.get("uploaded") is not True or payload.get("printed") is not True:
        assert False, (f"local ZIP job did not complete simulated upload/print: {payload}")
    try:
        if not extracted_path.exists() or extracted_path.read_text(encoding="utf-8") != "simulated 3mf content":
            assert False, ("local ZIP job did not extract the expected printer-ready file")
    finally:
        if extracted_path.parent.name.startswith("bambu-job-"):
            shutil.rmtree(extracted_path.parent, ignore_errors=True)
    print("sim-local-zip-job-json smoke ok")


def smoke_sim_local_zip_long_name_json(root):
    env = isolated_env(root)
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "agent-long-name-bundle.zip"
    member_name = ("a" * 300) + ".3mf"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member_name, "simulated 3mf content")

    result = run_cli(["--sim", "job", str(archive_path), "--upload-only", "--json"], env)
    payload = json_stdout(result)
    remote_name = payload.get("remote_name", "")
    if payload.get("status") != "uploaded" or payload.get("command") != "job":
        assert False, (f"unexpected long ZIP job payload: {payload}")
    if len(remote_name) > 160 or not remote_name.endswith(".3mf"):
        assert False, (f"long ZIP member was not truncated safely: {payload}")
    extracted_path = pathlib.Path(payload.get("extracted_path", ""))
    try:
        if not extracted_path.exists() or extracted_path.name != remote_name:
            assert False, (f"long ZIP job did not report extracted truncated file: {payload}")
    finally:
        if extracted_path.parent.name.startswith("bambu-job-"):
            shutil.rmtree(extracted_path.parent, ignore_errors=True)
    print("sim-local-zip-long-name-json smoke ok")


def smoke_local_zip_extract_error_json(root):
    env = isolated_env(root)
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "agent-empty-bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "not a model")

    result = run_cli(["job", str(archive_path), "--dry-run", "--json"], env, expected_returncode=3)
    payload = json_stdout(result)
    if payload.get("status") != "error" or payload.get("command") != "job":
        assert False, (f"unexpected local ZIP extract-error payload: {payload}")
    if payload.get("failed_step") != "extract" or payload.get("exit_code") != 3:
        assert False, (f"local ZIP extract-error payload lacks extract metadata: {payload}")
    if "supported model" not in payload.get("error", ""):
        assert False, (f"local ZIP extract-error payload is not actionable: {payload}")
    if payload.get("would_extract") is not True or payload.get("uploaded") is not False:
        assert False, (f"local ZIP extract-error payload lost job plan context: {payload}")
    print("local-zip-extract-error-json smoke ok")


def smoke_sim_job_json(root):
    env = isolated_env(root)
    ready = root / "ready file.3mf"
    ready.write_text("simulated 3mf content", encoding="utf-8")

    unconfirmed = run_cli(["--sim", "job", str(ready), "--json"], env)
    unconfirmed_payload = json_stdout(unconfirmed)
    if unconfirmed_payload.get("status") != "uploaded_not_printed":
        assert False, (f"unexpected unconfirmed sim-job payload: {unconfirmed_payload}")
    if unconfirmed_payload.get("command") != "job":
        assert False, (f"unconfirmed sim-job payload did not include command: {unconfirmed_payload}")
    if unconfirmed_payload.get("uploaded") is not True or unconfirmed_payload.get("printed") is not False:
        assert False, (f"unconfirmed sim-job payload did not preserve print safety: {unconfirmed_payload}")
    if unconfirmed_payload.get("next_command") != ["print", "ready file.3mf", "--confirm", "--json"]:
        assert False, (f"unconfirmed sim-job payload did not include the next print command: {unconfirmed_payload}")

    result = run_cli(["--sim", "job", str(ready), "--confirm", "--json"], env)
    payload = json_stdout(result)
    if payload.get("status") != "printed" or payload.get("uploaded") is not True or payload.get("printed") is not True:
        assert False, (f"unexpected sim-job payload: {payload}")
    if payload.get("command") != "job":
        assert False, (f"sim-job payload did not include command: {payload}")
    if payload.get("remote_name") != "ready file.3mf":
        assert False, (f"sim-job did not preserve remote filename with spaces: {payload}")
    print("sim-job-json smoke ok")


def smoke_send_alias_json(root):
    env = isolated_env(root)
    ready = root / "ready.3mf"
    ready.write_text("simulated 3mf content", encoding="utf-8")

    result = run_cli(["--sim", "send", str(ready), "--upload-only", "--json"], env)
    payload = json_stdout(result)
    if payload.get("status") != "uploaded" or payload.get("uploaded") is not True:
        assert False, (f"unexpected send-alias payload: {payload}")
    if payload.get("command") != "send":
        assert False, (f"send-alias payload did not preserve command: {payload}")
    if payload.get("printed") is not False:
        assert False, (f"send-alias upload-only payload reported a print start: {payload}")
    if payload.get("next_command") != ["print", "ready.3mf", "--confirm", "--json"]:
        assert False, (f"send-alias upload-only payload did not include the next print command: {payload}")
    print("send-alias-json smoke ok")


def smoke_sim_lower_level_json(root):
    env = isolated_env(root)
    ready = root / "ready.3mf"
    ready.write_text("simulated 3mf content", encoding="utf-8")

    status = json_stdout(run_cli(["--sim", "status", "--json"], env))
    if status.get("status") != "ok" or status.get("command") != "status":
        assert False, (f"status JSON is not self-describing: {status}")
    if status.get("printer", {}).get("gcode_state") != "IDLE" or status.get("gcode_state") != "IDLE":
        assert False, (f"status JSON did not preserve printer state fields: {status}")

    files = json_stdout(run_cli(["--sim", "files", "--json"], env))
    if files.get("status") != "ok" or files.get("command") != "files":
        assert False, (f"files JSON is not self-describing: {files}")
    if not isinstance(files.get("files"), list):
        assert False, (f"files JSON did not include file list: {files}")

    upload = json_stdout(run_cli(["--sim", "upload", str(ready), "--dry-run", "--json"], env))
    if upload.get("status") != "dry_run_ok" or upload.get("command") != "upload":
        assert False, (f"upload dry-run JSON is not self-describing: {upload}")
    if upload.get("uploaded") is not False:
        assert False, (f"upload dry-run JSON reported a side effect: {upload}")

    print_required = json_stdout(run_cli(["--sim", "print", "ready.3mf", "--json"], env))
    if print_required.get("status") != "confirmation_required" or print_required.get("command") != "print":
        assert False, (f"print confirmation JSON is not self-describing: {print_required}")
    if print_required.get("printed") is not False:
        assert False, (f"print confirmation JSON reported a print start: {print_required}")
    if print_required.get("next_command") != ["print", "ready.3mf", "--confirm", "--json"]:
        assert False, (f"print confirmation JSON did not include the next command: {print_required}")

    print_reject = json_stdout(run_cli(["--sim", "print", "model.stl", "--json"], env, expected_returncode=3))
    if print_reject.get("status") != "error" or print_reject.get("command") != "print":
        assert False, (f"print model rejection JSON is not self-describing: {print_reject}")
    if print_reject.get("failed_step") != "validate" or print_reject.get("file") != "model.stl":
        assert False, (f"print model rejection did not validate before confirmation: {print_reject}")

    path_reject = json_stdout(run_cli(["--sim", "print", "folder/ready.3mf", "--json"], env, expected_returncode=3))
    if path_reject.get("status") != "error" or path_reject.get("command") != "print":
        assert False, (f"print path rejection JSON is not self-describing: {path_reject}")
    if path_reject.get("failed_step") != "validate" or path_reject.get("file") != "folder/ready.3mf":
        assert False, (f"print path rejection did not reject the raw remote name: {path_reject}")

    print("sim-lower-level-json smoke ok")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        smoke_help_surface(root / "help")
        smoke_setup_json(root / "setup")
        smoke_setup_json_noninteractive_missing_values(root / "setup-missing-values")
        smoke_setup_json_rejects_bad_access_code_file(root / "setup-bad-access-code-file")
        smoke_invalid_config_json(root / "bad-config")
        smoke_parse_error_json(root / "parse-error")
        smoke_preflight_json(root / "preflight")
        smoke_local_job_dry_run_json(root / "local-dry-run")
        smoke_url_job_dry_run_json(root / "url-dry-run")
        smoke_url_job_dry_run_plans_direct_sources(root / "url-dry-run-direct-plan")
        smoke_global_json_flag_json(root / "global-json")
        smoke_download_rejects_non_model_json(root / "download-reject")
        smoke_url_job_dry_run_rejects_non_model_before_output(root / "job-download-reject")
        smoke_sim_local_zip_job_json(root / "sim-local-zip")
        smoke_sim_local_zip_long_name_json(root / "sim-local-zip-long-name")
        smoke_local_zip_extract_error_json(root / "local-zip-extract-error")
        smoke_sim_job_json(root / "sim-job")
        smoke_send_alias_json(root / "send-alias")
        smoke_sim_lower_level_json(root / "sim-lower-level")


if __name__ == "__main__":
    main()
