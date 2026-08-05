"""Physical-action contracts: print, upload, gcode."""

from tests.json_contract_base import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# print
# ---------------------------------------------------------------------------


def test_print_confirmation_required_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--sim", "print", "ready.3mf", "--json"])
    assert exc is not None and exc.code == 5  # refusal == EXIT_COMMAND_ERROR, same as stop/delete
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


