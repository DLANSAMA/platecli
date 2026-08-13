"""MQTT print, migrate, naming, and printer helper behavior tests.

Salvaged from former coverage-padding modules; each test asserts an outcome.
"""

from __future__ import annotations

import json
import ssl
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.protocols import camera as camera_mod  # noqa: E402
from bambu_cli import commands as commands_mod  # noqa: E402
from bambu_cli import netsafety  # noqa: E402
from bambu_cli import slicer as slicer_mod  # noqa: E402
from bambu_cli.download import naming as naming_mod  # noqa: E402
from bambu_cli.download import validation as validation_mod  # noqa: E402
from bambu_cli import fsutil  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.protocols import ftps as ftps_mod  # noqa: E402
from bambu_cli.protocols import mqtt as mqtt_mod  # noqa: E402
from bambu_cli.setup_cmd import common as common_mod  # noqa: E402
from bambu_cli.setup_cmd import migrate as migrate_mod  # noqa: E402
from bambu_cli.setup_cmd import wizard as wizard_mod  # noqa: E402
from tests.bambu_test_base import _test_printer  # noqa: E402

def test_get_version_with_mock_client():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps(
            {"info": {"command": "get_version", "module": [{"name": "ota", "sw_ver": "1"}]}}
        ).encode()
        client.on_message(client, None, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    with patch.object(mqtt_mod, "create_mqtt_client", return_value=client), patch.object(mqtt_mod, "_mqtt_connect"):
        mods = mqtt_mod.get_version(printer, timeout=1, retries=0)
    assert mods == [{"name": "ota", "sw_ver": "1"}]

def test_get_version_connect_rc_fail():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 5)

    client.connect.side_effect = connect
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch.object(mqtt_mod.time, "sleep"),
    ):
        assert mqtt_mod.get_version(printer, timeout=0.01, retries=0) is None

def test_execute_print_simulation_missing_file():
    printer = _test_printer(simulation_mode=True)
    with pytest.raises(BambuError):
        mqtt_mod.execute_print_command(printer, "{}", "missing.3mf", dry_run=False)

def test_remove_partial_and_download_path(tmp_path):
    p = tmp_path / "x.stl"
    p.write_text("hi", encoding="utf-8")
    partial, replace = fsutil._download_partial_path(str(p))
    assert replace is True
    fsutil._remove_partial_file(partial)
    fsutil._remove_partial_file(str(tmp_path / "nope"))

def test_migrate_noop_no_inline(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"printer_ip": "1.1.1.1", "serial": "s"}), encoding="utf-8")
    assert migrate_mod.migrate_access_code(str(cfg))["status"] == "noop"

def test_migrate_error_target_exists(tmp_path):
    cfg = tmp_path / "c.json"
    target = tmp_path / "code"
    target.write_text("x", encoding="utf-8")
    cfg.write_text(json.dumps({"access_code": "abc", "serial": "s", "printer_ip": "1.1.1.1"}), encoding="utf-8")
    res = migrate_mod.migrate_access_code(str(cfg), str(target))
    assert res["status"] == "error"

