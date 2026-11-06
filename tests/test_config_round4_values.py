"""Audit round 4: timeout ceiling, wrongly typed config values, unreadable config files, mDNS names."""

from __future__ import annotations

import argparse
import io
from unittest.mock import patch

import pytest

from bambu_cli import config
from bambu_cli.cliparse import positive_seconds
from bambu_cli.constants import EXIT_CONFIG_ERROR, MAX_TIMEOUT_SECONDS
from bambu_cli.context import Settings
from bambu_cli.errors import BambuError
from bambu_cli.setup_cmd import collect_preflight_checks
from bambu_cli.tlspin import normalize_fingerprint
from tests.bambu_test_base import config_ctx

GOOD_PIN = "aa" * 32


# --- C1 ----------------------------------------------------------------------------------


def test_max_timeout_constant():
    assert MAX_TIMEOUT_SECONDS == 86400


@pytest.mark.parametrize("value", [1e10, 86401, "86401"])
def test_config_timeout_over_limit_rejected(value):
    problem = config.config_timeout_problem(value)
    assert problem and "86400" in problem


def test_config_timeout_at_limit_ok():
    assert config.config_timeout_problem(86400) is None


@pytest.mark.parametrize("value", ["1e10", "86401"])
def test_positive_seconds_over_limit_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError, match="86400"):
        positive_seconds(value)


def test_positive_seconds_at_limit_ok():
    assert positive_seconds("86400") == 86400.0


# --- C3 ----------------------------------------------------------------------------------


def _preflight(cfg):
    with (
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value=None),
        patch("os.path.isdir", return_value=True),
        patch("os.path.exists", return_value=True),
        patch("shutil.which", return_value=None),
    ):
        return collect_preflight_checks()


def _check(checks, name):
    return [c for c in checks if c["name"] == name]


BASE = {"printer_ip": "10.1.2.3", "serial": "S1", "access_code": "12345678"}


@pytest.mark.parametrize("value", ["yes", "true", "1", "on"])
def test_preflight_insecure_tls_truthy_string_says_disabled(value):
    assert Settings.from_config({"insecure_tls": value}).insecure_tls is True
    (tls,) = _check(_preflight({**BASE, "insecure_tls": value}), "insecure-tls")
    assert "DISABLED" in tls["message"]
    assert "stays ENABLED" not in tls["message"]


def test_preflight_insecure_tls_unreadable_says_enabled():
    (tls,) = _check(_preflight({**BASE, "insecure_tls": 7}), "insecure-tls")
    assert "stays ENABLED" in tls["message"]


# --- C4 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, ["x"], 12345678, True])
def test_non_string_inline_access_code_is_a_config_error(value):
    with config_ctx({"access_code": value}), pytest.raises(BambuError) as exc:
        config.load_access_code()
    assert exc.value.exit_code == EXIT_CONFIG_ERROR


@pytest.mark.parametrize("value", [None, 5, ["a"]])
def test_non_string_access_code_file_is_treated_as_unset(value):
    with config_ctx({"access_code_file": value}), pytest.raises(BambuError) as exc:
        config.load_access_code()
    assert exc.value.exit_code == EXIT_CONFIG_ERROR


def test_null_access_code_file_falls_back_to_inline():
    with config_ctx({"access_code_file": None, "access_code": "REALCODE1"}):
        assert config.load_access_code() == "REALCODE1"


@pytest.mark.parametrize("value", [5, True, ["aa"], {"a": 1}])
def test_non_string_pin_is_a_config_error(value):
    with pytest.raises(BambuError) as exc:
        normalize_fingerprint(value)
    assert exc.value.exit_code == EXIT_CONFIG_ERROR


def test_expected_fingerprint_non_string_is_a_config_error():
    with config_ctx({"cert_fingerprint": 5}), pytest.raises(BambuError) as exc:
        config._expected_fingerprint()
    assert exc.value.exit_code == EXIT_CONFIG_ERROR


def test_normalize_fingerprint_still_accepts_none_and_strings():
    assert normalize_fingerprint(None) is None
    assert normalize_fingerprint("AA:BB") == "aabb"


@pytest.mark.parametrize("pin", [5, ["a"], "", "   "])
def test_validate_reports_bad_pin(pin):
    checks = _preflight({**BASE, "cert_fingerprint": pin})
    (pin_check,) = _check(checks, "cert-fingerprint")
    assert pin_check["status"] == "error"


def test_validate_good_pin_is_ok():
    (pin_check,) = _check(_preflight({**BASE, "cert_fingerprint": GOOD_PIN}), "cert-fingerprint")
    assert pin_check["status"] == "ok"


def test_int_camera_port_is_accepted():
    settings = Settings.from_config({"camera_port": 1985})
    assert settings.camera_port.split(":")[-2] == "1985"
    assert "1985" in settings.camera_stream_url


@pytest.mark.parametrize("value", [0, 70000, -1, True, ["x"], 1.5])
def test_bad_camera_port_does_not_drop_the_config(value):
    settings = Settings.from_config({"camera_port": value, "printer_ip": "10.0.0.5"})
    assert settings.printer_ip == "10.0.0.5"
    (check,) = _check(_preflight({**BASE, "camera_port": value}), "camera-port")
    assert check["status"] == "error"


