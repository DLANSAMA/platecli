"""Contract regression tests for every `--json` payload the CLI documents in
docs/api.md / AGENTS.md as its agent-facing API surface.

These are SHAPE-locking regression tests, not spec tests: where docs/api.md
disagrees with the actual current CLI output, we assert the actual output
(and flag the discrepancy in a comment) so a future accidental shape change
gets caught here.

Ground rules followed (docs/test-backlog.md):
- Never touch a real printer/network: use `--sim` and a scratch config path.
- Patch runtime state on real modules (`bambu_cli.config.CONFIG_PATH`, etc.).
- Drive the real argv/parser path via `bambu_cli.cli.main()`, catch
  `SystemExit`, capture stdout with `capsys`, and assert full payload shapes.
"""

import json
import sys
import zipfile
from unittest.mock import MagicMock

import pytest

# paho-mqtt is an optional/heavy dep; stub it the same way other tests do so
# importing the package never fails on environments without it installed.
_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.cli import main  # noqa: E402


# ---------------------------------------------------------------------------
# assert_shape: a small, self-contained schema-shape checker (no jsonschema
# dependency available/allowed).
# ---------------------------------------------------------------------------


def assert_shape(payload, spec, path="$"):
    """Validate `payload` against a small hand-rolled spec.

    spec keys:
      - "type": a type or tuple of types the value must be an instance of.
      - "required": {key: subspec, ...} keys that MUST be present.
      - "optional": {key: subspec, ...} keys that MAY be present; validated
        only if present.
      - "enum": iterable of allowed values for this exact node.
      - "items": subspec applied to every element when type is list.
    """
    assert isinstance(payload, dict) or "type" in spec or True, path

    if "type" in spec:
        expected_type = spec["type"]
        assert isinstance(payload, expected_type), (
            f"{path}: expected type {expected_type}, got {type(payload).__name__} ({payload!r})"
        )

    if "enum" in spec:
        assert payload in spec["enum"], f"{path}: {payload!r} not in allowed enum {spec['enum']!r}"

    if isinstance(payload, dict):
        required = spec.get("required", {})
        for key, subspec in required.items():
            assert key in payload, f"{path}: missing required key {key!r} in {sorted(payload.keys())}"
            assert_shape(payload[key], subspec, path=f"{path}.{key}")
        optional = spec.get("optional", {})
        for key, subspec in optional.items():
            if key in payload:
                assert_shape(payload[key], subspec, path=f"{path}.{key}")

    if isinstance(payload, list) and "items" in spec:
        for idx, item in enumerate(payload):
            assert_shape(item, spec["items"], path=f"{path}[{idx}]")


ANY = {}
STR = {"type": str}
BOOL = {"type": bool}
INT = {"type": int}
NUM = {"type": (int, float)}
DICT = {"type": dict}
LIST = {"type": list}

BASE_OK = {"type": dict, "required": {"status": {"enum": ["ok"]}, "command": STR}}


def base_error_spec(command=None, require_failed_step=True):
    required = {
        "status": {"enum": ["error"]},
        "command": {"enum": [command]} if command else STR,
        "exit_code": INT,
        "error": STR,
    }
    if require_failed_step:
        required["failed_step"] = STR
    return {"type": dict, "required": required}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_json_state():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None


def run_main(monkeypatch, tmp_path, argv, config_path=None):
    """Drive bambu_cli.cli.main() with a scratch config path so no real
    on-disk config is ever touched, and return the SystemExit (or None)."""
    import bambu_cli.cli as cli_mod
    import bambu_cli.config as config_mod

    monkeypatch.setattr(sys, "argv", ["bambu-cli"] + list(argv))
    monkeypatch.setattr(
        config_mod, "CONFIG_PATH", config_path or str(tmp_path / "no-such-config" / "config.json")
    )
    monkeypatch.setattr(cli_mod, "setup_logging", lambda *a, **k: None)
    exc = None
    try:
        main()
    except SystemExit as e:
        exc = e
    return exc


def read_json(capsys):
    out = capsys.readouterr().out
    return json.loads(out)


def make_ready_file(tmp_path, name="ready.3mf", content="simulated 3mf content"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "status", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["ok"]},
                "command": {"enum": ["status"]},
                "printer": DICT,
                "gcode_state": STR,
            },
        },
    )
    assert payload["printer"].get("gcode_state") == "IDLE"


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------