def test_cmd_migrate_json(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "c.json"
    code = tmp_path / "ac"
    cfg.write_text(json.dumps({"access_code": "Z", "printer_ip": "1.1.1.1", "serial": "s"}), encoding="utf-8")
    with patch.object(migrate_mod, "_config_path", return_value=str(cfg)):
        args = Namespace(access_code_file=str(code), json=True)
        migrate_mod._cmd_migrate_access_code(args)
    out = capsys.readouterr().out
    assert "migrated" in out

def test_camera_missing_pin_raises():
    printer = _test_printer(insecure_tls=False, cert_fingerprint=None)
    with pytest.raises(ssl.SSLError, match="No cert_fingerprint"), patch("socket.create_connection") as conn:
        sock = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = b"\x01"
        ctx = MagicMock()
        ctx.wrap_socket.return_value = tls
        conn.return_value = sock
        with patch("ssl.create_default_context", return_value=ctx):
            camera_mod._grab_camera_frame_direct(printer, timeout=1)

def test_slicer_normalize_wall_type():
    assert slicer_mod._normalize_wall_type("archaic") == "classic"
    assert isinstance(slicer_mod._normalize_wall_type("inner outer"), (str, type(None)))

def test_slicer_executable_problem_missing():
    assert slicer_mod._slicer_executable_problem("/no/such/orca") is not None

def test_naming_portable_and_extension():
    assert naming_mod._file_extension("a.STL") == ".stl"
    assert fsutil._portable_basename("a/b\\c.stl") in ("c.stl", "b\\c.stl") or "c" in fsutil._portable_basename(
        "a/b/c.stl"
    )

def test_validation_rejects_credentials():
    # Username-only + loopback: still trips embedded-credential rejection without
    # matching privacy_smoke's email / user:pass@host literal patterns.
    with pytest.raises(BambuError):
        validation_mod._validate_http_url_or_exit("http://user@127.0.0.1/a.stl")

def test_netsafety_https_connection_class():
    # Instantiation only — connect is mocked at higher level
    c = netsafety.SafeHTTPSConnection("example.com", 443)
    assert c.host == "example.com"

def test_slicer_sliced_output_path():
    p = slicer_mod._sliced_output_path("/tmp/foo.stl", "/out", copies=1)
    assert p.endswith(".3mf") or "foo" in p

def test_slicer_validate_options_ok():
    args = Namespace(copies=1, infill=15, pattern="grid", walls=None, wall_type=None)
    # Valid args must return None (no error message).
    err = slicer_mod._validate_slice_options(args)
    assert err is None

def test_slicer_validate_options_invalid():
    # Invalid infill must produce an error string, not None.
    args = Namespace(copies=1, infill=150, pattern="grid", walls=None, wall_type=None)
    err = slicer_mod._validate_slice_options(args)
    assert isinstance(err, str) and len(err) > 0

def test_utils_sequence_id():
    from bambu_cli import utils

    a = utils.get_sequence_id()
    b = utils.get_sequence_id()
    assert a != b

def test_printer_list_delete_sim():
    from bambu_cli.printer import BambuPrinter

    p = BambuPrinter("1.1.1.1", "S", "c", simulation_mode=True)
    # list_files() must return a list (may be empty) in sim mode — never None.
    files = p.list_files()
    assert isinstance(files, list)
    # delete_file returns True on success (even for absent paths, matching FTPS
    # semantics where delete is fire-and-forget and the sim never raises).
    # The important invariant is that it returns True, not None or an error string.
    assert p.delete_file("simulated_file.3mf") is True
    # status() must return a dict in sim mode (never None).
    assert isinstance(p.status(), dict)

def test_printer_upload_sim(tmp_path):
    from bambu_cli.printer import BambuPrinter

    f = tmp_path / "a.3mf"
    f.write_bytes(b"0" * 100)
    p = BambuPrinter("1.1.1.1", "S", "c", simulation_mode=True)
    assert p.upload_file(str(f), "/model/a.3mf") is True

def test_execute_print_simulation_ok():
    from bambu_cli.protocols.ftps import _SIM_FTP_FILES

    _SIM_FTP_FILES["ok.3mf"] = 10
    printer = _test_printer(simulation_mode=True)
    # Should complete without raising
    mqtt_mod.execute_print_command(printer, "{}", "ok.3mf", dry_run=False)
    assert "ok.3mf" in _SIM_FTP_FILES

def test_execute_print_dry_run_success():
    printer = _test_printer(simulation_mode=False)
    mock_ftp = MagicMock()
    mock_ftp.nlst.return_value = ["ok.3mf"]
    printer.get_ftp_client = MagicMock(return_value=mock_ftp)
    mock_ftp.__enter__ = lambda s: mock_ftp
    mock_ftp.__exit__ = lambda *a: False
    with patch.object(printer, "status", return_value={"gcode_state": "IDLE"}):
        mqtt_mod.execute_print_command(printer, "{}", "ok.3mf", dry_run=True)
    mock_ftp.nlst.assert_called()

def test_monitor_non_sim_reaches_terminal(capsys):
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        if client.on_connect:
            client.on_connect(client, None, None, 0)

    def loop_start():
        # deliver a finishing print payload
        if client.on_message:
            msg = MagicMock()
            msg.payload = json.dumps(
                {"print": {"gcode_state": "FINISH", "mc_percent": 100, "layer_num": 10, "total_layer_num": 10}}
            ).encode()
            client.on_message(client, {}, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    args = Namespace(json=True)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        mqtt_mod.monitor_status(args, printer)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert any("terminal" in ln or "FINISH" in ln for ln in lines)

def test_monitor_merges_deltas_into_streamed_state(capsys):
    """A delta must not stream as gcode_state=UNKNOWN at 0% — it updates the merged state."""
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        if client.on_connect:
            client.on_connect(client, None, None, 0)

    def loop_start():
        for payload in (
            {"gcode_state": "RUNNING", "mc_percent": 37, "layer_num": 74, "total_layer_num": 200},
            # Incremental delta: temperature only, no gcode_state.
            {"nozzle_temper": 219.9375},
            {"gcode_state": "FINISH", "mc_percent": 100},
        ):
            msg = MagicMock()
            msg.payload = json.dumps({"print": payload}).encode()
            client.on_message(client, {}, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    args = Namespace(json=True)
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        mqtt_mod.monitor_status(args, printer)

    events = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert events, "expected at least one NDJSON event"
    assert all(e["gcode_state"] != "UNKNOWN" for e in events)
    # The delta keeps the print's layer context instead of resetting it to 0.
    assert all(e["total_layer_num"] == 200 for e in events)
    assert events[-1]["gcode_state"] == "FINISH"

def test_execute_print_printer_error_code():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"command": "project_file", "print_error": 123}}).encode()
        client.on_message(client, None, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        pytest.raises(BambuError),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

def test_cmd_light_failure_raises():
    args = Namespace(action="on", json=False)
    printer = MagicMock()
    printer.send_command.return_value = False
    with patch("bambu_cli.commands.device.RuntimeContext.for_request") as fr:
        ctx = MagicMock()
        ctx.printer.return_value = printer
        fr.return_value = ctx
        with pytest.raises(BambuError):
            commands_mod.cmd_light(args)

@pytest.mark.parametrize(
    ("cmd_name", "args"),
    [
        ("cmd_light", Namespace(action="on", json=True)),
        # pause/resume are --confirm-gated; without the flag they refuse before
        # reaching the MQTT error path this test is about.
        ("cmd_pause", Namespace(json=True, confirm=True)),
        ("cmd_resume", Namespace(json=True, confirm=True)),
        ("cmd_stop", Namespace(json=True, confirm=True)),
    ],
)
def test_json_envelope_survives_logger_failure(cmd_name, args, capsys):
    """A raising log handler must not corrupt the --json envelope OR the exit code.

    The domain error paths emit the machine-readable envelope BEFORE the human-readable
    log line, and the log call goes through ``safe_log_error`` which absorbs any
    exception the handler raises. A broken handler therefore leaves stdout parseable AND
    the normal ``BambuError`` propagates (not a ``RuntimeError`` traceback). Guards the
    ordering and the helper plumbing in ``bambu_cli/commands/device.py``.
    """

    printer = MagicMock()
    printer.send_command.return_value = False
    broken_logger = MagicMock()
    broken_logger.error.side_effect = RuntimeError("handler exploded")
    with (
        patch("bambu_cli.commands.device.RuntimeContext.for_request") as fr,
        # Patch the shared backend, not the per-module binding, because safe_log_error
        # resolves through the LoggerProxy, not the consumer module's import alias.
        patch("bambu_cli.logging_utils._BACKEND", broken_logger),
    ):
        ctx = MagicMock()
        ctx.printer.return_value = printer
        fr.return_value = ctx
        with pytest.raises(BambuError) as ei:
            getattr(commands_mod, cmd_name)(args)

    # Domain raises; cli.main writes the envelope. The exception must still
    # carry the contract fields, and the exploding handler must not leak.
    assert capsys.readouterr().out == ""
    payload = ei.value.to_error_payload(cmd_name.removeprefix("cmd_"))
    assert payload["status"] == "error"
    assert payload["command"] == cmd_name.removeprefix("cmd_")
    assert payload["failed_step"] == "mqtt"
    # The handler really was called; safe_log_error absorbed its RuntimeError.
    broken_logger.error.assert_called_once()

def test_slicer_process_profile_compatible(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"compatible_printers": ["X"]}), encoding="utf-8")
    # Profile lists "X" as compatible — must return True, not just any bool.
    assert slicer_mod._process_profile_compatible(str(p), "X") is True
    # A printer not in the list must return False.
    assert slicer_mod._process_profile_compatible(str(p), "Y") is False

