"""Platform/config/camera/slicer branch behavior (no hardware).

Renamed historically from coverage padding; every test asserts an outcome.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import camera as camera_mod  # noqa: E402
from bambu_cli import commands as commands_mod  # noqa: E402
from bambu_cli import config as config_mod  # noqa: E402
from bambu_cli import slicer as slicer_mod  # noqa: E402
from bambu_cli.download import downloader as downloader_mod  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.protocols import ftps as ftps_mod  # noqa: E402
from bambu_cli.protocols import mqtt as mqtt_mod  # noqa: E402
from bambu_cli.setup_cmd import common as common_mod  # noqa: E402
from bambu_cli.setup_cmd import preflight as preflight_mod  # noqa: E402
from tests.bambu_test_base import _test_printer  # noqa: E402


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_default_config_path_platforms(platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.sys, "platform", platform)
    if platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    elif platform == "darwin":
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("HOME", raising=False)
    path = config_mod._default_config_path()
    assert "bambu" in path.replace("\\", "/")


def test_convert_step_gmsh_missing(monkeypatch):
    monkeypatch.setattr(slicer_mod.step_convert.shutil, "which", lambda *_a, **_k: None)

    def _no_gmsh(*_a, **_k):
        raise FileNotFoundError("gmsh")

    # Simulate a machine without the gmsh binary regardless of the host env
    # (a real gmsh on PATH previously made this test environment-dependent).
    monkeypatch.setattr(slicer_mod.step_convert.subprocess, "run", _no_gmsh)
    with patch.object(slicer_mod.step_convert.sys, "platform", "linux"):
        path, created = slicer_mod._convert_step_to_stl("/tmp/x.step")
    assert path is None
    assert created is False


def test_camera_simulation_snapshot(tmp_path, capsys):
    out = tmp_path / "snap.jpg"
    args = Namespace(output=str(out), json=True, direct=True)
    printer = _test_printer(simulation_mode=True, insecure_tls=True)
    with (
        patch("bambu_cli.camera.get_printer", return_value=printer, create=True),
        patch("bambu_cli.printer.get_printer", return_value=printer),
        patch.object(camera_mod, "_grab_camera_frame_direct", return_value=b"\xff\xd8\xfffakejpeg"),
    ):
        camera_mod._cmd_snapshot(args)
    assert out.is_file()
    assert out.read_bytes().startswith(b"\xff\xd8\xff")
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("status") == "saved"
    assert payload.get("command") == "snapshot"
    assert payload.get("size_bytes", 0) > 0


def test_preflight_permission_check(tmp_path):
    f = tmp_path / "secret"
    f.write_text("x", encoding="utf-8")
    if sys.platform == "win32":
        assert preflight_mod._file_permission_check(str(f), "secret-file") is None
    else:
        os.chmod(f, 0o644)
        res = preflight_mod._file_permission_check(str(f), "secret-file")
        assert res["status"] in ("ok", "warning", "error")


def test_common_setup_json_error(capsys):
    args = Namespace(json=True)
    common_mod._setup_json_error(args, "boom", foo=1)
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"] == "boom"


def test_ftps_connection_error_path_cleanup():
    ftp = ftps_mod.ImplicitFTPS()
    ftp.printer = _test_printer(cert_fingerprint="aa" * 32, insecure_tls=False)
    with patch.object(ftps_mod.socket, "create_connection", side_effect=OSError("fail")), pytest.raises(OSError):
        ftp.connect("1.1.1.1", 990, 1)


def test_mqtt_require_missing_dependency():
    prev = mqtt_mod.mqtt
    try:
        mqtt_mod.mqtt = None
        with patch.dict("sys.modules", {"paho.mqtt.client": None}), pytest.raises(BambuError):
            mqtt_mod._require_mqtt()
    finally:
        mqtt_mod.mqtt = prev


def test_cmd_gcode_success():
    args = Namespace(code="G28", json=False, confirm=True)
    printer = MagicMock()
    printer.send_command.return_value = True
    with (
        patch("bambu_cli.commands.gcode.RuntimeContext.for_request") as fr,
        patch("bambu_cli.commands.gcode.get_sequence_id", return_value="9"),
    ):
        ctx = MagicMock()
        ctx.printer.return_value = printer
        fr.return_value = ctx
        commands_mod.cmd_gcode(args)
    printer.send_command.assert_called_once()
    payload = printer.send_command.call_args[0][0]
    assert "G28" in payload
    assert "gcode_line" in payload


def test_downloader_exposes_cmd_download():
    assert callable(getattr(downloader_mod, "cmd_download", None) or getattr(downloader_mod, "_cmd_download", None))
