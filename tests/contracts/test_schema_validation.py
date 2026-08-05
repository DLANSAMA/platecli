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
        # JSON Schema allows a list of types; setup.json needs it for the nullable
        # model/nozzle fields, which are absent-as-null in a partial config.
        allowed = t if isinstance(t, list) else [t]
        matched = any(
            instance is None if name == "null" else isinstance(instance, mapping[name]) for name in allowed
        )
        assert matched, f"{path}: type {t} failed for {instance!r}"
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


# Which published schema(s) back each `--json`-emitting subcommand. README.md
# advertises "every command speaks --json with published schemas", so this map is
# what makes that claim enforceable rather than aspirational.
#
# Checked against build_parser() below instead of being a standalone list: a
# hand-maintained inventory drifts (this test previously listed 19 names and had
# already lost status.json), and CLAUDE.md forbids parallel hand-kept inventories.
_COMMAND_SCHEMAS = {
    "config": ["config_cmd.json"],
    "delete": ["delete.json"],
    "doctor": ["doctor.json"],
    "download": ["download.json"],
    "files": ["files.json"],
    "gcode": ["gcode.json"],
    # `go` is interactive-only: `--json` always emits the error envelope in go.json.
    "go": ["go.json"],
    "job": ["job_ok.json", "job_error.json"],
    "light": ["light.json"],
    "pause": ["pause.json"],
    "preflight": ["preflight.json"],
    "print": ["print.json"],
    "resume": ["resume.json"],
    # `send` is first-class but emits the same envelopes as `job`.
    "send": ["job_ok.json", "job_error.json"],
    "setup": ["setup.json"],
    "slice": ["slice.json", "slice_list_settings.json"],
    "snapshot": ["snapshot.json"],
    "status": ["status.json", "status_event.json"],
    "stop": ["stop.json"],
    # `tui` is interactive-only: `--json` always emits the error envelope in tui.json.
    "tui": ["tui.json"],
    "upload": ["upload.json"],
}

# Schemas that are not tied to one subcommand: the shared envelopes plus
# `--version`, which is a global flag rather than a subcommand.
_SHARED_SCHEMAS = {"error_envelope.json", "ok_envelope.json", "version.json"}


def _parser_subcommands():
    """Same derivation idiom as scripts/cli_help_smoke.py, deliberately."""
    from bambu_cli.cli import build_parser

    parser = build_parser()
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" or action.__class__.__name__ == "_SubParsersAction":
            return set(getattr(action, "choices", None) or {})
    raise AssertionError("could not derive subcommands from build_parser()")


def test_every_subcommand_has_a_published_schema():
    """Derived from the parser, so a new subcommand cannot ship schema-less.

    If this fails after adding a command, add its schema to docs/schemas/ and map
    it here -- do not delete the assertion. The README claim depends on it.
    """
    assert _parser_subcommands() == set(_COMMAND_SCHEMAS), (
        "subcommands and schema map disagree; "
        f"parser-only={sorted(_parser_subcommands() - set(_COMMAND_SCHEMAS))}, "
        f"map-only={sorted(set(_COMMAND_SCHEMAS) - _parser_subcommands())}"
    )
    for command, names in _COMMAND_SCHEMAS.items():
        for name in names:
            assert (SCHEMA_DIR / name).is_file(), f"{command}: missing schema {name}"


def test_every_schema_file_is_wellformed_and_self_identifying():
    """Each schema parses, declares the required metadata, and its $id matches its
    filename -- a copy-paste $id is otherwise invisible."""
    found = sorted(p.name for p in SCHEMA_DIR.glob("*.json"))
    assert found, "no schemas found"
    for name in found:
        schema = _load_schema(name)
        for key in ("$schema", "$id", "title", "type"):
            assert key in schema, f"{name}: missing {key!r}"
        assert schema["$id"].rsplit("/", 1)[-1] == name, (
            f"{name}: $id {schema['$id']!r} does not match filename"
        )


def test_no_orphan_schema_files():
    """Every published schema is reachable from a subcommand or is a shared
    envelope -- catches a schema left behind after a command is renamed."""
    mapped = {name for names in _COMMAND_SCHEMAS.values() for name in names} | _SHARED_SCHEMAS
    found = {p.name for p in SCHEMA_DIR.glob("*.json")}
    assert not (found - mapped), f"unreferenced schema files: {sorted(found - mapped)}"


def test_api_doc_lists_every_schema():
    """docs/api.md carries a hand-written schema table that has drifted before.

    README.md points agents at it, so a schema missing from the table is
    effectively unpublished even though the file exists.
    """
    api = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    missing = [p.name for p in sorted(SCHEMA_DIR.glob("*.json")) if f"schemas/{p.name}" not in api]
    assert not missing, f"schemas absent from docs/api.md: {missing}"


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
    # print without --confirm refuses with EXIT_COMMAND_ERROR (5)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 5
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


def test_stop_confirmation_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "stop", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("stop.json"))
    assert payload["status"] == "confirmation_required"
    assert payload["stopped"] is False


def test_stop_success_fixture_matches_schema():
    payload = {"status": "stopped", "command": "stop", "stopped": True}
    _validate(payload, _load_schema("stop.json"))


def test_files_listing_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "files", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("files.json"))
    assert payload["count"] == len(payload["files"])