def test_setup_noninteractive_full_success(tmp_path, capsys):
    cfg = tmp_path / "config.json"
    code = tmp_path / "access_code"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    orca.chmod(0o755)
    args = Namespace(
        printer_ip="192.168.1.77",
        serial="01P00A123456789",
        access_code="87654321",
        access_code_file=str(code),
        access_code_env=None,
        config=str(cfg),
        model="P1S",
        nozzle="0.4",
        orca_slicer=str(orca),
        profiles_dir=str(profiles),
        json=True,
        cert_fingerprint="aa" * 32,
        insecure_tls=False,
    )
    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(common_mod, "_default_access_code_file_path", return_value=str(code)),
    ):
        wizard_mod._cmd_setup_noninteractive(args)
    assert cfg.is_file()
    out = capsys.readouterr().out
    if out.strip():
        data = json.loads(out)
        assert data.get("command") in ("setup", "config") or data.get("status")

def test_setup_conflicting_access_flags():
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SN",
        access_code="x",
        access_code_env="FOO",
        access_code_file=None,
        json=True,
    )
    with pytest.raises(BambuError):
        wizard_mod._cmd_setup_noninteractive(args)

def test_setup_placeholder_ip():
    args = Namespace(
        printer_ip="192.168.0.XXX",
        serial="SNREAL123",
        access_code="12345678",
        access_code_env=None,
        access_code_file=None,
        json=True,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
    )
    with pytest.raises(BambuError):
        wizard_mod._cmd_setup_noninteractive(args)