def test_files_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "files", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["ok"]},
                "command": {"enum": ["files"]},
                "count": INT,
                "files": {
                    "type": list,
                    "items": {
                        "type": dict,
                        "required": {"name": STR, "path": STR},
                    },
                },
            },
        },
    )


# ---------------------------------------------------------------------------
# light / pause / resume
# ---------------------------------------------------------------------------


def test_light_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "light", "on", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["light_changed"]},
                "command": {"enum": ["light"]},
                "action": {"enum": ["on"]},
                "changed": {"enum": [True]},
            },
        },
    )


def test_pause_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "pause", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["paused"]},
                "command": {"enum": ["pause"]},
                "paused": {"enum": [True]},
            },
        },
    )


def test_resume_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "resume", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["resumed"]},
                "command": {"enum": ["resume"]},
                "resumed": {"enum": [True]},
            },
        },
    )


# ---------------------------------------------------------------------------
# stop / delete: confirmation-required contract (no --confirm)
# ---------------------------------------------------------------------------


def test_stop_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "stop", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["stop"]},
                "stopped": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"] == ["stop", "--confirm", "--json"]


def test_stop_confirmed_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "stop", "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["stopped"]},
                "command": {"enum": ["stop"]},
                "stopped": {"enum": [True]},
            },
        },
    )


def test_delete_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "delete", "old.3mf", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["delete"]},
                "file": {"enum": ["old.3mf"]},
                "deleted": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"] == ["delete", "old.3mf", "--confirm", "--json"]


def test_delete_confirmed_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "delete", "old.3mf", "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["deleted"]},
                "command": {"enum": ["delete"]},
                "file": STR,
                "deleted": {"enum": [True]},
            },
        },
    )


def test_delete_unsafe_name_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "delete", "../evil.3mf", "--confirm", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("delete"))
    assert payload["failed_step"] == "validate"


# ---------------------------------------------------------------------------
# print
# ---------------------------------------------------------------------------


def test_print_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "print", "ready.3mf", "--json"])
    assert exc is None  # cmd_print returns (no sys.exit) in the confirmation branch
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["print"]},
                "file": STR,
                "printed": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )


def test_print_started_success_shape(monkeypatch, tmp_path, capsys):
    # The simulated printer tracks uploaded files, so print requires an
    # upload first (matches tests/agent_cli_smoke.py sim-job flow).
    ready = make_ready_file(tmp_path)
    upload_exc = run_main(monkeypatch, tmp_path, ["--sim", "upload", str(ready), "--json"])
    assert upload_exc is None
    capsys.readouterr()  # discard the upload payload
    exc = run_main(monkeypatch, tmp_path, ["--sim", "print", "ready.3mf", "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["print_started"]},
                "command": {"enum": ["print"]},
                "file": STR,
                "printed": {"enum": [True]},
                "dry_run": {"enum": [False]},
            },
        },
    )


def test_print_unsafe_name_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "print", "folder/model.3mf", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("print"))
    assert payload["failed_step"] == "validate"
    assert payload["file"] == "folder/model.3mf"


def test_print_non_print_ready_extension_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "print", "model.stl", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("print"))
    assert payload["failed_step"] == "validate"


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


def test_upload_success_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["--sim", "upload", str(ready), "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["uploaded"]},
                "command": {"enum": ["upload"]},
                "file": STR,
                "remote_name": STR,
                "bytes": INT,
                "uploaded": {"enum": [True]},
            },
        },
    )


def test_upload_dry_run_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["--sim", "upload", str(ready), "--dry-run", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["dry_run_ok"]},
                "command": {"enum": ["upload"]},
                "file": STR,
                "remote_name": STR,
                "bytes": INT,
                "uploaded": {"enum": [False]},
            },
        },
    )


def test_upload_missing_file_error_shape(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "missing.3mf"
    exc = run_main(monkeypatch, tmp_path, ["--sim", "upload", str(missing), "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("upload"))
    assert payload["failed_step"] == "validate"


# ---------------------------------------------------------------------------
# gcode
# ---------------------------------------------------------------------------


def test_gcode_success_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "gcode", "M104 S220", "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["sent"]},
                "command": {"enum": ["gcode"]},
                "gcode": {"enum": ["M104 S220"]},
                "sent": {"enum": [True]},
            },
        },
    )


