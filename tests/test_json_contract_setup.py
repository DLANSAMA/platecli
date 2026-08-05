"""Local diagnostic contracts: doctor, preflight, setup."""

from tests.json_contract_base import *  # noqa: F401,F403


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


