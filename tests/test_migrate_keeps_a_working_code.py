"""`setup --migrate-access-code` must never leave the config without a usable code.

With both ``access_code`` and ``access_code_file`` set, migration deletes the
inline key because the file wins. Before the 2026-09-22 fix it did so without
looking at the file: when ``access_code_file`` pointed at a file that did not
exist (or held a placeholder), the only working copy of the printer's access
code was deleted and every printer command then failed.
"""

from __future__ import annotations

import json
import os

from bambu_cli import setup_cmd


def _config(tmp_path, **extra):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"printer_ip": "192.0.2.10", "serial": "SN", **extra}), encoding="utf-8")
    os.chmod(path, 0o600)
    return str(path)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_missing_configured_file_receives_the_inline_code(tmp_path):
    target = tmp_path / "secret" / "access_code"
    cfg = _config(tmp_path, access_code="12345678", access_code_file=str(target))
    result = setup_cmd.migrate_access_code(config_path=cfg)
    assert result["status"] == "migrated"
    assert target.read_text(encoding="utf-8").strip() == "12345678"
    assert "access_code" not in _read(cfg)


def test_placeholder_file_keeps_the_inline_code(tmp_path):
    target = tmp_path / "access_code"
    target.write_text("YOUR_ACCESS_CODE\n", encoding="utf-8")
    cfg = _config(tmp_path, access_code="12345678", access_code_file=str(target))
    result = setup_cmd.migrate_access_code(config_path=cfg)
    assert result["status"] == "error"
    assert "kept" in result["reason"]
    assert _read(cfg)["access_code"] == "12345678"


def test_unreadable_file_keeps_the_inline_code(tmp_path):
    target = tmp_path / "access_code_dir"
    target.mkdir()  # a directory: open() fails
    cfg = _config(tmp_path, access_code="12345678", access_code_file=str(target))
    result = setup_cmd.migrate_access_code(config_path=cfg)
    assert result["status"] == "error"
    assert _read(cfg)["access_code"] == "12345678"


def test_valid_file_still_wins_over_a_stale_inline_copy(tmp_path):
    target = tmp_path / "access_code"
    target.write_text("87654321\n", encoding="utf-8")
    cfg = _config(tmp_path, access_code="12345678", access_code_file=str(target))
    result = setup_cmd.migrate_access_code(config_path=cfg)
    assert result["status"] == "migrated"
    assert "access_code" not in _read(cfg)
    assert target.read_text(encoding="utf-8").strip() == "87654321"
