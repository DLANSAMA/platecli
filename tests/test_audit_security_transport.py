"""Targeted unit tests for security and transport hardening fixes."""

from __future__ import annotations

import hashlib
import io
import os
import ssl
import urllib.error
import urllib.request
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.errors import ConfigError, NetworkError

# ---------------------------------------------------------------------------
# 1. snapshot: proxy-disabled localhost opener, redirect validation, size cap
# ---------------------------------------------------------------------------


def test_snapshot_streamer_opener_disables_proxies_and_validates_redirects(monkeypatch):
    from bambu_cli.commands.snapshot import _build_localhost_streamer_opener

    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://192.0.2.1:8888")
    opener = _build_localhost_streamer_opener()
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers)

    redirect_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(redirect_handlers) == 1
    handler = redirect_handlers[0]

    req = urllib.request.Request("http://localhost:1985/api/frame.jpeg")
    fp = io.BytesIO(b"")
    headers = {}

    for ok_url in (
        "http://localhost:1985/next.jpg",
        "https://127.0.0.1:1985/next.jpg",
        "http://[::1]:1985/next.jpg",
    ):
        redirected = handler.redirect_request(req, fp, 302, "Found", headers, ok_url)
        assert redirected is not None
        assert redirected.full_url == ok_url

    for bad_url in (
        "http://evil.example.com/frame.jpg",
        "https://10.0.0.5/frame.jpg",
        "ftp://localhost/frame.jpg",
        "file://localhost/etc/passwd",
    ):
        with pytest.raises(urllib.error.URLError, match="Refusing camera streamer redirect outside localhost"):
            handler.redirect_request(req, fp, 302, "Found", headers, bad_url)


def test_cmd_snapshot_defaults_to_localhost_streamer_opener(tmp_path):
    from bambu_cli.commands import snapshot as snap_mod
    from bambu_cli.context import RuntimeContext, Settings

    outpath = tmp_path / "snap.jpg"
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"\xff\xd8\xffjpeg"
    fake_opener = MagicMock()
    fake_opener.open.return_value.__enter__.return_value = fake_resp

    ctx = RuntimeContext(
        settings=Settings(
            camera_allow_streamer=True,
            camera_stream_url="http://localhost:1985/api/frame.jpeg?src=p1s",
        )
    )
    args = Namespace(output=str(outpath), json=False, unique=False, allow_camera_streamer=True)
    run_ok = MagicMock(return_value=MagicMock(returncode=0, stdout="true"))

    with patch.object(snap_mod, "_build_localhost_streamer_opener", return_value=fake_opener) as build_mock:
        snap_mod.cmd_snapshot(
            args,
            ctx=ctx,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_ok,
        )

    build_mock.assert_called_once_with()
    fake_opener.open.assert_called_once()
    fake_resp.read.assert_called_once_with(snap_mod._MAX_SNAPSHOT_BYTES + 1)
    assert outpath.read_bytes() == b"\xff\xd8\xffjpeg"


def test_cmd_snapshot_aborts_when_streamer_exceeds_size_limit(tmp_path):
    from bambu_cli.commands import snapshot as snap_mod
    from bambu_cli.context import RuntimeContext, Settings

    outpath = tmp_path / "oversized.jpg"
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"x" * (snap_mod._MAX_SNAPSHOT_BYTES + 1)
    fake_urlopen = MagicMock()
    fake_urlopen.return_value.__enter__.return_value = fake_resp

    ctx = RuntimeContext(
        settings=Settings(
            camera_allow_streamer=True,
            camera_stream_url="http://localhost:1985/api/frame.jpeg?src=p1s",
        )
    )
    args = Namespace(output=str(outpath), json=False, unique=False, allow_camera_streamer=True)
    run_ok = MagicMock(return_value=MagicMock(returncode=0, stdout="true"))

    with pytest.raises(NetworkError) as excinfo:
        snap_mod.cmd_snapshot(
            args,
            ctx=ctx,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_ok,
            urlopen=fake_urlopen,
        )

    assert excinfo.value.exit_code == EXIT_NETWORK_ERROR
    assert excinfo.value.failed_step == "snapshot"
    assert "50 MB safety limit" in str(excinfo.value)
    assert not outpath.exists()


# ---------------------------------------------------------------------------
# 2. jsonio: localhost schemeless, malformed IPv6, embedded URLs
# ---------------------------------------------------------------------------