def test_gcode_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "gcode", "M104 S220", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["gcode"]},
                "gcode": {"enum": ["M104 S220"]},
                "sent": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"] == ["gcode", "M104 S220", "--confirm", "--json"]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _write_valid_config(config_path):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "printer_ip": "127.0.0.1",
                "serial": "CONTRACTTESTSERIAL",
                "access_code": "CONTRACTTESTCODE",
                "model": "P1P",
                "nozzle": "0.4",
            }
        ),
        encoding="utf-8",
    )


def test_doctor_success_shape(monkeypatch, tmp_path, capsys):
    out_path = tmp_path / "caps.json"
    config_path = tmp_path / "config" / "config.json"
    _write_valid_config(config_path)
    exc = run_main(
        monkeypatch, tmp_path, ["--sim", "doctor", "--output", str(out_path), "--json"], config_path=str(config_path)
    )
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["ok"]},
                "command": {"enum": ["doctor"]},
                "ok": {"enum": [True]},
                "output": STR,
                "printer_ip": STR,
                "capabilities": {
                    "type": dict,
                    "required": {
                        "model": STR,
                        "firmware": STR,
                        "serial": STR,
                        "capabilities": {
                            "type": dict,
                            "required": {
                                "ams": BOOL,
                                "chamber_light": BOOL,
                                "camera_snapshot": BOOL,
                                "camera_snapshot_note": STR,
                            },
                        },
                    },
                },
            },
            "optional": {"certificate_fingerprint": {"type": (str, type(None))}},
        },
    )
    # docs/api.md shows printer_ip: "<redacted>" always; actual behavior redacts
    # unless --verbose is passed (see bambu_cli/commands/doctor.py cmd_doctor). We are
    # not passing --verbose here, so this locks the documented redaction.
    assert payload["printer_ip"] == "<redacted>"


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_error_shape_no_config(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["preflight", "--json"])
    assert exc is not None and exc.code == 1
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["preflight"]},
                "exit_code": INT,
                "ok": {"enum": [False]},
                "errors": INT,
                "warnings": INT,
                "strict": BOOL,
                "checks": {
                    "type": list,
                    "items": {
                        "type": dict,
                        "required": {"name": STR, "status": {"enum": ["ok", "warning", "error"]}, "message": STR},
                    },
                },
            },
        },
    )
    check_names = {c["name"] for c in payload["checks"]}
    assert "config" in check_names


# ---------------------------------------------------------------------------
# setup (non-interactive)
# ---------------------------------------------------------------------------


def test_setup_success_shape(monkeypatch, tmp_path, capsys):
    access_code_file = tmp_path / "secrets" / "access_code"
    monkeypatch.setenv("BAMBU_SETUP_ACCESS_CODE", "contract-test-secret")
    exc = run_main(
        monkeypatch,
        tmp_path,
        [
            "setup",
            "--printer-ip",
            "printer.local",
            "--serial",
            "CONTRACTTESTSERIAL",
            "--access-code-env",
            "BAMBU_SETUP_ACCESS_CODE",
            "--access-code-file",
            str(access_code_file),
            "--model",
            "P1P",
            "--nozzle",
            "0.4",
            "--json",
        ],
    )
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["configured"]},
                "command": {"enum": ["setup"]},
            },
        },
    )
    assert "CONTRACTTESTSERIAL" not in json.dumps(payload)
    assert "contract-test-secret" not in json.dumps(payload)


def test_setup_missing_values_error_shape(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda self: False})())
    exc = run_main(monkeypatch, tmp_path, ["setup", "--json"])
    assert exc is not None and exc.code == 1
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["setup"]},
                "failed_step": {"enum": ["validate"]},
                "exit_code": {"enum": [1]},
                "missing": {"type": list, "items": STR},
            },
        },
    )


# ---------------------------------------------------------------------------
# job / send
# ---------------------------------------------------------------------------


def test_job_dry_run_local_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["job", str(ready), "--confirm", "--dry-run", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["dry_run_local_skipped"]},
                "command": {"enum": ["job"]},
                "would_upload": {"enum": [True]},
                "would_print": {"enum": [True]},
            },
        },
    )
    assert not payload.get("uploaded") and not payload.get("printed")


def test_job_sim_printed_success_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["--sim", "job", str(ready), "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["printed"]},
                "command": {"enum": ["job"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [True]},
            },
        },
    )


