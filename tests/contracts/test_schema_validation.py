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
        "job_error.json",
        "preflight.json",
        "doctor.json",
        "slice.json",
        "download.json",
        "config_cmd.json",
        "gcode.json",
        "snapshot.json",
        "light.json",
        "pause.json",
        "resume.json",
        "print.json",
        "delete.json",
        "slice_list_settings.json",
    ):
        assert (SCHEMA_DIR / name).is_file()


def test_version_payload_matches_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["plate", "--json", "--version"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("version.json"))
    assert payload["version"] == VERSION


def test_status_ok_matches_ok_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "status", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("ok_envelope.json"))
    assert payload["command"] == "status"


def test_status_ok_matches_status_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "status", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("status.json"))


def test_setup_error_matches_error_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["plate", "setup", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
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
    monkeypatch.setattr(sys, "argv", ["plate", "preflight", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("preflight.json"))
    assert payload["command"] == "preflight"
    assert isinstance(payload.get("checks"), list)


def test_doctor_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "config.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "doctor", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
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
        ["plate", "--sim", "job", str(model), "--dry-run", "--json"],
    )
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("job_ok.json"))


def test_gcode_confirmation_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "gcode", "G28", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("gcode.json"))
    assert payload["status"] == "confirmation_required"
    assert payload["sent"] is False


def test_gcode_sent_fixture_matches_schema():
    payload = {"status": "sent", "command": "gcode", "gcode": "G28", "sent": True}
    _validate(payload, _load_schema("gcode.json"))


def test_print_confirmation_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["plate", "--sim", "print", "cube.gcode.3mf", "--json"],
    )
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()  # print without --confirm returns without SystemExit
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("print.json"))
    assert payload["status"] == "confirmation_required"
    assert payload["printed"] is False


def test_print_started_fixture_matches_schema():
    payload = {
        "status": "print_started",
        "command": "print",
        "file": "cube.gcode.3mf",
        "printed": True,
        "dry_run": False,
    }
    _validate(payload, _load_schema("print.json"))


def test_delete_confirmation_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "delete", "cube.gcode.3mf", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("delete.json"))
    assert payload["status"] == "confirmation_required"
    assert payload["deleted"] is False


def test_delete_success_fixture_matches_schema():
    payload = {
        "status": "deleted",
        "command": "delete",
        "file": "cube.gcode.3mf",
        "deleted": True,
    }
    _validate(payload, _load_schema("delete.json"))


def test_light_success_fixture_matches_schema():
    payload = {"status": "light_changed", "command": "light", "action": "on", "changed": True}
    _validate(payload, _load_schema("light.json"))


def test_pause_success_fixture_matches_schema():
    payload = {"status": "paused", "command": "pause", "paused": True}
    _validate(payload, _load_schema("pause.json"))


def test_resume_success_fixture_matches_schema():
    payload = {"status": "resumed", "command": "resume", "resumed": True}
    _validate(payload, _load_schema("resume.json"))


def test_snapshot_success_fixture_matches_schema():
    payload = {
        "status": "saved",
        "command": "snapshot",
        "output": "/tmp/snap.png",
        "size_bytes": 12000,
        "captured_at": "2026-07-24T19:15:30Z",
        "sha256": "a" * 64,
        "method": "direct",
    }
    _validate(payload, _load_schema("snapshot.json"))


def test_device_command_errors_match_error_envelope(monkeypatch, tmp_path, capsys):
    """Invalid gcode still uses the shared error envelope."""
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "gcode", "", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("error_envelope.json"))
    assert payload["command"] == "gcode"


def test_job_error_matches_job_error_and_error_envelope(monkeypatch, tmp_path, capsys):
    """Missing source emits the job summary error shape (error_envelope + job fields)."""
    config_path = tmp_path / "config" / "config.json"
    _write_valid_config(config_path)
    missing = tmp_path / "missing.stl"
    monkeypatch.setattr(
        sys,
        "argv",
        ["plate", "--sim", "job", str(missing), "--dry-run", "--json"],
    )
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("job_error.json"))
    _validate(payload, _load_schema("error_envelope.json"))
    assert payload["command"] == "job"
    assert payload["failed_step"] == "validate"
    assert payload["status"] == "error"