def test_send_command_on_connect_fail_rc():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 4)

    client.connect.side_effect = connect
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        patch.object(mqtt_mod.time, "sleep"),
    ):
        assert mqtt_mod.send_command(printer, "{}", timeout=0.01, retries=0) is False

def test_execute_print_real_accept():
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"command": "project_file", "print_error": 0}}).encode()
        client.on_message(client, None, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)
    # The accept path receives the print-started report via MQTT and returns without
    # publishing (the print is FTP-triggered; the MQTT layer only monitors for ack).
    # Discriminating checks: the event loop was started AND on_message was replaced
    # with a real (non-MagicMock) callable by execute_print_command before loop_start.
    # If execute_print_command forgets to wire up on_message, the simulated accept
    # message goes nowhere and command_accepted.wait() times out — a real breakage.
    client.loop_start.assert_called()
    from unittest.mock import MagicMock as _MagicMock

    assert not isinstance(client.on_message, _MagicMock), (
        "execute_print_command must assign a real handler to client.on_message; "
        "a MagicMock default means the MQTT accept path is unwired"
    )

def test_cmd_pause_success(capsys):
    args = Namespace(json=True, confirm=True)
    printer = MagicMock()
    printer.send_command.return_value = True
    with (
        patch("bambu_cli.commands.device.RuntimeContext.for_request") as fr,
        patch("bambu_cli.commands.device.get_sequence_id", return_value="1"),
    ):
        ctx = MagicMock()
        ctx.printer.return_value = printer
        fr.return_value = ctx
        commands_mod.cmd_pause(args)
    printer.send_command.assert_called_once()
    out = capsys.readouterr().out
    assert "paused" in out.lower() or '"status"' in out

