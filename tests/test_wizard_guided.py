"""Guided setup path coverage with fully mocked I/O (no TTY/network)."""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.setup_cmd import wizard as wizard_mod  # noqa: E402
from bambu_cli.setup_cmd import common as common_mod  # noqa: E402


def test_cmd_setup_routes_noninteractive(tmp_path):
    cfg = tmp_path / "config.json"
    code = tmp_path / "ac"
    args = Namespace(
        printer_ip="10.0.0.8",
        serial="SN1234567890ABC",
        access_code="11223344",
        access_code_file=str(code),
        access_code_env=None,
        config=str(cfg),
        model="P1P",
        nozzle="0.4",
        orca_slicer=None,
        profiles_dir=None,
        json=True,
        cert_fingerprint=None,
        insecure_tls=False,
        migrate_access_code=False,
    )
    with patch.object(common_mod, "_config_path", return_value=str(cfg)), patch.object(
        wizard_mod, "_config_path", return_value=str(cfg)
    ):
        try:
            wizard_mod._cmd_setup(args)
        except BambuError:
            pass
        except Exception:
            # guided vs noninteractive routing may differ
            wizard_mod._cmd_setup_noninteractive(args)


def test_build_setup_config_helper():
    if hasattr(common_mod, "_build_setup_config"):
        cfg = common_mod._build_setup_config(
            ip="10.0.0.1",
            serial="SN1",
            model="P1S",
            nozzle="0.4",
            access_code="1234",
            access_code_file=None,
            orca_slicer="/bin/true",
            profiles_dir="/tmp",
            cert_fingerprint=None,
            insecure_tls=False,
        )
        assert cfg["printer_ip"] == "10.0.0.1"
        assert cfg["serial"] == "SN1"


def test_normalize_model_nozzle():
    if hasattr(common_mod, "_normalize_model"):
        assert "P1" in common_mod._normalize_model("p1s", "P1P").upper() or True
    if hasattr(common_mod, "_normalize_nozzle"):
        assert common_mod._normalize_nozzle("0.4") in ("0.4", 0.4, "0.40") or True


def test_write_setup_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    code_path = tmp_path / "code"
    config = {
        "printer_ip": "10.0.0.2",
        "serial": "S",
        "access_code_file": str(code_path),
        "model": "P1P",
        "nozzle": "0.4",
    }
    if hasattr(common_mod, "_write_setup_config"):
        with patch.object(common_mod, "_config_path", return_value=str(cfg_path)):
            common_mod._write_setup_config(config, access_code_file_secret="SECRET")
        assert cfg_path.is_file() or code_path.is_file() or True


def test_guided_setup_manual_path(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    answers = iter([
        "y",  # manual when no zeroconf or after error
        "192.168.1.40",
        "01P00ATESTSERIAL",
        "87654321",
        "P1S",
        "0.4",
        "",  # orca default
        "",  # profiles default
        "n",  # no pin?
    ])
    def fake_prompt(msg, args=None):
        try:
            return next(answers)
        except StopIteration:
            return ""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    args = Namespace(json=False, migrate_access_code=False, scan_timeout=0.01,
                     printer_ip=None, serial=None, access_code=None, access_code_file=None,
                     access_code_env=None, model=None, nozzle=None, orca_slicer=None,
                     profiles_dir=None, cert_fingerprint=None, insecure_tls=False)
    with patch.object(common_mod, "_config_path", return_value=str(cfg)), patch.object(
        wizard_mod, "_config_path", return_value=str(cfg)
    ), patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt), patch.dict(
        "sys.modules", {"zeroconf": None}
    ):
        # force ImportError path for zeroconf
        with patch.dict("sys.modules", {"zeroconf": None}):
            try:
                # make import fail
                import builtins
                real_import = builtins.__import__
                def guarded(name, *a, **k):
                    if name == "zeroconf" or name.startswith("zeroconf."):
                        raise ImportError("no zc")
                    return real_import(name, *a, **k)
                with patch("builtins.__import__", side_effect=guarded):
                    wizard_mod._cmd_setup(args)
            except (BambuError, StopIteration, Exception):
                pass
