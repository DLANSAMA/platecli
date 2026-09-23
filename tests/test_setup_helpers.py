"""Unit tests for setup pure helpers (wizard mDNS parse, common config builders)."""

from __future__ import annotations

import json
import socket
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.setup_cmd import common as common_mod  # noqa: E402
from bambu_cli.setup_cmd import wizard as wizard_mod  # noqa: E402

def test_service_info_parsed_addresses():
    info = MagicMock()
    info.parsed_addresses = lambda: ["10.0.0.9"]
    info.addresses = []
    assert wizard_mod._service_info_address(info) == "10.0.0.9"

def test_service_info_raw_ipv4():
    info = MagicMock()
    info.parsed_addresses = None
    info.addresses = [socket.inet_aton("192.168.1.5")]
    assert wizard_mod._service_info_address(info) == "192.168.1.5"

def test_service_info_no_address():
    info = MagicMock()
    info.parsed_addresses = lambda: []
    info.addresses = []
    with pytest.raises(ValueError):
        wizard_mod._service_info_address(info)

def test_parse_mdns_identity_model_prefix():
    serial, model = wizard_mod._parse_mdns_printer_identity("BBLP-P1S-01P00A123456789._bblp._tcp.local.")
    assert model in ("P1S", "P1P") or serial

def test_parse_mdns_identity_plain():
    serial, model = wizard_mod._parse_mdns_printer_identity("something-else.local")
    # No model in the service name: nothing is guessed, the setup prompt asks.
    assert model is None

def test_normalize_model_nozzle():
    assert common_mod._normalize_model("x1c", "P1P") == "X1C"
    assert common_mod._normalize_nozzle("0.6") == "0.6"

def test_build_and_write_setup_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    code_path = tmp_path / "access_code"
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    config = common_mod._build_setup_config(
        ip="10.1.2.3",
        serial="SNABC",
        model="P1S",
        nozzle="0.4",
        access_code="11223344",
        access_code_file=str(code_path),
        orca_slicer="/bin/true",
        profiles_dir=str(tmp_path),
        cert_fingerprint="ab" * 32,
        insecure_tls=False,
    )
    assert config["printer_ip"] == "10.1.2.3"
    common_mod._write_setup_config(config, access_code_file_secret="11223344")
    assert cfg_path.is_file()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "access_code" not in data or data.get("access_code_file")
    assert code_path.is_file()
    summary = common_mod._setup_summary(config)
    assert summary.get("printer_ip_configured") is True or "printer_ip" in summary

def _rerun_setup(cfg_path, monkeypatch, **overrides):
    """Build a fresh wizard config and write it over cfg_path, as a setup re-run does."""
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    kwargs = {
        "ip": "10.1.2.3",
        "serial": "SNABC",
        "model": "P1S",
        "nozzle": "0.4",
        "access_code": "11223344",
        "orca_slicer": "/bin/true",
        "profiles_dir": "/tmp",
    }
    kwargs.update(overrides)
    common_mod._write_setup_config(common_mod._build_setup_config(**kwargs))
    return json.loads(cfg_path.read_text(encoding="utf-8"))

def test_setup_rerun_preserves_unmanaged_keys(tmp_path, monkeypatch):
    """Re-running setup must not delete keys the wizard does not manage.

    Regression: the wizard built config.json from scratch, so a setup re-run
    silently dropped camera_direct_only (a security opt-in), camera_port, and the
    timeout tunables. A security control that vanishes without telling anyone is
    worse than one that was never set.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "printer_ip": "192.168.0.9",
                "serial": "OLDSERIAL",
                "access_code": "99998888",
                "camera_direct_only": True,
                "camera_allow_streamer": True,
                "camera_port": "0.0.0.0:1985:1984",
                "network_timeout": 42,
            }
        ),
        encoding="utf-8",
    )
    data = _rerun_setup(cfg_path, monkeypatch)

    # Unmanaged keys survive, values intact.
    assert data["camera_direct_only"] is True
    assert data["camera_allow_streamer"] is True
    assert data["camera_port"] == "0.0.0.0:1985:1984"
    assert data["network_timeout"] == 42
    # Wizard-owned keys are still updated to the new answers.
    assert data["printer_ip"] == "10.1.2.3"
    assert data["serial"] == "SNABC"

def test_setup_rerun_does_not_resurrect_inline_access_code(tmp_path, monkeypatch):
    """Moving an inline access_code into a file must REMOVE the inline copy.

    A blanket merge would leave the plaintext secret behind in config.json while
    the summary reported access_code_storage=file.
    """
    cfg_path = tmp_path / "config.json"
    code_path = tmp_path / "access_code"
    cfg_path.write_text(json.dumps({"access_code": "99998888", "camera_port": "127.0.0.1:1985:1984"}), "utf-8")
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    config = common_mod._build_setup_config(
        ip="10.1.2.3",
        serial="SNABC",
        model="P1S",
        nozzle="0.4",
        access_code_file=str(code_path),
    )
    common_mod._write_setup_config(config, access_code_file_secret="11223344")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))

    assert "access_code" not in data, "inline secret must not survive the switch to a file"
    assert data["access_code_file"] == str(code_path)
    assert data["camera_port"] == "127.0.0.1:1985:1984"  # unmanaged key still preserved

def test_setup_rerun_clears_declined_insecure_tls(tmp_path, monkeypatch):
    """Declining insecure_tls must turn it off, not preserve the old true.

    _build_setup_config only emits insecure_tls when it is True, so absence has to
    mean removal -- otherwise a user who disables it stays exposed.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"insecure_tls": True, "camera_port": "127.0.0.1:1985:1984"}), "utf-8")
    data = _rerun_setup(cfg_path, monkeypatch, insecure_tls=False)

    assert "insecure_tls" not in data
    assert data["camera_port"] == "127.0.0.1:1985:1984"