def test_config_show_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "config", "show", "--json"])
    # common._config_path() imports CONFIG_PATH by name — patch both bindings.
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.setup_cmd.common.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("config_cmd.json"))
    assert payload["action"] == "show"
    assert payload["status"] == "ok"
    assert isinstance(payload.get("config"), dict)
    # Secrets must never appear in cleartext.
    assert payload["config"].get("access_code") in (None, "<redacted>")


def test_config_validate_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "config", "validate", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.setup_cmd.common.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    # validate may exit non-zero if orca/profiles missing; still emit config envelope.
    try:
        main()
    except SystemExit:
        pass
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("config_cmd.json"))
    assert payload["action"] == "validate"
    assert payload["command"] == "config"
    assert isinstance(payload.get("checks"), list)


def test_download_error_matches_error_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["plate", "download", "not-a-url", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("error_envelope.json"))
    assert payload["command"] == "download"
    assert payload["failed_step"] == "validate"


def test_download_success_fixture_matches_schema():
    """Success shape from download/downloader._record_download_success (cannot --sim network)."""
    payload = {
        "status": "downloaded",
        "command": "download",
        "source": "https://example.com/model.stl",
        "normalized_source": None,
        "download_url": "https://example.com/model.stl",
        "path": "/tmp/model.stl",
        "filename": "model.stl",
        "bytes": 1024,
    }
    _validate(payload, _load_schema("download.json"))


def test_download_archive_success_fixture_matches_schema():
    payload = {
        "status": "downloaded",
        "command": "download",
        "source": "https://example.com/pack.zip",
        "normalized_source": None,
        "download_url": "https://example.com/pack.zip",
        "path": "/tmp/pack/model.stl",
        "filename": "model.stl",
        "archive_entry": "model.stl",
        "bytes": 2048,
    }
    _validate(payload, _load_schema("download.json"))


def test_slice_success_fixture_matches_schema():
    """Success shape from slicer/output.py emit_json (Orca not hermetic in contract suite)."""
    payload = {
        "status": "sliced",
        "command": "slice",
        "file": "/tmp/cube.stl",
        "path": "/tmp/cube.gcode.3mf",
        "filename": "cube.gcode.3mf",
        "bytes": 4096,
        "step_converted": False,
    }
    _validate(payload, _load_schema("slice.json"))


def test_slice_list_settings_matches_schema(monkeypatch, tmp_path, capsys):
    """`slice --list-settings --json` discovery envelope (agent override vocabulary)."""
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "filament").mkdir(parents=True)
    (profiles / "process" / "std.json").write_text(
        json.dumps({"wall_loops": "2", "layer_height": "0.2", "name": "std"}), encoding="utf-8"
    )
    (profiles / "filament" / "pla.json").write_text(json.dumps({"flow_ratio": "1.0", "name": "pla"}), encoding="utf-8")
    config_path = tmp_path / "config" / "cfg.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "printer_ip": "127.0.0.1",
                "serial": "CONTRACTTESTSERIAL",
                "access_code": "CONTRACTTESTCODE",
                "model": "P1P",
                "nozzle": "0.4",
                "profiles_dir": str(profiles),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["plate", "--json", "slice", "--list-settings"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("slice_list_settings.json"))
    assert payload["action"] == "list_settings"
    assert payload["process"]["count"] >= 1
    assert "wall_loops" in payload["process"]["settings"]
    assert "flow_ratio" in payload["filament"]["settings"]
    # bookkeeping keys must not leak into the settable surface
    assert "name" not in payload["process"]["settings"]


def test_slice_error_matches_error_envelope(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "nope.stl"
    monkeypatch.setattr(sys, "argv", ["plate", "slice", str(missing), "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("error_envelope.json"))
    assert payload["command"] == "slice"
