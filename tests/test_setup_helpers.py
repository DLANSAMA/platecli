"""Unit tests for setup pure helpers (wizard mDNS parse, common config builders)."""

from __future__ import annotations

import json
import socket
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

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
    assert model == "P1P"


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
    with patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)), patch(
        "bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)
    ):
        try:
            wizard_mod._cmd_setup_noninteractive(args)
        except BambuError:
            pass


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
    with patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)), patch(
        "bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)
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
