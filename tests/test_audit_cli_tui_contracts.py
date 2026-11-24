"""Regression tests for CLI, preflight, TUI, and API-doc audit fixes."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from bambu_cli import context as _context
from bambu_cli import utils
from bambu_cli.argutils import setup_args_provided
from bambu_cli.cli import main
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR
from bambu_cli.context import RuntimeContext, Settings
from bambu_cli.errors import BambuError

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCHEMA = ROOT / "docs" / "schemas" / "preflight.json"
API_DOC = ROOT / "docs" / "api.md"


@pytest.fixture(autouse=True)
def _reset_globals():
    saved_ctx = _context.get_current()
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    yield
    _context.set_current(saved_ctx)
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None


def _validate_schema(instance, schema, path="$"):
    """Validate JSON instance against published schema (incl. array items)."""
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
        allowed = t if isinstance(t, list) else [t]
        matched = any(
            (instance is None if name == "null" else isinstance(instance, mapping[name]))
            and not (name in ("integer", "number") and isinstance(instance, bool))
            for name in allowed
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
                _validate_schema(instance[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            assert not extra, f"{path}: unexpected keys {extra}"
    elif isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(instance):
            _validate_schema(item, item_schema, f"{path}[{idx}]")


class _NonTtyStdin:
    def isatty(self):
        return False


# ---------------------------------------------------------------------------
# 1. cli.py: _json_setup_should_be_noninteractive exception handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc_cls", [KeyboardInterrupt, EOFError])
def test_json_setup_noninteractive_handles_interrupt_and_eof(monkeypatch, tmp_path, capsys, exc_cls):
    def _raise(_args):
        raise exc_cls()

    monkeypatch.setattr(sys, "argv", ["plate", "setup", "--json"])
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.setup_cmd._cmd_setup_noninteractive", _raise)

    with pytest.raises(SystemExit) as ei:
        main()

    assert ei.value.code == EXIT_COMMAND_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["command"] == "setup"
    assert payload["failed_step"] == "interrupted"
    assert payload["exit_code"] == EXIT_COMMAND_ERROR


def test_json_setup_noninteractive_handles_bambu_error(monkeypatch, tmp_path, capsys):
    def _raise(_args):
        raise BambuError("bad setup", exit_code=EXIT_CONFIG_ERROR, failed_step="validate")

    monkeypatch.setattr(sys, "argv", ["plate", "setup", "--json"])
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.setup_cmd._cmd_setup_noninteractive", _raise)

    with pytest.raises(SystemExit) as ei:
        main()

    assert ei.value.code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["command"] == "setup"
    assert payload["failed_step"] == "validate"
    assert payload["error"] == "bad setup"


def test_json_setup_noninteractive_handles_unexpected_exception(monkeypatch, tmp_path, capsys):
    def _raise(_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(sys, "argv", ["plate", "setup", "--json"])
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("bambu_cli.setup_cmd._cmd_setup_noninteractive", _raise)

    with pytest.raises(SystemExit) as ei:
        main()

    assert ei.value.code == EXIT_COMMAND_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["command"] == "setup"
    assert payload["exit_code"] == EXIT_COMMAND_ERROR
    assert "Unexpected error: boom" in payload["error"]


# ---------------------------------------------------------------------------
# 2. argutils.py: setup_args_provided includes insecure_tls and force
# ---------------------------------------------------------------------------


def test_setup_args_provided_recognizes_insecure_tls_and_force():
    assert setup_args_provided(argparse.Namespace(insecure_tls=True, force=False)) is True
    assert setup_args_provided(argparse.Namespace(insecure_tls=False, force=True)) is True
    assert setup_args_provided(argparse.Namespace(insecure_tls=False, force=False)) is False


def test_setup_insecure_tls_runs_noninteractive_update_mode(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "printer_ip": "192.168.1.50",
                "serial": "01S00A000000001",
                "access_code": "12345678",
                "model": "P1P",
                "nozzle": "0.4",
            }
        ),
        encoding="utf-8",
    )

    config_path.chmod(0o600)

    monkeypatch.setattr(sys, "argv", ["plate", "setup", "--insecure-tls", "--json"])
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.setup_cmd.common.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "configured"
    assert payload["command"] == "setup"
    assert payload["insecure_tls"] is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["insecure_tls"] is True


# ---------------------------------------------------------------------------
# 3. setup_cmd/preflight.py: access-code detail is a dict matching schema
# ---------------------------------------------------------------------------


def test_preflight_access_code_file_detail_matches_schema(monkeypatch, tmp_path, capsys):
    secret_file = tmp_path / "access_code"
    secret_file.write_text("12345678\n", encoding="utf-8")
    secret_file.chmod(0o600)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "printer_ip": "192.168.1.50",
                "serial": "01S00A000000001",
                "access_code_file": str(secret_file),
                "model": "P1P",
                "nozzle": "0.4",
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    monkeypatch.setattr(sys, "argv", ["plate", "preflight", "--json"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.setup_cmd.common.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)

    try:
        main()
    except SystemExit:
        pass

    payload = json.loads(capsys.readouterr().out)
    schema = json.loads(PREFLIGHT_SCHEMA.read_text(encoding="utf-8"))
    _validate_schema(payload, schema)
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        jsonschema.validate(instance=payload, schema=schema)

    access_checks = [c for c in payload["checks"] if c["name"] == "access-code"]
    assert len(access_checks) == 1
    assert access_checks[0]["status"] == "ok"
    assert isinstance(access_checks[0]["detail"], dict)
    assert "access_code_file" in access_checks[0]["detail"]


# ---------------------------------------------------------------------------
# 4. tui/services.py: StatusService locking and close()
# ---------------------------------------------------------------------------


def test_status_service_fetch_after_close_returns_closed_error():
    from bambu_cli.tui.services import StatusService

    class ExplodingCtx:
        def printer(self):
            raise AssertionError("printer() should not be called after close()")

    svc = StatusService(ctx=ExplodingCtx())
    svc.close()
    snap = svc.fetch(argparse.Namespace(sim=False))
    assert snap.ok is False
    assert snap.error == "Status service closed."


def test_status_service_close_unblocked_during_inflight_status():
    from bambu_cli.tui.services import StatusService

    started = threading.Event()
    release = threading.Event()

    class BlockingPrinter:
        def __init__(self):
            self.held = 0
            self.released = 0

        def hold_mqtt(self):
            self.held += 1

        def release_mqtt(self):
            self.released += 1

        def status(self):
            started.set()
            assert release.wait(timeout=2.0)
            return {
                "gcode_state": "IDLE",
                "mc_percent": 0,
                "bed_temper": 25.0,
                "nozzle_temper": 25.0,
            }

    printer = BlockingPrinter()

    class Ctx:
        def printer(self):
            return printer

    svc = StatusService(ctx=Ctx())
    results = []
    t = threading.Thread(target=lambda: results.append(svc.fetch(argparse.Namespace(sim=False))))
    t.start()
    assert started.wait(timeout=2.0)

    # close() must not block waiting on printer.status().
    svc.close()
    assert printer.released == 1

    release.set()
    t.join(timeout=2.0)
    assert len(results) == 1
    assert results[0].ok is True

    after_close = svc.fetch(argparse.Namespace(sim=False))
    assert after_close.ok is False
    assert after_close.error == "Status service closed."


# ---------------------------------------------------------------------------
# 5. tui/screens/dashboard.py: _apply_snapshot guards unmounted screen
# ---------------------------------------------------------------------------


def test_dashboard_apply_snapshot_returns_early_when_unmounted():
    pytest.importorskip("textual")
    from bambu_cli.tui.screens.dashboard import DashboardScreen
    from bambu_cli.tui.services import StatusSnapshot

    screen = DashboardScreen(argparse.Namespace(), status_provider=None)
    screen._fetching = True
    assert screen.is_mounted is False

    def _boom(*_args, **_kwargs):
        raise AssertionError("query_one should not be called when unmounted")

    screen.query_one = _boom  # type: ignore[method-assign]
    screen._apply_snapshot(StatusSnapshot(ok=True, raw={"gcode_state": "IDLE"}))
    assert screen._fetching is False


# ---------------------------------------------------------------------------
# 6. tui/screens/settings.py: reject '=' in override key
# ---------------------------------------------------------------------------


async def test_settings_screen_rejects_equals_in_override_key(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Button, Input, OptionList, Static

    from bambu_cli.interactive.core import GoSteps
    from bambu_cli.tui.app import PlateApp
    from bambu_cli.tui.deps import TuiDeps
    from bambu_cli.tui.screens.prepare import PrepareScreen
    from bambu_cli.tui.screens.settings import SettingsScreen
    from bambu_cli.tui.services import StatusSnapshot
    from tests.tui_text import widget_text

    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    orca.chmod(0o755)
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)

    _context.set_current(
        RuntimeContext(
            settings=Settings(
                printer_ip="192.168.1.50",
                printer_model="P1S",
                nozzle_size="0.4",
                orca_slicer=str(orca),
                profiles_dir=str(profiles),
            )
        )
    )

    class _Status:
        def fetch(self, _args):
            return StatusSnapshot(ok=True, raw={"gcode_state": "IDLE", "mc_percent": 0}, ams={"units": []})

    deps = TuiDeps(
        steps=GoSteps(),
        status_provider=_Status(),
        ams_detector=lambda _args: None,
    )
    app = PlateApp(argparse.Namespace(cmd="tui", sim=False, json=False, verbose=False), deps)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        prepare = app.screen
        assert isinstance(prepare, PrepareScreen)
        prepare.action_settings()
        await pilot.pause()
        settings = app.screen
        assert isinstance(settings, SettingsScreen)

        settings.query_one("#override-key", Input).value = "foo=bar"
        await pilot.pause()
        settings.query_one("#override-value", Input).value = "1"
        settings.query_one("#override-add", Button).press()
        await pilot.pause()

        err = widget_text(settings.query_one("#settings-error", Static))
        assert err == "Setting name cannot contain '='."
        assert settings.query_one("#override-current", OptionList).option_count == 0
        assert "foo=bar" not in settings._process


# ---------------------------------------------------------------------------
# 7. docs/api.md: status example puts gcode_state & mc_percent under printer
# ---------------------------------------------------------------------------


def test_api_doc_status_example_nests_printer_fields():
    text = API_DOC.read_text(encoding="utf-8")
    match = re.search(r"### `status`\s+.*?```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    assert match is not None, "Could not find status JSON example in docs/api.md"
    example = json.loads(match.group(1))
    assert "gcode_state" not in example
    assert "mc_percent" not in example
    assert example["printer"]["gcode_state"] == "IDLE"
    assert example["printer"]["mc_percent"] == 0