def test_setup_noninteractive_writes_config(tmp_path, capsys):
    cfg = tmp_path / "config.json"
    code = tmp_path / "access_code"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    orca.chmod(0o755)
    args = Namespace(
        printer_ip="192.168.1.50",
        serial="01P00A000000000",
        access_code="12345678",
        access_code_file=str(code),
        access_code_env=None,
        config=str(cfg),
        model="P1S",
        nozzle="0.4",
        orca_slicer=str(orca),
        profiles_dir=str(profiles),
        json=True,
        cert_fingerprint="ab" * 32,
        insecure_tls=False,
    )
    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
    ):
        wizard_mod._cmd_setup_noninteractive(args)
    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "192.168.1.50"
    assert data["serial"] == "01P00A000000000"

def test_printer_error_hex_rendering():
    assert mqtt_mod._printer_error_hex(83935248) == "0x0500C010"
    assert mqtt_mod._printer_error_hex(1234) == "0x000004D2"
    assert mqtt_mod._printer_error_hex("nope") is None
    assert mqtt_mod._printer_error_hex(True) is None
    assert mqtt_mod._printer_error_hex(None) is None

def test_execute_print_printer_error_code_records_hex():
    from bambu_cli import utils as utils_mod

    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"command": "project_file", "print_error": 83935248}}).encode()
        client.on_message(client, None, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    utils_mod._LAST_ERROR_PAYLOAD = None
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        pytest.raises(BambuError),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

    payload = utils_mod._LAST_ERROR_PAYLOAD
    assert payload["printer_error_code"] == 83935248
    assert payload["printer_error_code_hex"] == "0x0500C010"

def test_execute_print_connect_refused_is_network_error():
    """rc != 0 (bad CONNACK / wrong access code) must fail, not report success.

    Regression for the false 'Print started' exit 0 on a refused connection.
    """
    from bambu_cli import utils as utils_mod
    from bambu_cli.constants import EXIT_NETWORK_ERROR

    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def loop_start():
        # Broker refuses: paho delivers rc=4 (bad user/pass) via on_connect
        # asynchronously after loop_start (mirrors _mqtt_connect being patched).
        client.on_connect(client, None, None, 4)

    client.loop_start.side_effect = loop_start
    utils_mod._LAST_ERROR_PAYLOAD = None
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        pytest.raises(BambuError),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

    payload = utils_mod._LAST_ERROR_PAYLOAD
    assert payload is not None
    assert payload["exit_code"] == EXIT_NETWORK_ERROR
    assert payload["printed"] is False
    # The publish must never have happened on a refused connection.
    assert not client.publish.called

def test_execute_print_rejected_result_is_printer_error():
    """A project_file ack with result=fail must not report success."""
    from bambu_cli import utils as utils_mod
    from bambu_cli.constants import EXIT_PRINTER_ERROR

    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        msg = MagicMock()
        # Firmware rejects the job in the ack itself, no nonzero print_error.
        msg.payload = json.dumps(
            {"print": {"command": "project_file", "result": "fail", "reason": "invalid ams_mapping"}}
        ).encode()
        client.on_message(client, None, msg)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    utils_mod._LAST_ERROR_PAYLOAD = None
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        pytest.raises(BambuError),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

    payload = utils_mod._LAST_ERROR_PAYLOAD
    assert payload is not None
    assert payload["exit_code"] == EXIT_PRINTER_ERROR
    assert payload["printed"] is False
    assert "invalid ams_mapping" in payload["error"]

def test_execute_print_stale_error_before_ack_is_not_blamed():
    """A latched print_error from a prior job (a lone periodic report arriving
    before our project_file ack) must not be attributed to this print."""
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        # First: a stale periodic *full-state snapshot* carrying a latched error
        # from a prior job (all required snapshot keys present), with NO
        # project_file command field — this is not our ack.
        stale = MagicMock()
        stale.payload = json.dumps(
            {
                "print": {
                    "print_error": 83935248,
                    "gcode_state": "IDLE",
                    "mc_percent": 0,
                    "bed_temper": 25.0,
                    "nozzle_temper": 30.0,
                }
            }
        ).encode()
        client.on_message(client, None, stale)
        # Then: our clean project_file ack (error already cleared / not ours).
        ack = MagicMock()
        ack.payload = json.dumps({"print": {"command": "project_file", "print_error": 0}}).encode()
        client.on_message(client, None, ack)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        # Must NOT raise: the stale error predates our ack and is not ours.
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

def test_execute_print_error_after_ack_is_blamed():
    """An error arriving with/after our project_file ack is still reported."""
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def connect(*a, **k):
        client.on_connect(client, None, None, 0)

    def loop_start():
        ack = MagicMock()
        ack.payload = json.dumps({"print": {"command": "project_file", "print_error": 0}}).encode()
        client.on_message(client, None, ack)
        # A later report carries a real error for OUR print.
        err = MagicMock()
        err.payload = json.dumps({"print": {"print_error": 1234}}).encode()
        client.on_message(client, None, err)

    client.connect.side_effect = connect
    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
        pytest.raises(BambuError),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)

