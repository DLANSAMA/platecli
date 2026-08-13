"""Front-door contract tests for ``plate tui`` (bambu_cli.tui.entry).

Mirrors the ``go`` precedent (tests/test_interactive_session.py): ``--json`` and
a non-TTY stdin both emit the standard error envelope and abort with exit 5, and
a missing Textual extra aborts with exit 1 and an install hint. No Textual app
is ever launched here — all three guards short-circuit before the import.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

import pytest

from bambu_cli import utils  # noqa: E402
from bambu_cli.constants import LOCAL_COMMANDS  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.tui import entry as entry_mod  # noqa: E402
from bambu_cli.tui.entry import cmd_tui  # noqa: E402

def _args(**kwargs) -> argparse.Namespace:
    base = {"cmd": "tui", "sim": False, "json": False, "verbose": False}
    base.update(kwargs)
    return argparse.Namespace(**base)

@pytest.fixture(autouse=True)
def _reset_json_state():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None

@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    yield

def test_json_mode_emits_error_envelope_and_exits_5(capsys):
    with pytest.raises(BambuError) as ei:
        cmd_tui(_args(json=True))
    assert ei.value.exit_code == 5
    assert ei.value.failed_step == "parse"
    assert capsys.readouterr().out == ""
    payload = ei.value.to_error_payload("tui")
    assert payload["status"] == "error"
    assert payload["command"] == "tui"
    assert payload["exit_code"] == 5
    assert payload["failed_step"] == "parse"

def test_non_tty_stdin_aborts_exit_5(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(BambuError) as ei:
        cmd_tui(_args())
    assert ei.value.exit_code == 5
    assert "interactive" in str(ei.value)

def test_missing_textual_extra_aborts_exit_1(monkeypatch, _tty):
    # Simulate the extra not being installed: find_spec returns None only for
    # 'textual'. This drives the missing-extra branch without uninstalling.
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "textual":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(entry_mod.importlib.util, "find_spec", fake_find_spec)
    with pytest.raises(BambuError) as ei:
        cmd_tui(_args())
    assert ei.value.exit_code == 1
    assert "platecli[tui]" in str(ei.value)

def test_tui_in_local_commands_routing():
    # Routing: like `go`, tui must be a LOCAL_COMMAND so it renders its own
    # guidance instead of hard-failing on an unconfigured printer IP.
    assert "tui" in LOCAL_COMMANDS

def test_tui_launches_app_when_all_guards_pass(monkeypatch, _tty):
    # With a TTY, no --json, and Textual "available", cmd_tui delegates to the
    # app runner exactly once with the parsed args — without really opening a UI.
    monkeypatch.setattr(entry_mod, "_textual_available", lambda: True)
    called = {}

    def fake_run_app(args):
        called["args"] = args

    import bambu_cli.tui.app as app_mod

    monkeypatch.setattr(app_mod, "run_app", fake_run_app)
    args = _args()
    cmd_tui(args)
    assert called["args"] is args