def test_files_empty_listing_matches_schema():
    """count/files must still validate when the printer holds nothing."""
    _validate({"status": "ok", "command": "files", "count": 0, "files": []}, _load_schema("files.json"))


def test_upload_matches_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config" / "cfg.json"
    _write_valid_config(config_path)
    model = tmp_path / "probe.gcode.3mf"
    model.write_bytes(b"probe payload")
    monkeypatch.setattr(sys, "argv", ["plate", "--sim", "upload", str(model), "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    main()
    payload = json.loads(capsys.readouterr().out)
    _validate(payload, _load_schema("upload.json"))
    assert payload["uploaded"] is True
    assert payload["remote_name"] == "probe.gcode.3mf"


def test_upload_dry_run_fixture_matches_schema():
    payload = {
        "status": "dry_run_ok",
        "command": "upload",
        "file": "/tmp/probe.gcode.3mf",
        "remote_name": "probe.gcode.3mf",
        "bytes": 13,
        "uploaded": False,
    }
    _validate(payload, _load_schema("upload.json"))


def test_setup_summary_matches_schema():
    """Built by the real _setup_summary, not a hand-written fixture, so the schema
    tracks the function rather than someone's memory of it."""
    from bambu_cli.setup_cmd.common import _setup_summary

    with_file = _setup_summary(
        {
            "printer_ip": "127.0.0.1",
            "serial": "CONTRACTTESTSERIAL",
            "access_code_file": "/tmp/access_code",
            "model": "P1P",
            "nozzle": "0.4",
            "orca_slicer": "/opt/orca",
            "profiles_dir": "/opt/profiles",
            "cert_fingerprint": "aa" * 32,
        }
    )
    _validate(with_file, _load_schema("setup.json"))
    assert with_file["access_code_storage"] == "file"
    assert with_file["access_code_file"] == "/tmp/access_code"

    # Inline storage omits access_code_file entirely, and an empty config leaves
    # model/nozzle null -- both must still validate.
    inline = _setup_summary({"printer_ip": "127.0.0.1", "serial": "S", "access_code": "CODE"})
    _validate(inline, _load_schema("setup.json"))
    assert inline["access_code_storage"] == "inline"
    assert "access_code_file" not in inline

    # The summary must never carry the secret itself.
    for payload in (with_file, inline):
        assert "access_code" not in payload
        assert "CODE" not in json.dumps(payload)


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


def test_pause_confirmation_fixture_matches_schema():
    payload = {
        "status": "confirmation_required",
        "command": "pause",
        "paused": False,
        "next_command": ["pause", "--confirm", "--json"],
    }
    _validate(payload, _load_schema("pause.json"))


def test_resume_confirmation_fixture_matches_schema():
    payload = {
        "status": "confirmation_required",
        "command": "resume",
        "resumed": False,
        "next_command": ["resume", "--confirm", "--json"],
    }
    _validate(payload, _load_schema("resume.json"))


def test_snapshot_success_fixture_matches_schema():
    """Hand-written fixture: snapshot requires injecting a real grab_frame + camera
    TLS stack; the hermetic seam exists (tests/test_camera_cmd.py:855) but is not
    imported here to keep the contract suite's dependency footprint minimal.
    The fixture guards schema shape; the camera cmd test guards the real emitter.
    """
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
    """Success shape from download/downloader._record_download_success.

    Remains a hand-written fixture because download requires a real HTTP server;
    --sim is not supported for the download subcommand. The fixture is cross-checked
    against the schema to guard against schema-fixture drift, but won't catch drift
    in the production _record_download_success payload shape. That shape is covered
    by tests/test_download_hardening_p0.py which drives the real emitter.
    """
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


def test_slice_success_real_output_matches_schema(tmp_path, monkeypatch, capsys):
    """Slice success envelope captured from real slicer/output.py emit_json via orca stub.

    Previously validated a hand-written fixture; now drives the real emitter (the
    fake OrcaSlicer launcher from tests/fakes/orca_stub) so schema drift in
    slicer/output.py's ``emit_json`` payload fails this test, not just fixture drift.
    """
    import argparse

    from bambu_cli.slicer import cmd_slice
    from tests.bambu_test_base import settings_ctx
    from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl

    # A DISPLAY makes _build_orcaslicer_cmd skip the xvfb-run prefix on Linux so
    # the fake launcher runs directly; harmless on macOS/Windows.
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ORCA_STUB_SCENARIO", "success")

    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    args = argparse.Namespace(
        file=model,
        output=str(outdir),
        quality="standard",
        copies=1,
        infill=15,
        pattern="3dhoneycomb",
        supports=False,
        nozzle_temp=220,
        bed_temp=60,
        filament="PLA Basic",
        json=True,
        threads=None,
        list_settings=False,
    )
    with settings_ctx(orca_slicer=launcher, profiles_dir=profiles):
        cmd_slice(args)

    # emit_json pretty-prints (indent=2), so the envelope spans multiple lines;
    # decode the last balanced JSON object from stdout.
    out = capsys.readouterr().out
    decoder = json.JSONDecoder()
    payload = None
    idx = 0
    while idx < len(out):
        brace = out.find("{", idx)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(out, brace)
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        payload = obj
        idx = end
    assert payload is not None and payload.get("status") == "sliced", (
        f"No 'sliced' JSON envelope found in output:\n{out}"
    )
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