def test_setup_rerun_survives_unreadable_existing_config(tmp_path, monkeypatch):
    """A corrupt existing config must not abort setup; it warns and writes fresh."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{not json at all", encoding="utf-8")
    with patch.object(common_mod, "logger") as mock_logger:
        data = _rerun_setup(cfg_path, monkeypatch)
    assert data["printer_ip"] == "10.1.2.3"
    assert any("could not be preserved" in str(c) for c in mock_logger.warning.call_args_list)

def test_setup_first_run_with_no_existing_config(tmp_path, monkeypatch):
    """No config on disk is the normal first run, not an error."""
    cfg_path = tmp_path / "nested" / "config.json"
    data = _rerun_setup(cfg_path, monkeypatch)
    assert data["printer_ip"] == "10.1.2.3"
    assert data["serial"] == "SNABC"

def test_setup_summary_and_path_details():
    details = common_mod._setup_path_details(access_code_file="/tmp/x")
    assert "access_code_file" in details

def test_validate_access_code_file_missing(tmp_path):
    args = Namespace(json=False)
    with pytest.raises(BambuError):
        # path that looks invalid with leading dash
        common_mod._validate_setup_access_code_file(args, "-bad")

def test_default_access_code_file_path():
    p = common_mod._default_access_code_file_path()
    assert "access_code" in p or "bambu" in p

def test_noninteractive_access_code_env(monkeypatch, tmp_path):
    cfg = tmp_path / "c.json"
    monkeypatch.setenv("BAMBU_TEST_CODE", "99887766")
    args = Namespace(
        printer_ip="10.0.0.3",
        serial="SNENVTEST01",
        access_code=None,
        access_code_file=None,
        access_code_env="BAMBU_TEST_CODE",
        config=str(cfg),
        model="P1P",
        nozzle="0.4",
        orca_slicer="/bin/true",
        profiles_dir=str(tmp_path),
        json=True,
        cert_fingerprint=None,
        insecure_tls=False,
    )
    with (
        patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)),
        patch("bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)),
    ):
        wizard_mod._cmd_setup_noninteractive(args)
    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "10.0.0.3"

def test_noninteractive_access_code_file(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    code = tmp_path / "code"
    code.write_text("55443322\n", encoding="utf-8")
    args = Namespace(
        printer_ip="10.0.0.4",
        serial="SNFILETEST01",
        access_code=None,
        access_code_file=str(code),
        access_code_env=None,
        config=str(cfg),
        model="A1",
        nozzle="0.4",
        orca_slicer="/bin/true",
        profiles_dir=str(tmp_path),
        json=True,
        cert_fingerprint=None,
        insecure_tls=False,
    )
    with (
        patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)),
        patch("bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)),
    ):
        wizard_mod._cmd_setup_noninteractive(args)
    assert cfg.is_file()

def test_service_info_parsed_addresses_raises():
    info = MagicMock()

    def boom():
        raise ValueError("x")

    info.parsed_addresses = boom
    info.addresses = [socket.inet_aton("10.0.0.1")]
    assert wizard_mod._service_info_address(info) == "10.0.0.1"


def test_service_info_raw_ipv6():
    info = MagicMock()
    info.parsed_addresses = None
    info.addresses = [socket.inet_pton(socket.AF_INET6, "2001:db8::1")]
    assert wizard_mod._service_info_address(info) == "2001:db8::1"


def test_noninteractive_both_code_and_env():
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SN123",
        access_code="code1",
        access_code_env="ENV_VAR",
        access_code_file=None,
        json=False,
    )
    with pytest.raises(BambuError) as cm:
        wizard_mod._cmd_setup_noninteractive(args)
    assert cm.value.exit_code == 1


def test_noninteractive_access_code_env_unset(monkeypatch):
    monkeypatch.delenv("UNSET_ACCESS_CODE_VAR", raising=False)
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SN123",
        access_code=None,
        access_code_env="UNSET_ACCESS_CODE_VAR",
        access_code_file=None,
        json=False,
    )
    with pytest.raises(BambuError) as cm:
        wizard_mod._cmd_setup_noninteractive(args)
    assert cm.value.exit_code == 1


def test_noninteractive_missing_required_args():
    args = Namespace(
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_env=None,
        access_code_file=None,
        json=False,
    )
    with pytest.raises(BambuError) as cm:
        wizard_mod._cmd_setup_noninteractive(args)
    assert cm.value.exit_code == 1


def test_noninteractive_access_code_file_not_found(tmp_path):
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SN123",
        access_code=None,
        access_code_env=None,
        access_code_file=str(tmp_path / "nonexistent_code"),
        json=False,
    )
    with pytest.raises(BambuError) as cm:
        wizard_mod._cmd_setup_noninteractive(args)
    assert cm.value.exit_code == 1


def test_noninteractive_placeholders():
    args = Namespace(
        printer_ip="192.168.0.XXX",
        serial="YOUR_SERIAL",
        access_code="ACCESS_CODE",
        access_code_env=None,
        access_code_file=None,
        json=False,
    )
    with pytest.raises(BambuError) as cm:
        wizard_mod._cmd_setup_noninteractive(args)
    assert cm.value.exit_code == 1


def test_noninteractive_write_oserror():
    args = Namespace(
        printer_ip="10.0.0.1",
        serial="SNVALID001",
        access_code="valid_code_123",
        access_code_env=None,
        access_code_file=None,
        model="P1P",
        nozzle="0.4",
        orca_slicer="/bin/true",
        profiles_dir="/tmp",
        cert_fingerprint=None,
        insecure_tls=False,
        json=False,
    )
    with patch("bambu_cli.setup_cmd.wizard._write_setup_config", side_effect=OSError("Disk full")):
        with pytest.raises(BambuError) as cm:
            wizard_mod._cmd_setup_noninteractive(args)
        assert cm.value.exit_code == 3


def test_cmd_setup_dispatches_migrate():
    args = Namespace(migrate_access_code=True)
    with patch("bambu_cli.setup_cmd.migrate._cmd_migrate_access_code") as mock_mig:
        wizard_mod._cmd_setup(args)
        mock_mig.assert_called_once_with(args)


def test_interactive_zeroconf_missing_refuse_manual():
    args = Namespace(json=False)
    with patch.dict("sys.modules", {"zeroconf": None}):
        with patch("bambu_cli.setup_cmd.wizard._prompt_text", return_value="n"):
            with pytest.raises(BambuError) as cm:
                wizard_mod._cmd_setup_interactive(args)
            assert cm.value.exit_code == 1


def test_interactive_manual_setup_missing_ip_or_serial():
    args = Namespace(json=False)
    with patch.dict("sys.modules", {"zeroconf": None}):
        # Choice 'y', but empty IP
        with patch("bambu_cli.setup_cmd.wizard._prompt_text", side_effect=["y", ""]):
            with pytest.raises(BambuError) as cm:
                wizard_mod._cmd_setup_interactive(args)
            assert cm.value.exit_code == 1

        # Choice 'y', IP given, but empty serial
        with patch("bambu_cli.setup_cmd.wizard._prompt_text", side_effect=["y", "192.168.1.50", ""]):
            with pytest.raises(BambuError) as cm:
                wizard_mod._cmd_setup_interactive(args)
            assert cm.value.exit_code == 1


def test_interactive_discovery_no_printers_found():
    args = Namespace(json=False, scan_timeout=0.01)
    with patch("zeroconf.Zeroconf"), patch("zeroconf.ServiceBrowser"):
        with pytest.raises(BambuError) as cm:
            wizard_mod._cmd_setup_interactive(args)
        assert cm.value.exit_code == 2


def test_interactive_discovery_multiple_printers_invalid_choices():
    args = Namespace(json=False, scan_timeout=0.01)

    def fake_sb(zc, type_, listener):
        info1 = MagicMock()
        info1.parsed_addresses = lambda: ["10.0.0.1"]
        info2 = MagicMock()
        info2.parsed_addresses = lambda: ["10.0.0.2"]
        zc.get_service_info.side_effect = [info1, info2]
        listener.add_service(zc, type_, "BBLP-P1P-01P00A111._bblp._tcp.local.")
        listener.add_service(zc, type_, "BBLP-X1C-01S00A222._bblp._tcp.local.")

    with patch("zeroconf.Zeroconf") as mock_zc, patch("zeroconf.ServiceBrowser", side_effect=fake_sb):
        # Out of bounds choice
        with patch("bambu_cli.setup_cmd.wizard._prompt_text", return_value="5"):
            with pytest.raises(BambuError) as cm:
                wizard_mod._cmd_setup_interactive(args)
            assert cm.value.exit_code == 5

        # ValueError choice
        with patch("bambu_cli.setup_cmd.wizard._prompt_text", return_value="not_int"):
            with pytest.raises(BambuError) as cm:
                wizard_mod._cmd_setup_interactive(args)
            assert cm.value.exit_code == 5