def test_jsonio_localhost_malformed_ipv6_and_embedded_urls(capsys):
    from bambu_cli import utils
    from bambu_cli.jsonio import looks_like_schemeless_credential_url, redact_url_credentials

    at = "@"
    assert looks_like_schemeless_credential_url("user:pass" + at + "localhost/model.stl") is True
    assert redact_url_credentials("user:pass" + at + "localhost/model.stl") == "localhost/model.stl"

    bad_https = "https://user:pass" + at + "[invalid/a.stl"
    bad_scheme_rel = "//user:pass" + at + "[invalid/a.stl"
    assert redact_url_credentials(bad_https) == "https://[invalid/a.stl"
    assert redact_url_credentials(bad_scheme_rel) == "//[invalid/a.stl"

    embedded = (
        "Network error: https://user:secret"
        + at
        + "host.example/model.stl and //u2:p2"
        + at
        + "mirror.example/b.3mf failed"
    )
    assert (
        redact_url_credentials(embedded)
        == "Network error: https://host.example/model.stl and //mirror.example/b.3mf failed"
    )

    embedded_bad_ipv6 = "Failed fetching https://user:secret" + at + "[invalid/a.stl today"
    assert redact_url_credentials(embedded_bad_ipv6) == "Failed fetching https://[invalid/a.stl today"

    # Non-URL strings stay untouched.
    assert redact_url_credentials("/home/x" + at + "y") == "/home/x" + at + "y"
    assert redact_url_credentials("plain text without url") == "plain text without url"

    # emit_json never crashes on malformed IPv6 URLs.
    utils._JSON_EMITTED = False
    utils.emit_json({"error": bad_https})
    out = capsys.readouterr().out
    assert "user:pass" not in out
    assert "https://[invalid/a.stl" in out


# ---------------------------------------------------------------------------
# 3. ftps: close data socket if TLS wrap_socket raises
# ---------------------------------------------------------------------------


def test_implicit_ftps_ntransfercmd_closes_conn_on_wrap_error():
    import ftplib

    from bambu_cli.protocols.ftps import ImplicitFTPS

    ftp = ImplicitFTPS()
    ftp.host = "192.0.2.10"
    ftp._prot_p = True

    mock_ctrl_sock = MagicMock(spec=ssl.SSLSocket)
    mock_ctrl_sock.session = object()
    mock_ctrl_sock.context.wrap_socket.side_effect = ssl.SSLError("handshake failed")
    ftp.sock = mock_ctrl_sock

    raw_data_conn = MagicMock()
    with (
        patch.object(ftplib.FTP, "ntransfercmd", return_value=(raw_data_conn, 123)),
        pytest.raises(ssl.SSLError, match="handshake failed"),
    ):
        ftp.ntransfercmd("STOR /model/test.3mf")

    raw_data_conn.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# 4. mqtt_session: _closed guard & get_version connect-failure retries
# ---------------------------------------------------------------------------


def test_mqtt_session_closed_prevents_ensure_connected_and_get_version_retries():
    from bambu_cli.protocols.mqtt_session import MqttSession

    printer = MagicMock(serial="SN123")
    sleeps: list[float] = []
    clients: list[MagicMock] = []

    def make_client(_printer):
        client = MagicMock()
        clients.append(client)
        return client

    session = MqttSession(printer, client_factory=make_client, sleep=sleeps.append)
    # Force connection timeout on first attempt, then broker rc!=0 on second? No, test timeout retry first:
    session._connect = MagicMock()
    with patch.object(session, "_reset_client", wraps=session._reset_client) as reset_mock:
        res = session.get_version(timeout=0.001, retries=2)
    assert res is None
    assert sleeps == [1, 2]
    assert reset_mock.call_count >= 3

    # Broker refusal (_connect_failed=True) must stop immediately without sleeping.
    sleeps.clear()

    def refuse_connect(client):
        session._on_connect(client, None, None, 5)

    session._connect = refuse_connect
    with patch.object(session, "_reset_client", wraps=session._reset_client) as reset_mock:
        res = session.get_version(timeout=0.05, retries=2)
    assert res is None
    assert sleeps == []
    assert reset_mock.call_count >= 1

    # After close(), ensure_connected() returns False immediately without creating a client.
    before_count = len(clients)
    session.close()
    assert session._closed is True
    assert session.ensure_connected(0.05) is False
    assert len(clients) == before_count
    assert session.get_version(timeout=0.05, retries=2) is None
    assert sleeps == []


# ---------------------------------------------------------------------------
# 5. printer: last_size_verified set on upload remote-size recovery path
# ---------------------------------------------------------------------------


def test_upload_file_sets_last_size_verified_on_recovery_path(tmp_path):
    from bambu_cli.printer import BambuPrinter

    local_file = tmp_path / "model.3mf"
    payload = b"1234567890"
    local_file.write_bytes(payload)

    printer = BambuPrinter("192.0.2.1", "SN1", "CODE")
    ftp_transfer = MagicMock()
    ftp_transfer.storbinary.side_effect = ssl.SSLError("EOF without close-notify")
    ftp_probe = MagicMock()
    ftp_probe.size.return_value = len(payload)

    cm_transfer = MagicMock()
    cm_transfer.__enter__.return_value = ftp_transfer
    cm_probe = MagicMock()
    cm_probe.__enter__.return_value = ftp_probe

    with patch.object(printer, "get_ftp_client", side_effect=[cm_transfer, cm_probe]):
        ok = printer.upload_file(str(local_file), "/model/model.3mf", sleep=lambda _s: None)

    assert ok is True
    assert printer.last_size_verified is True


