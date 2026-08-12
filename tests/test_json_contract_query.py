"""Read-only query contracts: status, files."""

from tests.json_contract_base import *  # noqa: F401,F403


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


