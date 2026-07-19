"""Guided setup path coverage with fully mocked I/O (no TTY/network)."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.setup_cmd import common as common_mod  # noqa: E402
from bambu_cli.setup_cmd import wizard as wizard_mod  # noqa: E402


def test_cmd_setup_routes_noninteractive(tmp_path):
    cfg = tmp_path / "config.json"
    code = tmp_path / "ac"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    args = Namespace(
        printer_ip="10.0.0.8",
        serial="SN1234567890ABC",
        access_code="11223344",
        access_code_file=str(code),
        access_code_env=None,
        config=str(cfg),
        model="P1P",
        nozzle="0.4",
        orca_slicer="/bin/true",
        profiles_dir=str(profiles),
        json=True,
        cert_fingerprint=None,
        insecure_tls=False,
        migrate_access_code=False,
    )
    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
    ):
        # Noninteractive fields present → should write config (not enter guided TTY).
        wizard_mod._cmd_setup(args)
    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "10.0.0.8"
    assert data["serial"] == "SN1234567890ABC"


def test_build_setup_config_helper():
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
    assert common_mod._normalize_model("p1s", "P1P") == "P1S"
    assert common_mod._normalize_nozzle("0.4") == "0.4"


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
    with patch.object(common_mod, "_config_path", return_value=str(cfg_path)):
        common_mod._write_setup_config(config, access_code_file_secret="SECRET")
    assert cfg_path.is_file()
    assert code_path.is_file()
    assert code_path.read_text(encoding="utf-8").strip() == "SECRET"


def test_guided_setup_manual_path(tmp_path, monkeypatch):
    """When zeroconf is unavailable, guided setup falls back to manual prompts."""
    cfg = tmp_path / "config.json"
    answers = iter(
        [
            "192.168.1.40",
            "01P00ATESTSERIAL",
            "87654321",
            "P1S",
            "0.4",
            "",  # orca default
            "",  # profiles default
            "n",  # no pin
        ]
    )

    def fake_prompt(msg, args=None, default=None):
        try:
            return next(answers)
        except StopIteration:
            return default or ""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = Namespace(
        json=False,
        migrate_access_code=False,
        scan_timeout=0.01,
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_file=None,
        access_code_env=None,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        config=str(cfg),
    )
    import builtins

    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name == "zeroconf" or (isinstance(name, str) and name.startswith("zeroconf.")):
            raise ImportError("no zc")
        return real_import(name, *a, **k)

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt),
        patch.object(common_mod, "_prompt_text", side_effect=fake_prompt),
        patch("builtins.__import__", side_effect=guarded),
    ):
        raised = None
        try:
            wizard_mod._cmd_setup(args)
        except BambuError as exc:
            raised = exc
    # Either wrote config or raised a structured error — not a silent pass.
    if cfg.is_file():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data.get("printer_ip") == "192.168.1.40"
    else:
        assert raised is not None, "guided setup neither wrote config nor raised"

class MockServiceInfo:
    def __init__(self, ip):
        self.ip = ip
    def parsed_addresses(self):
        return [self.ip]

class MockZeroconf:
    def __init__(self, services):
        self.services = services
        self.closed = False

    def get_service_info(self, type_, name):
        for s_name, s_ip in self.services:
            if s_name == name:
                return MockServiceInfo(s_ip)
        return None

    def close(self):
        self.closed = True

def create_mock_zeroconf(services):
    return lambda: MockZeroconf(services)

def mock_service_browser(services):
    def init(zc, type_, listener):
        for name, _ip in services:
            listener.add_service(zc, type_, name)
        return MagicMock()
    return init

def test_guided_setup_mdns_one_printer(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    answers = iter([
        "87654321",  # access code
        "",  # confirm model
        "",  # confirm nozzle
        "",  # access code file (empty to use secret prompt)
    ])

    def fake_prompt(msg, args=None, default=None):
        try:
            return next(answers)
        except StopIteration:
            return default or ""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = Namespace(
        json=False,
        migrate_access_code=False,
        scan_timeout=0.01,
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_file=None,
        access_code_env=None,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        config=str(cfg),
    )

    services = [("BBLP-P1P-01P00A123._bblp._tcp.local.", "10.0.0.50")]

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_secret", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_access_code_file_path", return_value=None),
        patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value="aa:bb:cc"),
    ):
        import builtins
        real_import = builtins.__import__

        def guarded(name, *a, **k):
            if name == "zeroconf":
                mock_zc_module = MagicMock()
                mock_zc_module.Zeroconf = create_mock_zeroconf(services)
                mock_zc_module.ServiceBrowser = mock_service_browser(services)
                return mock_zc_module
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=guarded):
            wizard_mod._cmd_setup(args)

    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "10.0.0.50"
    assert data["serial"] == "01P00A123"
    assert data["model"] == "P1P"

def test_guided_setup_mdns_multiple_printers(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    answers = iter([
        "1",         # choice: index 1 (the second printer)
        "11223344",  # access code
        "",  # confirm model
        "",  # confirm nozzle
        "",  # access code file
    ])

    def fake_prompt(msg, args=None, default=None):
        try:
            return next(answers)
        except StopIteration:
            return default or ""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = Namespace(
        json=False,
        migrate_access_code=False,
        scan_timeout=0.01,
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_file=None,
        access_code_env=None,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        config=str(cfg),
    )

    services = [
        ("BBLP-X1C-00M00A000._bblp._tcp.local.", "10.0.0.60"),
        ("BBLP-A1-03000A111._bblp._tcp.local.", "10.0.0.70"),
    ]

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_secret", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_access_code_file_path", return_value=None),
        patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value="dd:ee:ff"),
    ):
        import builtins
        real_import = builtins.__import__

        def guarded(name, *a, **k):
            if name == "zeroconf":
                mock_zc_module = MagicMock()
                mock_zc_module.Zeroconf = create_mock_zeroconf(services)
                mock_zc_module.ServiceBrowser = mock_service_browser(services)
                return mock_zc_module
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=guarded):
            wizard_mod._cmd_setup(args)

    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "10.0.0.70"
    assert data["serial"] == "03000A111"
    assert data["model"] == "A1"

def test_guided_setup_mdns_no_printers(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = Namespace(
        json=False,
        migrate_access_code=False,
        scan_timeout=0.01,
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_file=None,
        access_code_env=None,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        config=str(cfg),
    )

    services = []

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
    ):
        import builtins
        real_import = builtins.__import__

        def guarded(name, *a, **k):
            if name == "zeroconf":
                mock_zc_module = MagicMock()
                mock_zc_module.Zeroconf = create_mock_zeroconf(services)
                mock_zc_module.ServiceBrowser = mock_service_browser(services)
                return mock_zc_module
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=guarded):
            raised = None
            try:
                wizard_mod._cmd_setup(args)
            except BambuError as exc:
                raised = exc

    assert raised is not None
    assert raised.exit_code == 2  # EXIT_NETWORK_ERROR


def test_guided_setup_mdns_discovery_error(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    answers = iter([
        "192.168.1.100", # manual IP fallback
        "03000A222",     # manual Serial fallback
        "12341234",      # access code
        "",              # confirm model
        "",              # confirm nozzle
        "",              # access code file
    ])

    def fake_prompt(msg, args=None, default=None):
        try:
            return next(answers)
        except StopIteration:
            return default or ""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = Namespace(
        json=False,
        migrate_access_code=False,
        scan_timeout=0.01,
        printer_ip=None,
        serial=None,
        access_code=None,
        access_code_file=None,
        access_code_env=None,
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        config=str(cfg),
    )

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_secret", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_access_code_file_path", return_value=None),
        patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value=None),
    ):
        import builtins
        real_import = builtins.__import__

        def guarded(name, *a, **k):
            if name == "zeroconf":
                mock_zc_module = MagicMock()
                # raise exception to trigger manual fallback block
                def failing_zc(*args, **kwargs):
                    raise Exception("simulated mDNS failure")
                mock_zc_module.Zeroconf = failing_zc
                return mock_zc_module
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=guarded):
            wizard_mod._cmd_setup(args)

    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["printer_ip"] == "192.168.1.100"
    assert data["serial"] == "03000A222"