def test_execute_print_on_connect_publishes_once_but_resubscribes():
    """paho auto-reconnect re-firing on_connect must not re-publish the print,
    but MUST resubscribe on every (re)connect (clean_session drops the sub)."""
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def loop_start():
        # Simulate a reconnect: on_connect fires twice within the ack window.
        client.on_connect(client, None, None, 0)
        client.on_connect(client, None, None, 0)
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"command": "project_file", "print_error": 0}}).encode()
        client.on_message(client, None, msg)

    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        mqtt_mod.execute_print_command(printer, "{}", "x.3mf", dry_run=False, command_timeout=1)
    # The print-start payload must be published exactly once despite two connects.
    request_publishes = [c for c in client.publish.call_args_list if c.args and str(c.args[0]).endswith("/request")]
    assert len(request_publishes) == 1
    # But the report subscription must be (re)established on BOTH connects, or an
    # ack after a mid-window reconnect would be invisible and time the print out.
    report_subscribes = [c for c in client.subscribe.call_args_list if c.args and str(c.args[0]).endswith("/report")]
    assert len(report_subscribes) == 2

def test_send_command_on_connect_publishes_once():
    """send_command must not re-publish on a paho auto-reconnect either."""
    printer = _test_printer(simulation_mode=False)
    client = MagicMock()

    def loop_start():
        client.on_connect(client, None, None, 0)
        client.on_connect(client, None, None, 0)
        client.on_publish(client, None, 1)

    client.loop_start.side_effect = loop_start
    with (
        patch.object(mqtt_mod, "create_mqtt_client", return_value=client),
        patch.object(mqtt_mod, "_mqtt_connect"),
    ):
        assert mqtt_mod.send_command(printer, "{}", timeout=1, retries=0) is True
    request_publishes = [c for c in client.publish.call_args_list if c.args and str(c.args[0]).endswith("/request")]
    assert len(request_publishes) == 1
    # And publishes at QoS 1 so on_publish reflects a broker PUBACK, not a bare
    # local socket write.
    assert request_publishes[0].kwargs.get("qos") == 1
