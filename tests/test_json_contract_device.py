"""Device-state contracts: light/pause/resume, and the stop/delete confirmation gate."""

from tests.json_contract_base import *  # noqa: F401,F403


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
    exc = run_main(monkeypatch, tmp_path, ["--sim", "pause", "--confirm", "--json"])
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
    exc = run_main(monkeypatch, tmp_path, ["--sim", "resume", "--confirm", "--json"])
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


def test_pause_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "pause", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["pause"]},
                "paused": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"] == ["pause", "--confirm", "--json"]


def test_resume_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "resume", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["confirmation_required"]},
                "command": {"enum": ["resume"]},
                "resumed": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"] == ["resume", "--confirm", "--json"]


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


