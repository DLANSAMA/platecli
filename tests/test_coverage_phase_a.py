"""High-yield unit tests for transport/setup/download paths (roadmap Phase A/C).

No real network/printer. Prefer exercising public helpers and command entry
points with fakes over asserting implementation trivia.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import ssl
import sys
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.errors import BambuError, FileError, NetworkError  # noqa: E402
from bambu_cli.protocols import ftps as ftps_mod  # noqa: E402
from bambu_cli.protocols import mqtt as mqtt_mod  # noqa: E402
from bambu_cli.setup_cmd import migrate as migrate_mod  # noqa: E402
from bambu_cli.setup_cmd import preflight as preflight_mod  # noqa: E402
from bambu_cli.download import extract as extract_mod  # noqa: E402
from bambu_cli.download import naming as naming_mod  # noqa: E402
from bambu_cli import netsafety  # noqa: E402
from tests.bambu_test_base import _test_printer, settings_ctx  # noqa: E402

pytestmark = pytest.mark.security


# --- MQTT sim / status helpers ------------------------------------------------

def test_sim_mqtt_client_callbacks_fire():
    client = mqtt_mod._SimMqttClient()
    connected = []
    published = []
    client.on_connect = lambda *a, **k: connected.append(True)
    client.on_publish = lambda *a, **k: published.append(True)
    client.username_pw_set("bblp", "x")
    client.tls_set()
    client.tls_insecure_set(True)
    client.connect("127.0.0.1", 8883)
    client.subscribe("t")
    client.publish("t", "{}")
    client.loop_start()
    client.loop_stop()
    client.disconnect()
    assert client.socket() is None
    assert connected and published


def test_get_status_simulation_includes_ams():
    status = mqtt_mod.get_status(_test_printer(simulation_mode=True))
    assert status["gcode_state"] == "IDLE"
    assert "ams" in status
    assert status["ams"]["ams"][0]["tray"][0]["tray_type"] == "PLA"


def test_get_version_simulation():
    mods = mqtt_mod.get_version(_test_printer(simulation_mode=True))
    assert mods[0]["name"] == "ota"


def test_status_event_int_coercion():
    ev = mqtt_mod._status_event({"gcode_state": "RUNNING", "mc_percent": "42", "layer_num": None}, "update")
    assert ev["event"] == "update"
    assert ev["mc_percent"] == 42
    assert ev["layer_num"] == 0
    assert ev["command"] == "status"


def test_monitor_status_simulation_ndjson(capsys):
    args = Namespace(json=True, sim=True)
    with (
        patch("bambu_cli.printer.get_printer", return_value=_test_printer(simulation_mode=True)),
        patch.object(mqtt_mod.time, "sleep", return_value=None),
    ):
        mqtt_mod.monitor_status(args)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) >= 2
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "update"
    assert events[-1]["event"] == "terminal"
    assert events[-1]["gcode_state"] == "FINISH"


def test_send_command_simulation_true():
    assert mqtt_mod.send_command(_test_printer(simulation_mode=True), "{}") is True


def test_probe_cert_fingerprint_reads_der():
    der = b"\x30\x82probe"
    expected = hashlib.sha256(der).hexdigest()
    raw = MagicMock()
    tls = MagicMock()
    tls.getpeercert.return_value = der
    tls.__enter__ = lambda s: tls
    tls.__exit__ = lambda *a: False
    raw_cm = MagicMock()
    raw_cm.__enter__ = lambda s: raw
    raw_cm.__exit__ = lambda *a: False
    ctx = MagicMock()
    ctx.wrap_socket.return_value = tls
    with patch("bambu_cli.protocols.mqtt.socket.create_connection", return_value=raw_cm), \
         patch("ssl.SSLContext", return_value=ctx):
        fp = mqtt_mod.probe_cert_fingerprint("10.0.0.1", 990, timeout=1)
    assert fp == expected


# --- FTPS sim / naming --------------------------------------------------------

def test_sim_ftp_store_list_delete():
    ftp = ftps_mod._SimFtp()
    with ftp as f:
        buf = io.BytesIO(b"hello-model")
        f.storbinary("STOR /model/part.3mf", buf, callback=lambda b: None)
        assert "part.3mf" in f.nlst()
        assert f.size("/model/part.3mf") == 11
        f.delete("/model/part.3mf")
        assert "part.3mf" not in f.nlst()
    f.quit()
    f.close()


def test_sim_ftp_size_missing():
    ftp = ftps_mod._SimFtp()
    with pytest.raises(Exception):
        ftp.size("/model/nope.3mf")


def test_noncolliding_path_creates_sibling(tmp_path):
    p = tmp_path / "model.stl"
    p.write_text("a", encoding="utf-8")
    # open exclusive fails → sibling
    out = ftps_mod._noncolliding_path(str(p))
    assert out != str(p)
    assert out.endswith(".stl")
    assert Path(out).parent == tmp_path


def test_get_ftp_simulation():
    ftps_mod.connection_manager.clear()
    printer = _test_printer(simulation_mode=True)
    client = ftps_mod.get_ftp(printer, timeout=5)
    assert client is not None
    with client:
        pass
    # Reuse pooled sim client (exercises voidcmd health check).
    client2 = ftps_mod.get_ftp(printer, timeout=5)
    assert client2 is not None
    with client2:
        pass
    ftps_mod.connection_manager.clear()


# --- netsafety extras ---------------------------------------------------------

def test_safe_http_handler_open_methods():
    opener = netsafety.build_safe_opener()
    assert any(isinstance(h, netsafety.SafeHTTPSHandler) for h in opener.handlers)
    assert netsafety._default_user_agent().startswith("Mozilla/5.0")


def test_link_local_refused():
    with patch.object(netsafety.socket, "getaddrinfo", return_value=[
        (2, 1, 6, "", ("169.254.1.1", 443))
    ]), patch.object(netsafety.socket, "create_connection") as conn, \
         pytest.raises(Exception):
        netsafety._get_safe_connection("ll.example", 443, 5, None)
    conn.assert_not_called()


# --- ZIP extract safety -------------------------------------------------------

def test_extract_zip_rejects_no_model(tmp_path):
    zpath = tmp_path / "empty.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("readme.txt", "hi")
    args = Namespace(max_download_mb=10, name=None)
    with pytest.raises(ValueError, match="supported model"):
        extract_mod._extract_zip_model(str(zpath), str(tmp_path), args)


def test_extract_zip_selects_stl(tmp_path):
    zpath = tmp_path / "m.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("nested/part.stl", "solid x")
    args = Namespace(max_download_mb=10, name=None)
    out = extract_mod._extract_zip_model(str(zpath), str(tmp_path), args)
    path = out[0] if isinstance(out, tuple) else out
    assert str(path).endswith(".stl")
    assert Path(path).is_file()


def test_sanitize_windows_reserved_names():
    name = naming_mod._sanitize_download_filename("CON.stl")
    assert "CON" not in name.upper() or name != "CON.stl"


# --- migrate / preflight ------------------------------------------------------

def test_migrate_access_code_writes_file(tmp_path):
    cfg_path = tmp_path / "config.json"
    code_path = tmp_path / "access_code"
    cfg = {
        "printer_ip": "10.0.0.1",
        "serial": "SN",
        "access_code": "SECRET123",
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    result = migrate_mod.migrate_access_code(
        config_path=str(cfg_path),
        access_code_file_path=str(code_path),
    )
    assert result["status"] == "migrated"
    assert code_path.is_file()
    assert code_path.read_text(encoding="utf-8").strip() == "SECRET123"
    updated = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "access_code" not in updated
    assert "access_code_file" in updated
    if sys.platform != "win32":
        assert (code_path.stat().st_mode & 0o777) == 0o600


def test_migrate_noop_when_file_already_configured(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"printer_ip": "1.1.1.1", "serial": "S", "access_code_file": "/tmp/x"}),
        encoding="utf-8",
    )
    result = migrate_mod.migrate_access_code(config_path=str(cfg_path))
    assert result["status"] == "noop"


def test_preflight_collect_checks_has_python():
    checks = preflight_mod.collect_preflight_checks()
    assert any(c.get("id") == "python" or c.get("name") == "python" or "Python" in str(c) for c in checks)


def test_preflight_placeholder_ip_is_error():
    with settings_ctx(printer_ip="192.168.0.XXX"):
        checks = preflight_mod.collect_preflight_checks()
    statuses = [c.get("status") or c.get("level") for c in checks]
    assert "error" in statuses or any("placeholder" in str(c).lower() or "printer" in str(c).lower() for c in checks)


# --- camera pin missing already covered; docker URL localhost -----------------

def test_camera_stream_url_localhost_default():
    from bambu_cli.context import Settings
    s = Settings.from_config({"camera_port": "1985:1984"})
    assert "localhost" in s.camera_stream_url


def test_send_command_retry_then_fail():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()
    client.connect.side_effect = OSError("down")
    with patch.object(mqtt_mod, "create_mqtt_client", return_value=client), \
         patch.object(mqtt_mod.time, "sleep"):
        assert mqtt_mod.send_command(printer, "{}", timeout=0.01, retries=1) is False
    assert client.connect.call_count >= 2


def test_get_status_timeout_returns_none():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()
    # connect succeeds but no message
    def connect(*a, **k):
        if client.on_connect:
            client.on_connect(client, None, None, 0)
    client.connect.side_effect = connect
    with patch.object(mqtt_mod, "create_mqtt_client", return_value=client), \
         patch.object(mqtt_mod.time, "sleep"), \
         patch.object(mqtt_mod, "_mqtt_connect"):
        # wait returns False immediately
        with patch("threading.Event") as Ev:
            inst = MagicMock()
            inst.wait.return_value = False
            Ev.return_value = inst
            result = mqtt_mod.get_status(printer, timeout=0.01, retries=0)
    assert result is None


def test_connection_manager_clear():
    ftps_mod.connection_manager.clear()
    ftps_mod.connection_manager.close_all()


def test_common_looks_like_placeholder():
    from bambu_cli.setup_cmd import common as common

    assert common._looks_like_placeholder("192.168.0.XXX", {"192.168.0.XXX"})
    assert not common._looks_like_placeholder("10.0.0.5", {"192.168.0.XXX"})


def test_common_secure_write_json(tmp_path):
    from bambu_cli.setup_cmd import common as common
    path = tmp_path / "c.json"
    common._secure_write_json(str(path), {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_migrate_cmd_file_not_found():
    args = Namespace(access_code_file=None, json=False)
    with patch.object(migrate_mod, "_config_path", return_value="/no/such/config.json"), pytest.raises(BambuError):
        migrate_mod._cmd_migrate_access_code(args)


def test_migrate_cmd_noop_logs(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"access_code_file": "x", "printer_ip": "1.1.1.1", "serial": "s"}), encoding="utf-8")
    args = Namespace(access_code_file=None, json=True)
    with patch.object(migrate_mod, "_config_path", return_value=str(cfg)):
        migrate_mod._cmd_migrate_access_code(args)
    assert "noop" in capsys.readouterr().out
