"""Targeted coverage for A+ (W1): monitor/session/camera/ftps/snapshot/wizard edges.

These hit decision branches that the existing suites only graze. No production
test-awareness; collaborators are injected or patched at the call site.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.errors import BambuError
from bambu_cli.printer import BambuPrinter
from bambu_cli.protocols import mqtt as mqtt_mod
from bambu_cli.protocols import mqtt_monitor as monitor_mod
from bambu_cli.protocols.mqtt_session import MqttSession
from tests.test_mqtt_session import FakeBrokerClient, _held, _printer


# ---------------------------------------------------------------------------
# mqtt_monitor
# ---------------------------------------------------------------------------


def test_monitor_sim_human_path_logs_states():
    printer = BambuPrinter("1.2.3.4", "SN", "code", simulation_mode=True)
    args = Namespace(json=False)
    with (
        patch.object(mqtt_mod.time, "sleep"),
        patch.object(monitor_mod.logger, "info") as info,
    ):
        mqtt_mod.monitor_status(args, printer)
    texts = " ".join(str(c) for c in info.call_args_list)
    assert "PREPARE" in texts or "Simulated status" in texts
    assert "FINISH" in texts


def test_monitor_connect_rc_nonzero_stops():
    printer = BambuPrinter("1.2.3.4", "SN", "code")
    client = MagicMock()

    def loop_start():
        client.on_connect(client, {}, None, 5)

    client.loop_start.side_effect = loop_start
    args = Namespace(json=True)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch("sys.stdout.isatty", return_value=False),
    ):
        mqtt_mod.monitor_status(args, printer)
    client.loop_stop.assert_called()
    client.disconnect.assert_called()


def test_monitor_bad_json_and_generic_handler_error(capsys):
    printer = BambuPrinter("1.2.3.4", "SN", "code")
    client = MagicMock()

    def connect(*_a, **_k):
        client.on_connect(client, {}, None, 0)

    def loop_start():
        bad = MagicMock()
        bad.payload = b"not-json{"
        client.on_message(client, {}, bad)
        boom = MagicMock()
        boom.payload = json.dumps({"print": {"gcode_state": "RUNNING", "mc_percent": 1}}).encode()
        with patch.object(mqtt_mod, "_status_event", side_effect=RuntimeError("explode")):
            client.on_message(client, {}, boom)
        done = MagicMock()
        done.payload = json.dumps({"print": {"gcode_state": "STOP", "mc_percent": 0}}).encode()
        client.on_message(client, {}, done)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    args = Namespace(json=True)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch("sys.stdout.isatty", return_value=False),
    ):
        mqtt_mod.monitor_status(args, printer)
    out = capsys.readouterr().out
    assert "STOP" in out or "terminal" in out


def test_monitor_human_logger_and_keyboard_interrupt():
    printer = BambuPrinter("1.2.3.4", "SN", "code")
    client = MagicMock()
    userdata: dict = {}

    def connect(*_a, **_k):
        client.on_connect(client, userdata, None, 0)

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"gcode_state": "RUNNING", "mc_percent": 12}}).encode()
        client.on_message(client, userdata, msg)
        raise KeyboardInterrupt

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    client.loop_stop.side_effect = RuntimeError("stop")
    client.disconnect.side_effect = RuntimeError("disc")
    args = Namespace(json=False)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch("sys.stdout.isatty", return_value=False),
        patch.object(monitor_mod.logger, "info") as info,
    ):
        mqtt_mod.monitor_status(args, printer)
    texts = " ".join(str(c) for c in info.call_args_list)
    assert "RUNNING" in texts or "stopped by user" in texts.lower() or "🛑" in texts


def test_status_event_coerces_bad_ints():
    payload = mqtt_mod._status_event(
        {"gcode_state": "RUNNING", "mc_percent": "nope", "layer_num": None, "total_layer_num": "x"},
        "update",
    )
    assert payload["mc_percent"] == 0
    assert payload["layer_num"] == 0


# ---------------------------------------------------------------------------
# mqtt_session
# ---------------------------------------------------------------------------


def test_session_send_command_connect_fail_returns_false():
    def factory(_p):
        return FakeBrokerClient(connect_rc=4)

    printer = _printer()
    _held(printer, factory)
    assert mqtt_mod.send_command(printer, '{"print":{"command":"pause"}}', timeout=0.05, retries=0) is False
    printer.release_mqtt()


def test_session_send_command_timeout_and_oserror():
    class SilentClient(FakeBrokerClient):
        def publish(self, topic, payload, qos=0):
            self.publishes.append((topic, payload, qos))
            return MagicMock(rc=0)

    def factory(_p):
        return SilentClient()

    printer = _printer()
    _held(printer, factory, sleep=lambda _s: None)
    assert mqtt_mod.send_command(printer, '{"print":{"command":"pause"}}', timeout=0.01, retries=1) is False
    printer.release_mqtt()

    class BoomClient(FakeBrokerClient):
        def connect(self, host, port, keepalive=10):
            raise OSError("broker down")

    printer = _printer()
    _held(printer, lambda _p: BoomClient(), sleep=lambda _s: None)
    assert mqtt_mod.send_command(printer, "{}", timeout=0.01, retries=1) is False
    printer.release_mqtt()


def test_session_get_version_timeout_and_connect_fail():
    def factory(_p):
        return FakeBrokerClient(status_replies=[], version_reply=None)

    printer = _printer()
    _held(printer, factory, sleep=lambda _s: None)
    assert mqtt_mod.get_version(printer, timeout=0.01, retries=1) is None
    printer.release_mqtt()

    printer = _printer()
    _held(printer, lambda _p: FakeBrokerClient(connect_rc=3), sleep=lambda _s: None)
    assert mqtt_mod.get_version(printer, timeout=0.01, retries=0) is None
    printer.release_mqtt()


def test_session_get_version_oserror_retries_then_none():
    class Boom(FakeBrokerClient):
        def connect(self, host, port, keepalive=10):
            raise ssl.SSLError("handshake")

    printer = _printer()
    _held(printer, lambda _p: Boom(), sleep=lambda _s: None)
    assert mqtt_mod.get_version(printer, timeout=0.01, retries=1) is None
    printer.release_mqtt()


def test_session_reset_swallows_teardown_errors():
    class Nasty(FakeBrokerClient):
        def loop_stop(self):
            raise RuntimeError("loop")

        def disconnect(self):
            raise RuntimeError("disc")

    printer = _printer()
    session = _held(printer, lambda _p: Nasty())
    assert mqtt_mod.get_status(printer, timeout=1) is not None
    session.close()
    session.close()  # second close is a no-op


def test_session_subscribe_failure_still_connects():
    class NoSub(FakeBrokerClient):
        def subscribe(self, topic):
            raise RuntimeError("no sub")

    printer = _printer()
    _held(printer, lambda _p: NoSub())
    assert mqtt_mod.get_status(printer, timeout=1)["gcode_state"] == "IDLE"
    printer.release_mqtt()


def test_session_ignores_undecodable_and_non_dict_messages():
    printer = _printer()
    session = _held(printer, lambda _p: FakeBrokerClient())
    assert session.ensure_connected(1)
    session._on_message(session._client, None, SimpleNamespace(payload=b"\xff"))
    msg = MagicMock()
    msg.payload = b'"just-a-string"'
    session._on_message(session._client, None, msg)
    printer.release_mqtt()


def test_session_publish_pushall_and_issue_pending_noop_without_client():
    printer = _printer()
    session = MqttSession(printer, client_factory=lambda _p: FakeBrokerClient(), sleep=lambda _s: None)
    session._client = None
    session._live = True
    session._pending_payload = "{}"
    session._command_issued = False
    session._publish_pushall()
    session._issue_pending()
    session._make_client()  # default factory path is skipped; factory is set
    session.close()


def test_session_default_client_factory_used():
    printer = _printer()
    fake = FakeBrokerClient()
    session = MqttSession(printer, sleep=lambda _s: None)
    with patch.object(mqtt_mod, "create_mqtt_client", return_value=fake):
        built = session._make_client()
    assert built is fake


def test_session_ensure_connected_times_out():
    class NeverConnects(FakeBrokerClient):
        def connect(self, host, port, keepalive=10):
            self.connects += 1

    printer = _printer()
    session = _held(printer, lambda _p: NeverConnects())
    assert session.ensure_connected(0.01) is False
    printer.release_mqtt()


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------


def test_camera_missing_ip_or_code_returns_none():
    from bambu_cli.protocols.camera import _grab_camera_frame_direct

    assert _grab_camera_frame_direct(SimpleNamespace(ip="", access_code="x")) is None
    assert _grab_camera_frame_direct(SimpleNamespace(ip="1.2.3.4", access_code="")) is None


def test_camera_eof_and_zero_size_and_close_error():
    from bambu_cli.protocols.camera import _grab_camera_frame_direct

    mock_sock = MagicMock()
    mock_tls = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.return_value = mock_tls
    mock_tls.close.side_effect = OSError("already closed")
    mock_tls.recv.side_effect = [
        (0).to_bytes(4, "little") + b"\x00" * 12,
        (4).to_bytes(4, "little") + b"\x00" * 12,
        b"\xff\xd8\xff\xd9",
    ]
    printer = SimpleNamespace(ip="1.2.3.4", access_code="code", insecure_tls=True, cert_fingerprint=None)
    frame = _grab_camera_frame_direct(
        printer,
        create_connection=MagicMock(return_value=mock_sock),
        ssl_context_factory=MagicMock(return_value=mock_ctx),
    )
    assert frame == b"\xff\xd8\xff\xd9"

    mock_tls2 = MagicMock()
    mock_ctx2 = MagicMock()
    mock_ctx2.wrap_socket.return_value = mock_tls2
    mock_tls2.recv.return_value = b""
    printer2 = SimpleNamespace(ip="1.2.3.4", access_code="code", insecure_tls=True, cert_fingerprint=None)
    with pytest.raises(EOFError):
        _grab_camera_frame_direct(
            printer2,
            create_connection=MagicMock(return_value=MagicMock()),
            ssl_context_factory=MagicMock(return_value=mock_ctx2),
        )


# ---------------------------------------------------------------------------
# ftps
# ---------------------------------------------------------------------------


def test_sim_ftp_size_missing_and_delete():
    from bambu_cli.protocols.ftps import _SIM_FTP_FILES, _SimFtp
    import ftplib

    ftp = _SimFtp()
    with pytest.raises(ftplib.error_perm):
        ftp.size("/cache/nope.3mf")
    ftp.delete("simulated_file.3mf")
    assert "simulated_file.3mf" not in _SIM_FTP_FILES
    _SIM_FTP_FILES["simulated_file.3mf"] = 1000


def test_implicit_ftps_connect_cleanup_on_wrap_failure():
    from bambu_cli.protocols.ftps import ImplicitFTPS

    mock_sock = MagicMock()
    mock_sock.family = 2
    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.side_effect = ssl.SSLError("boom")
    ftp = ImplicitFTPS()
    ftp.printer = SimpleNamespace(cert_fingerprint=None, insecure_tls=False)
    with pytest.raises(ssl.SSLError):
        ftp.connect(
            "192.168.1.1",
            990,
            5,
            create_connection=MagicMock(return_value=mock_sock),
            ssl_context_cls=MagicMock(return_value=mock_ctx),
        )
    mock_sock.close.assert_called()


def test_implicit_ftps_data_channel_pin_mismatch_closes():
    from bambu_cli.protocols.ftps import ImplicitFTPS
    from bambu_cli.errors import BambuError

    ftp = ImplicitFTPS()
    ftp.host = "192.168.1.1"
    ftp.printer = SimpleNamespace(cert_fingerprint="ab" * 32)
    ftp._prot_p = True
    control = MagicMock(spec=ssl.SSLSocket)
    ftp.sock = control
    data = MagicMock()
    ctx = MagicMock()
    wrapped = MagicMock()
    wrapped.getpeercert.return_value = b"\x00peer"
    ctx.wrap_socket.return_value = wrapped
    control.context = ctx
    control.session = object()
    with (
        patch("ftplib.FTP.ntransfercmd", return_value=(data, 10)),
        patch("bambu_cli.tlspin.verify_cert_fingerprint", side_effect=ssl.SSLError("pin")),
        pytest.raises(ssl.SSLError),
    ):
        ftp.ntransfercmd("STOR x")
    wrapped.close.assert_called()


def test_create_raw_ftp_sim_and_real_login_failure():
    from bambu_cli.protocols.ftps import _create_raw_ftp

    sim = _create_raw_ftp(SimpleNamespace(simulation_mode=True, ip="1.2.3.4", access_code="x"))
    assert hasattr(sim, "nlst")

    ftp = MagicMock()
    ftp.connect.side_effect = OSError("refused")
    ftp.close.side_effect = OSError("already")
    with (
        patch("bambu_cli.protocols.ftps._resolve_ip", return_value="1.2.3.4"),
        patch("bambu_cli.protocols.ftps.ImplicitFTPS", return_value=ftp),
        pytest.raises(OSError),
    ):
        _create_raw_ftp(SimpleNamespace(simulation_mode=False, ip="1.2.3.4", access_code="x"), timeout=1)


# ---------------------------------------------------------------------------
# snapshot helpers + command edges
# ---------------------------------------------------------------------------


def test_snapshot_helpers_and_warn_bind():
    from bambu_cli.commands import snapshot as snap

    assert snap._utc_stamp()
    assert snap._is_valid_port_number("nope") is False
    assert snap._camera_port_is_valid("") is False
    assert snap._camera_bind_host("1984") == ""
    ctx = SimpleNamespace(settings=SimpleNamespace(camera_container_name="bambu_camera"))
    snap._warn_if_running_bind_exposed(ctx, lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))
    bad = MagicMock(returncode=1, stdout="")
    snap._warn_if_running_bind_exposed(ctx, lambda *_a, **_k: bad)
    ok = MagicMock(returncode=0, stdout='{"1984/tcp":[{"HostIp":"0.0.0.0"}]}')
    with patch.object(snap.logger, "warning") as warn:
        snap._warn_if_running_bind_exposed(ctx, lambda *_a, **_k: ok)
    warn.assert_called()


def test_snapshot_direct_write_oserror(tmp_path):
    from bambu_cli.commands.snapshot import cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings

    args = Namespace(output=str(tmp_path / "out.jpg"), json=False, unique=False)
    ctx = RuntimeContext(settings=Settings(), simulation=True)
    with (
        patch("bambu_cli.commands.snapshot._write_snapshot_atomic", side_effect=OSError("disk")),
        pytest.raises(BambuError),
    ):
        cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: b"\xff\xd8\xff\xd9")


def test_snapshot_docker_unreachable_and_run_fail(tmp_path):
    from bambu_cli.commands.snapshot import cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings

    args = Namespace(output=str(tmp_path / "out.jpg"), json=False, unique=False, allow_camera_streamer=True)
    ctx = RuntimeContext(settings=Settings(camera_allow_streamer=True), simulation=True)
    with pytest.raises(BambuError) as missing:
        cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: None, which=lambda _n: None)
    assert "Docker" in str(missing.value) or missing.value.exit_code

    def run_fail(cmd, **_k):
        if cmd[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="")
        if cmd[:2] == ["docker", "rm"]:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret 1.2.3.4 boom")

    ctx.settings.printer_ip = "1.2.3.4"
    with pytest.raises(BambuError):
        cmd_snapshot(
            args,
            ctx=ctx,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_fail,
            access_code_loader=lambda: "secret",
        )

    def inspect_raises(*_a, **_k):
        raise FileNotFoundError("docker")

    with pytest.raises(BambuError):
        cmd_snapshot(
            args,
            ctx=ctx,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=inspect_raises,
        )


def test_snapshot_unique_with_user_output_and_error_paths(tmp_path):
    from bambu_cli.commands.snapshot import cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings
    from bambu_cli.protocols.camera import _CameraPinMismatch

    out = tmp_path / "cam.jpg"
    args = Namespace(output=str(out), json=False, unique=True)
    ctx = RuntimeContext(settings=Settings(), simulation=True)
    cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: b"\xff\xd8\xff\xd9", now=None)
    assert list(tmp_path.glob("cam_*.jpg"))

    args2 = Namespace(output=str(tmp_path / "pin.jpg"), json=False, unique=False)
    with pytest.raises(BambuError):
        cmd_snapshot(
            args2,
            ctx=ctx,
            grab_frame=lambda _p: (_ for _ in ()).throw(_CameraPinMismatch("mismatch")),
        )

    ctx.settings.cert_fingerprint = "ab" * 32
    ctx.settings.insecure_tls = False
    ctx._printer = SimpleNamespace(insecure_tls=False, cert_fingerprint="ab" * 32)

    def grab_ssl(_p):
        raise ssl.SSLError("pinned handshake")

    with pytest.raises(BambuError):
        cmd_snapshot(args2, ctx=ctx, grab_frame=grab_ssl)

    args3 = Namespace(
        output=str(tmp_path / "stream.jpg"),
        json=False,
        unique=False,
        allow_camera_streamer=True,
    )
    ctx3 = RuntimeContext(settings=Settings(camera_allow_streamer=True), simulation=True)

    def run_ok_then_urlerror(cmd, **_k):
        return SimpleNamespace(returncode=0, stdout="true\n")

    with pytest.raises(BambuError):
        cmd_snapshot(
            args3,
            ctx=ctx3,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_ok_then_urlerror,
            urlopen=lambda *_a, **_k: (_ for _ in ()).throw(__import__("urllib.error").URLError("down")),
        )

    def run_start_fail(cmd, **_k):
        if cmd[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="false")
        if cmd[:2] == ["docker", "rm"]:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret 9.9.9.9 boom")

    ctx3.settings.printer_ip = "9.9.9.9"
    with pytest.raises(BambuError):
        cmd_snapshot(
            args3,
            ctx=ctx3,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_start_fail,
            access_code_loader=lambda: "secret",
        )

    def run_then_oserror(cmd, **_k):
        return SimpleNamespace(returncode=0, stdout="true\n")

    class BoomResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise OSError("disk full")

    with pytest.raises(BambuError):
        cmd_snapshot(
            args3,
            ctx=ctx3,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=run_then_oserror,
            urlopen=lambda *_a, **_k: BoomResp(),
        )


def test_snapshot_unique_uses_clock(tmp_path):
    from bambu_cli.commands.snapshot import cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings

    args = Namespace(output=None, json=False, unique=True)
    ctx = RuntimeContext(settings=Settings(), simulation=True)
    out_dir = tmp_path
    with patch("bambu_cli.commands.snapshot._expand_path", side_effect=lambda p: str(out_dir / os.path.basename(p))):
        cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: b"\xff\xd8\xff\xd9", now=None)
    saved = list(out_dir.glob("printer_snapshot_*.jpg"))
    assert saved


# ---------------------------------------------------------------------------
# wizard leftover error branches
# ---------------------------------------------------------------------------


def test_wizard_ipv6_and_bad_raw_address():
    from bambu_cli.setup_cmd import wizard as wizard_mod

    info = MagicMock()
    info.parsed_addresses = None
    info.addresses = [socket.inet_pton(socket.AF_INET6, "::1")]
    assert ":" in wizard_mod._service_info_address(info)

    info2 = MagicMock()
    info2.parsed_addresses = None
    info2.addresses = [b"xx"]
    with pytest.raises(ValueError):
        wizard_mod._service_info_address(info2)


def test_wizard_parse_identity_model_only():
    from bambu_cli.setup_cmd import wizard as wizard_mod

    serial, model = wizard_mod._parse_mdns_printer_identity("BBLP-P1S._bblp._tcp.local.")
    assert model == "P1S"
    assert serial


def test_wizard_noninteractive_empty_env_and_missing(monkeypatch):
    from bambu_cli.setup_cmd import wizard as wizard_mod

    monkeypatch.delenv("EMPTY_CODE", raising=False)
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SN1",
        access_code=None,
        access_code_env="EMPTY_CODE",
        access_code_file=None,
        json=True,
    )
    with pytest.raises(BambuError):
        wizard_mod._cmd_setup_noninteractive(args)

    args2 = Namespace(
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_env=None,
        access_code_file=None,
        json=True,
    )
    with pytest.raises(BambuError) as exc:
        wizard_mod._cmd_setup_noninteractive(args2)
    assert "missing" in str(exc.value).lower() or exc.value.exit_code


def test_wizard_noninteractive_file_errors(tmp_path, monkeypatch):
    from bambu_cli.setup_cmd import wizard as wizard_mod

    missing = tmp_path / "nope"
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SNREAL123456",
        access_code=None,
        access_code_env=None,
        access_code_file=str(missing),
        json=True,
        model="P1S",
        nozzle="0.4",
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        force=False,
    )
    with pytest.raises(BambuError):
        wizard_mod._cmd_setup_noninteractive(args)

    bad = tmp_path / "code"
    bad.write_text("ACCESS_CODE\n", encoding="utf-8")
    args.access_code_file = str(bad)
    with pytest.raises(BambuError):
        wizard_mod._cmd_setup_noninteractive(args)

    cfg = tmp_path / "config.json"
    code = tmp_path / "okcode"
    code.write_text("12345678", encoding="utf-8")
    args.access_code = "12345678"
    args.access_code_file = str(code)
    args.force = False
    with (
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_write_setup_config", side_effect=OSError("rofs")),
        pytest.raises(BambuError),
    ):
        wizard_mod._cmd_setup_noninteractive(args)


# ---------------------------------------------------------------------------
# facade freeze (Architecture A+)
# ---------------------------------------------------------------------------


def test_snapshot_dash_path_and_atomic_unlink(tmp_path):
    from bambu_cli.commands.snapshot import _write_snapshot_atomic, cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings

    args = Namespace(output="-evil.jpg", json=False, unique=False)
    ctx = RuntimeContext(settings=Settings(), simulation=True)
    with pytest.raises(BambuError):
        cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: b"x")

    target = tmp_path / "x.jpg"
    with (
        patch("os.replace", side_effect=OSError("busy")),
        patch("os.unlink", side_effect=OSError("gone")),
        pytest.raises(OSError),
    ):
        _write_snapshot_atomic(str(target), b"data")

    def inspect_then_missing(cmd, **_k):
        if cmd[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="")
        raise FileNotFoundError("docker vanished")

    args3 = Namespace(output=str(tmp_path / "s.jpg"), json=False, unique=False, allow_camera_streamer=True)
    ctx3 = RuntimeContext(settings=Settings(camera_allow_streamer=True), simulation=True)
    with pytest.raises(BambuError):
        cmd_snapshot(
            args3,
            ctx=ctx3,
            grab_frame=lambda _p: None,
            which=lambda _n: "/usr/bin/docker",
            subprocess_run=inspect_then_missing,
            access_code_loader=lambda: "x",
        )


def test_slice_step_fail_and_dash_outdir(tmp_path):
    from bambu_cli.slicer.cmd import cmd_slice

    step = tmp_path / "part.step"
    step.write_text("solid\n", encoding="utf-8")
    args = Namespace(
        file=str(step),
        list_settings=False,
        json=True,
        output=None,
        quality="standard",
        copies=1,
    )
    with (
        patch("bambu_cli.slicer.cmd._convert_step_to_stl", return_value=(None, False)),
        pytest.raises(BambuError),
    ):
        cmd_slice(args)

    stl = tmp_path / "cube.stl"
    stl.write_text("solid\n", encoding="utf-8")
    args2 = Namespace(
        file=str(stl),
        list_settings=False,
        json=True,
        output="-nope",
        quality="standard",
        copies=1,
    )
    with pytest.raises(BambuError):
        cmd_slice(args2)


def test_download_html_page_has_no_model_link(tmp_path):
    from bambu_cli.download.downloader import _cmd_download

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            return b"<html><body>no files</body></html>"

        def getheader(self, name):
            return "text/html" if name.lower() == "content-type" else None

        def geturl(self):
            return None

    class Opener:
        def open(self, *a, **k):
            return Resp()

    args = Namespace(
        url="https://example.com/gallery",
        output=str(tmp_path),
        json=True,
        name=None,
        max_download_mb=10,
        progress=False,
    )
    with (
        patch("bambu_cli.download.downloader._validate_download_url_or_exit"),
        patch("bambu_cli.download.downloader._reject_unsupported_download_extension"),
        pytest.raises(BambuError),
    ):
        _cmd_download(args, opener_factory=lambda: Opener(), resolve_printables=lambda u: (u, None))


def test_mqtt_facade_all_is_frozen():
    """New public names must be added to this freeze deliberately, not by accident."""
    expected = {
        "TERMINAL_GCODE_STATES",
        "PinningSSLContext",
        "_REQUIRED_STATUS_KEYS",
        "_SimMqttClient",
        "_mqtt_connect",
        "_printer_error_hex",
        "_require_mqtt",
        "_status_event",
        "MqttSession",
        "create_mqtt_client",
        "execute_print_command",
        "get_status",
        "get_version",
        "monitor_status",
        "mqtt",
        "pinning_ssl_context",
        "probe_cert_fingerprint",
        "send_command",
        "status_is_complete",
        "time",
    }
    assert set(mqtt_mod.__all__) == expected


# ---------------------------------------------------------------------------
# download / slice / preflight leftovers
# ---------------------------------------------------------------------------


def test_response_url_helpers():
    from bambu_cli.download.downloader import _response_url

    assert _response_url(object()) is None
    assert _response_url(SimpleNamespace(geturl="not-callable")) is None

    class Boom:
        def geturl(self):
            raise RuntimeError("x")

    assert _response_url(Boom()) is None
    assert _response_url(SimpleNamespace(geturl=lambda: 123)) is None
    assert _response_url(SimpleNamespace(geturl=lambda: "https://example.com/a.stl")) == "https://example.com/a.stl"


def test_download_rejects_dash_outdir():
    from bambu_cli.download.downloader import _cmd_download

    args = Namespace(url="https://example.com/a.stl", output="-sneaky", json=True, name=None, max_download_mb=10)
    with pytest.raises(BambuError):
        _cmd_download(args)


def test_slice_validate_edges(tmp_path):
    from bambu_cli.slicer.cmd import cmd_slice

    with pytest.raises(BambuError):
        cmd_slice(Namespace(file=None, list_settings=False, json=True))
    with pytest.raises(BambuError):
        cmd_slice(Namespace(file="-evil.stl", list_settings=False, json=True))
    missing = tmp_path / "nope.stl"
    with pytest.raises(BambuError):
        cmd_slice(Namespace(file=str(missing), list_settings=False, json=True))
    directory = tmp_path / "modeldir"
    directory.mkdir()
    with pytest.raises(BambuError):
        cmd_slice(Namespace(file=str(directory), list_settings=False, json=True))


def test_preflight_module_and_perms(tmp_path, monkeypatch):
    from bambu_cli.setup_cmd import preflight as pf

    monkeypatch.setattr("importlib.util.find_spec", lambda _n: (_ for _ in ()).throw(ValueError("x")))
    assert pf._module_available("definitely_missing_mod") is False
    assert pf._file_permission_check("", "secret") is None
    secret = tmp_path / "code"
    secret.write_text("x", encoding="utf-8")
    secret.chmod(0o644)
    if sys.platform == "win32":
        # POSIX mode bits do not apply on Windows; the check is a no-op there
        # (see the "Windows secret ACLs" residual in SECURITY.md).
        assert pf._file_permission_check(str(secret), "access_code_file") is None
        return
    check = pf._file_permission_check(str(secret), "access_code_file")
    assert check is not None
    assert check["status"] == "warning"
    secret.chmod(0o600)
    ok = pf._file_permission_check(str(secret), "access_code_file")
    assert ok is not None
    assert ok["status"] == "ok"
    gone = tmp_path / "missing"
    assert pf._file_permission_check(str(gone), "access_code_file") is None


def test_snapshot_parent_dir_error(tmp_path):
    from bambu_cli.commands.snapshot import cmd_snapshot
    from bambu_cli.context import RuntimeContext, Settings

    args = Namespace(output=str(tmp_path / "nope" / "out.jpg"), json=False, unique=False)
    ctx = RuntimeContext(settings=Settings(), simulation=True)
    with (
        patch("bambu_cli.commands.snapshot._ensure_parent_dir", side_effect=BambuError("no dir", exit_code=3)),
        pytest.raises(BambuError),
    ):
        cmd_snapshot(args, ctx=ctx, grab_frame=lambda _p: b"x")


def test_monitor_progress_stop_raises():
    printer = BambuPrinter("1.2.3.4", "SN", "code")
    client = MagicMock()

    class Progress:
        def update(self, *a, **k):
            return None

        def stop(self):
            raise RuntimeError("x")

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"gcode_state": "FINISH", "mc_percent": 100}}).encode()
        client.on_message(client, {"progress": Progress(), "task_id": 1}, msg)

    client.loop_start.side_effect = loop_start
    args = Namespace(json=False)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch("sys.stdout.isatty", return_value=False),
    ):
        mqtt_mod.monitor_status(args, printer)