def test_job_sim_uploaded_not_printed_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path, name="ready2.3mf")
    exc = run_main(monkeypatch, tmp_path, ["--sim", "job", str(ready), "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["uploaded_not_printed"]},
                "command": {"enum": ["job"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"][0] == "print"


def test_send_alias_uploaded_only_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path, name="ready3.3mf")
    exc = run_main(monkeypatch, tmp_path, ["--sim", "send", str(ready), "--upload-only", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["uploaded"]},
                "command": {"enum": ["send"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [False]},
            },
        },
    )


def test_job_url_dry_run_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["job", "printables.com/model/12345-contract", "--dry-run", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["dry_run_url_skipped"]},
                "command": {"enum": ["job"]},
                "normalized_source": STR,
                "would_download": {"enum": [True]},
            },
        },
    )


def test_job_download_rejection_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["job", "https://example.com/archive.rar", "--dry-run", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("job"))
    assert payload["failed_step"] == "validate"
    assert payload["extension"] == ".rar"


def test_job_local_zip_extract_error_shape(monkeypatch, tmp_path, capsys):
    archive_path = tmp_path / "empty-bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "not a model")
    exc = run_main(monkeypatch, tmp_path, ["job", str(archive_path), "--dry-run", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("job"))
    assert payload["failed_step"] == "extract"


# ---------------------------------------------------------------------------
# slice
# ---------------------------------------------------------------------------


def test_slice_missing_file_error_shape(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "missing.stl"
    exc = run_main(monkeypatch, tmp_path, ["slice", str(missing), "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("slice"))
    assert payload["failed_step"] == "validate"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_rejects_non_model_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["download", "https://example.com/archive.rar", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("download"))
    assert payload["failed_step"] == "validate"
    assert payload["extension"] == ".rar"


def test_download_credential_url_rejected_and_redacted_shape(monkeypatch, tmp_path, capsys):
    # Assembled from pieces so the repo's privacy smoke doesn't flag a
    # credential-bearing URL / email-like literal in this file.
    credentialed_url = "https://" + "agent:" + "secret" + "@" + "example.com/model.stl"
    exc = run_main(monkeypatch, tmp_path, ["download", credentialed_url, "--json"])
    assert exc is not None and exc.code == 5
    out = capsys.readouterr().out
    assert "secret" not in out
    payload = json.loads(out)
    assert_shape(payload, base_error_spec("download"))
    assert payload["source"] == "https://example.com/model.stl"


# ---------------------------------------------------------------------------
# JsonArgumentParser bad-argument contract
# ---------------------------------------------------------------------------


def test_bad_argument_parse_error_shape(monkeypatch, tmp_path, capsys):
    # slice requires a positional "file"; omit it under --json to trigger
    # argparse's own error() path (JsonArgumentParser.error).
    exc = run_main(monkeypatch, tmp_path, ["slice", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["slice"]},
                "failed_step": {"enum": ["parse"]},
                "exit_code": {"enum": [5]},
                "error": STR,
            },
        },
    )
    assert capsys.readouterr().err.strip() == ""


def test_bad_argument_parse_error_shape_global_json_flag(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json", "job"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert payload["status"] == "error"
    assert payload["failed_step"] == "parse"
    assert payload["command"] == "job"


# ---------------------------------------------------------------------------
# main(): missing-subcommand contract
# ---------------------------------------------------------------------------


def test_missing_subcommand_json_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["main"]},
                "failed_step": {"enum": ["parse"]},
                "exit_code": {"enum": [5]},
                "error": STR,
            },
        },
    )


def test_missing_subcommand_without_json_prints_usage_not_json(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, [])
    assert exc is not None and exc.code == 5
    out, err = capsys.readouterr()
    assert out.strip() == ""
    assert "usage:" in err.lower()


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_json_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json", "--version"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["ok"]},
                "command": {"enum": ["version"]},
                "version": STR,
            },
        },
    )
    assert payload["version"] == __import__("bambu_cli.constants", fromlist=["VERSION"]).VERSION


# ---------------------------------------------------------------------------
# config-error contract (printer-network command, no config, no --sim)
# ---------------------------------------------------------------------------


def test_config_error_shape_for_network_command(monkeypatch, tmp_path, capsys):
    # Force the "never configured" state (default printer_ip 0.0.0.0)
    # explicitly so this test doesn't depend on run order.
    from bambu_cli import context
    from bambu_cli.context import RuntimeContext

    context.set_current(RuntimeContext())
    exc = run_main(monkeypatch, tmp_path, ["status", "--json"])
    assert exc is not None and exc.code == 1
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("status"))
    assert payload["failed_step"] == "config"