def test_preflight_does_not_blame_json_for_int_port():
    checks = _preflight({**BASE, "camera_port": 1985})
    assert [c for c in checks if c["name"] == "camera-port"][0]["status"] == "ok"


@pytest.mark.parametrize("value", [["a"], 5, None, True])
def test_non_string_printer_ip(value):
    settings = Settings.from_config({"printer_ip": value})
    assert isinstance(settings.printer_ip, str)
    (check,) = _check(_preflight({**BASE, "printer_ip": value}), "printer-ip")
    assert check["status"] == "error"


def test_preflight_non_string_access_fields_do_not_crash():
    checks = _preflight({**BASE, "access_code": None, "access_code_file": ["x"]})
    assert _check(checks, "access-code")[0]["status"] == "error"
    checks = _preflight({**BASE, "access_code": ["x"]})
    assert _check(checks, "access-code")[0]["status"] == "error"


# --- C5 ----------------------------------------------------------------------------------


def _cfg_file(tmp_path, data: bytes):
    path = tmp_path / "config.json"
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize("data", [b"\xff\xfe", b"[]", b"3", b'"x"'])
def test_read_config_json_rejects_bad_files_as_valueerror(tmp_path, data):
    with pytest.raises(ValueError):
        config.read_config_json(_cfg_file(tmp_path, data))


@pytest.mark.parametrize("data", [b"\xff\xfe", b"[]"])
def test_config_show_reports_config_error(tmp_path, data):
    from bambu_cli.setup_cmd.config_cmd import _cmd_config_show

    path = _cfg_file(tmp_path, data)
    args = argparse.Namespace(cmd="config", action="show", json=False)
    with (
        patch("bambu_cli.setup_cmd.config_cmd._config_path", return_value=path),
        patch("bambu_cli.setup_cmd.config_cmd.logger"),
        patch("sys.stdout", new_callable=io.StringIO) as out,
        pytest.raises(BambuError) as exc,
    ):
        _cmd_config_show(args)
    assert exc.value.exit_code == EXIT_CONFIG_ERROR
    assert '"config"' not in out.getvalue()


@pytest.mark.parametrize("data", [b"\xff\xfe", b"[]"])
def test_migrate_reports_config_error(tmp_path, data):
    from bambu_cli.setup_cmd.migrate import _cmd_migrate_access_code

    path = _cfg_file(tmp_path, data)
    args = argparse.Namespace(json=False, access_code_file=None)
    with (
        patch("bambu_cli.setup_cmd.migrate._config_path", return_value=path),
        patch("bambu_cli.setup_cmd.migrate.logger"),
        pytest.raises(BambuError) as exc,
    ):
        _cmd_migrate_access_code(args)
    assert exc.value.exit_code == EXIT_CONFIG_ERROR


# --- C6 ----------------------------------------------------------------------------------


def test_discovery_log_lines_strip_control_chars(tmp_path, monkeypatch):
    import builtins
    import sys
    from unittest.mock import MagicMock

    from bambu_cli.setup_cmd import common as common_mod
    from bambu_cli.setup_cmd import wizard as wizard_mod
    from tests.test_wizard_guided import create_mock_zeroconf, mock_service_browser

    cfg = tmp_path / "config.json"
    answers = iter(["0", "87654321", "", "", ""])

    def fake_prompt(msg, args=None, default=None):
        return next(answers, default or "")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = argparse.Namespace(
        json=False, migrate_access_code=False, scan_timeout=0.01, printer_ip=None, serial=None, access_code=None,
        access_code_file=None, access_code_env=None, model=None, nozzle=None, orca_slicer=None, profiles_dir=None,
        cert_fingerprint=None, insecure_tls=False, config=str(cfg),
    )
    services = [
        ("BBLP-P1P-01P00A123._bblp\nERROR: spoofed\x1b[2J._tcp.local.", "10.0.0.50"),
        ("BBLP-A1-03000A111._bblp._tcp.local.", "10.0.0.70"),
    ]
    log = MagicMock()
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name == "zeroconf":
            zc = MagicMock()
            zc.Zeroconf = create_mock_zeroconf(services)
            zc.ServiceBrowser = mock_service_browser(services)
            return zc
        return real_import(name, *a, **k)

    with (
        patch.object(common_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "_config_path", return_value=str(cfg)),
        patch.object(wizard_mod, "logger", log),
        patch.object(wizard_mod, "_prompt_text", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_secret", side_effect=fake_prompt),
        patch.object(wizard_mod, "_prompt_access_code_file_path", return_value=None),
        patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value="aa:bb:cc"),
        patch("builtins.__import__", side_effect=guarded),
    ):
        wizard_mod._cmd_setup(args)

    logged = [str(c.args[0]) for c in log.method_calls if c.args]
    joined = "\n".join(logged)
    assert "spoofed" in joined  # the name is still shown, just defanged
    assert "\x1b" not in joined
    assert not any("\nERROR: spoofed" in line for line in logged)