# ---------------------------------------------------------------------------
# 6. config: access_code_file non-empty string validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["", "   ", None, 123, ["path"]])
def test_load_access_code_rejects_invalid_access_code_file(bad_value):
    from bambu_cli.config import load_access_code
    from bambu_cli.context import RuntimeContext, Settings, set_current

    set_current(RuntimeContext(settings=Settings(), config={"access_code_file": bad_value}))
    with pytest.raises(ConfigError) as excinfo:
        load_access_code()
    assert excinfo.value.exit_code == EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# 7. context: _stream_host_port and Settings.from_config handle non-str camera_port
# ---------------------------------------------------------------------------


def test_context_handles_non_str_camera_port():
    from bambu_cli.config import DEFAULT_CAMERA_PORT
    from bambu_cli.context import Settings, _stream_host_port

    assert _stream_host_port(None) == "1985"
    assert _stream_host_port(1985) == "1985"

    for bad_port in (None, 1985, ["1985:1984"]):
        settings = Settings.from_config({"camera_port": bad_port})
        assert settings.camera_port == DEFAULT_CAMERA_PORT
        assert settings.camera_stream_url == "http://localhost:1985/api/frame.jpeg?src=p1s"


# ---------------------------------------------------------------------------
# 8. tlspin: non-str pins coerce and fail closed as malformed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_pin", [False, 0, 12345])
def test_tlspin_non_str_pins_fail_closed_as_malformed(bad_pin):
    from bambu_cli.tlspin import normalize_fingerprint, verify_cert_fingerprint

    der = b"fake-der"
    with pytest.raises(ConfigError, match="cert_fingerprint"):
        normalize_fingerprint(bad_pin)
    with pytest.raises(ConfigError, match="cert_fingerprint"):
        verify_cert_fingerprint(der, bad_pin)

    assert normalize_fingerprint(None) is None
    assert normalize_fingerprint("") is None


def test_tlspin_valid_pin_unchanged():
    from bambu_cli.tlspin import verify_cert_fingerprint

    der = b"fake-der"
    fp = hashlib.sha256(der).hexdigest()
    assert verify_cert_fingerprint(der, fp) == fp


# ---------------------------------------------------------------------------
# 9. doctor: no temp capabilities file leaked on abort & verbose vs redacted IP
# ---------------------------------------------------------------------------


def test_doctor_cleans_up_temp_capabilities_file_on_abort(tmp_path):
    from bambu_cli.commands import doctor

    created_paths: list[str] = []
    real_mkstemp = doctor.tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, dir=str(tmp_path), **kwargs)
        created_paths.append(path)
        return fd, path

    args = Namespace(output=None, json=False, verbose=False, network_timeout=1)
    with (
        patch.object(doctor.tempfile, "mkstemp", side_effect=tracking_mkstemp),
        patch.object(doctor, "load_config", side_effect=ConfigError("bad config")),
        pytest.raises(ConfigError),
    ):
        doctor.cmd_doctor(args)

    assert created_paths == []


def test_doctor_mqtt_fail_shows_ip_when_verbose(tmp_path):
    from bambu_cli.commands import doctor
    from bambu_cli.context import RuntimeContext, Settings

    printer = MagicMock()
    printer.status.return_value = None
    ctx = RuntimeContext(settings=Settings(printer_ip="10.0.0.99", mqtt_port=8883))
    ctx._printer = printer

    recorded: list[str] = []
    args = Namespace(output=str(tmp_path / "cap.json"), json=False, verbose=True, network_timeout=1)

    with (
        patch.object(doctor, "load_config", return_value=None),
        patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value=None),
        patch.object(doctor.logger, "error", side_effect=lambda msg, *a, **k: recorded.append(str(msg))),
        pytest.raises(NetworkError),
    ):
        doctor.cmd_doctor(args, ctx=ctx)

    assert any("Ensure printer at 10.0.0.99 is on" in m for m in recorded)


# ---------------------------------------------------------------------------
# 10. files: ctx.printer() and BambuError (ConfigError) propagation
# ---------------------------------------------------------------------------


def test_files_and_delete_use_ctx_printer_and_propagate_config_error():
    from bambu_cli.commands.files import cmd_delete, cmd_files

    fake_printer = MagicMock()
    fake_printer.list_files.return_value = ["/model/cube.3mf"]
    fake_printer.delete_file.return_value = True

    ctx = MagicMock()
    ctx.printer.return_value = fake_printer

    cmd_files(Namespace(json=False), ctx=ctx)
    fake_printer.list_files.assert_called_once_with("/model/")

    cmd_delete(Namespace(file="cube.3mf", confirm=True, json=False), ctx=ctx)
    fake_printer.delete_file.assert_called_once_with("/model/cube.3mf")

    failing_ctx = MagicMock()
    failing_ctx.printer.side_effect = ConfigError("missing access_code_file")

    with pytest.raises(ConfigError) as exc1:
        cmd_files(Namespace(json=False), ctx=failing_ctx)
    assert exc1.value.exit_code == EXIT_CONFIG_ERROR

    with pytest.raises(ConfigError) as exc2:
        cmd_delete(Namespace(file="cube.3mf", confirm=True, json=False), ctx=failing_ctx)
    assert exc2.value.exit_code == EXIT_CONFIG_ERROR
