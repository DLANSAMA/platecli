"""Schema-backed contract checks for agent JSON envelopes (roadmap Phase D).

Uses a tiny local validator so we do not require the jsonschema package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli.cli import main  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.constants import VERSION  # noqa: E402

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "schemas"


def _load_schema(name: str) -> dict:
    path = SCHEMA_DIR / name
    assert path.is_file(), f"missing schema {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(instance, schema, path="$"):
    """Minimal subset of JSON Schema (type/const/enum/required/properties)."""
    if "const" in schema:
        assert instance == schema["const"], f"{path}: expected const {schema['const']!r}, got {instance!r}"
    if "enum" in schema:
        assert instance in schema["enum"], f"{path}: {instance!r} not in {schema['enum']}"
    if "type" in schema:
        t = schema["type"]
        mapping = {
            "object": dict,
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
        }
        assert isinstance(instance, mapping[t]), f"{path}: type {t} failed for {instance!r}"
    if "minLength" in schema and isinstance(instance, str):
        assert len(instance) >= schema["minLength"], f"{path}: minLength"
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            assert key in instance, f"{path}: missing {key}"
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in instance:
                _validate(instance[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            assert not extra, f"{path}: unexpected keys {extra}"


@pytest.fixture(autouse=True)
def _reset():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None


def test_schemas_exist():
    for name in (
        "error_envelope.json",
        "ok_envelope.json",
        "version.json",
        "status_event.json",
        "job_ok.json",
        "preflight.json",
        "doctor.json",
    ):
        assert (SCHEMA_DIR / name).is_file()


def test_version_payload_matches_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--json", "--version"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("version.json"))
    assert payload["version"] == VERSION


def test_status_ok_matches_ok_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--sim", "status", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("ok_envelope.json"))
    assert payload["command"] == "status"


def test_setup_error_matches_error_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["bambu-cli", "setup", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("error_envelope.json"))
    assert payload["command"] == "setup"
    assert payload["failed_step"] == "validate"


def test_status_event_schema_against_builder():
    from bambu_cli.protocols.mqtt import _status_event

    event = _status_event({"gcode_state": "RUNNING", "mc_percent": 10}, "update")
    _validate(event, _load_schema("status_event.json"))


def _write_valid_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
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


def test_preflight_matches_schema(monkeypatch, tmp_path, capsys):
    """Missing config still emits a preflight envelope with checks[] (error path)."""
    monkeypatch.setattr(sys, "argv", ["bambu-cli", "preflight", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("preflight.json"))
    assert payload["command"] == "preflight"
    assert isinstance(payload.get("checks"), list)


def test_doctor_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "config.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["bambu-cli", "--sim", "doctor", "--json"])
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("doctor.json"))


def test_job_dry_run_matches_schema(monkeypatch, tmp_path, capsys):
    model = tmp_path / "cube.gcode"
    model.write_text("; gcode\n")
    config_path = tmp_path / "config" / "config.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["bambu-cli", "--sim", "job", str(model), "--dry-run", "--json"],
    )
    monkeypatch.setattr(bambu, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(bambu, "setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("job_ok.json"))
