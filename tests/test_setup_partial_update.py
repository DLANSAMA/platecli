"""`plate setup --<one value>` updates that value in an existing config.

docs/manual.md and docs/troubleshooting.md tell users to run, for example,
``plate setup --printer-ip <new-ip>`` after a DHCP change, ``plate setup
--access-code-env PLATE_CODE`` after rotating the LAN code, and ``plate setup
--profiles-dir <path>`` to fix a path. Before the 2026-09-23 fix none of them
worked: partial non-interactive setup failed on "missing required values", and
``--orca-slicer``/``--profiles-dir``/``--cert-fingerprint`` alone started the
interactive wizard and ignored the flag. Values not given now come from the
existing config -- the pin and access code only when it is the same printer.
"""

from __future__ import annotations

import json
import os

import pytest

from bambu_cli.cliparse import build_parser
from bambu_cli.errors import BambuError

PIN = "ab" * 32


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    from bambu_cli.setup_cmd import common as common_mod
    from bambu_cli.setup_cmd import wizard as wizard_mod

    cfg = tmp_path / "config.json"
    code = tmp_path / "access_code"
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg))
    monkeypatch.setattr(wizard_mod, "_config_path", lambda: str(cfg))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def write_existing(**over):
        code.write_text("12345678\n", encoding="utf-8")
        os.chmod(code, 0o600)
        data = {
            "printer_ip": "192.0.2.10",
            "serial": "01S00A000000000",
            "username": "bblp",
            "model": "P1S",
            "nozzle": "0.4",
            "orca_slicer": "/opt/orca",
            "profiles_dir": "/opt/profiles",
            "access_code_file": str(code),
            "cert_fingerprint": PIN,
            "camera_port": "127.0.0.1:1985:1984",
        }
        data.update(over)
        cfg.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(cfg, 0o600)

    def run(*argv):
        from bambu_cli.setup_cmd.wizard import _cmd_setup

        _cmd_setup(build_parser().parse_args(["setup", *argv]))
        return json.loads(cfg.read_text(encoding="utf-8"))

    return argparse_ns(cfg=cfg, code=code, write_existing=write_existing, run=run)


def argparse_ns(**kw):
    import argparse

    return argparse.Namespace(**kw)


def test_printer_ip_alone_updates_only_the_ip(setup_env):
    setup_env.write_existing()
    cfg = setup_env.run("--printer-ip", "192.0.2.77")
    assert cfg["printer_ip"] == "192.0.2.77"
    assert cfg["serial"] == "01S00A000000000" and cfg["model"] == "P1S"
    assert cfg["cert_fingerprint"] == PIN  # same printer, same certificate
    assert cfg["access_code_file"] == str(setup_env.code)
    assert cfg["camera_port"] == "127.0.0.1:1985:1984"


def test_access_code_rotation_rewrites_the_configured_file(setup_env, monkeypatch):
    setup_env.write_existing()
    monkeypatch.setenv("PLATE_CODE", "87654321")
    cfg = setup_env.run("--access-code-env", "PLATE_CODE")
    assert setup_env.code.read_text(encoding="utf-8").strip() == "87654321"
    assert cfg["access_code_file"] == str(setup_env.code)
    assert "access_code" not in cfg


@pytest.mark.parametrize(
    "argv, key, value",
    [
        (["--profiles-dir", "/new/profiles"], "profiles_dir", "/new/profiles"),
        (["--orca-slicer", "/new/orca"], "orca_slicer", "/new/orca"),
        (["--cert-fingerprint", "cd" * 32], "cert_fingerprint", "cd" * 32),
    ],
)
def test_path_and_pin_flags_update_instead_of_starting_the_wizard(setup_env, argv, key, value):
    setup_env.write_existing()
    cfg = setup_env.run(*argv)
    assert cfg[key] == value
    assert cfg["printer_ip"] == "192.0.2.10"


def test_a_different_printer_does_not_inherit_pin_or_code(setup_env, monkeypatch):
    setup_env.write_existing()
    with pytest.raises(BambuError) as caught:
        setup_env.run("--printer-ip", "192.0.2.99", "--serial", "03000B000000000")
    assert "--access-code" in str(caught.value.extra) + str(caught.value)
    monkeypatch.setenv("PLATE_CODE", "11112222")
    new_code = setup_env.code.parent / "other_code"
    cfg = setup_env.run(
        "--printer-ip", "192.0.2.99", "--serial", "03000B000000000",
        "--access-code-env", "PLATE_CODE", "--access-code-file", str(new_code), "--model", "A1",
    )  # fmt: skip
    assert "cert_fingerprint" not in cfg
    assert cfg["access_code_file"] == str(new_code)


def test_without_an_existing_config_partial_setup_still_asks_for_everything(setup_env):
    with pytest.raises(BambuError) as caught:
        setup_env.run("--printer-ip", "192.0.2.77")
    assert "--serial" in str(caught.value.extra) + str(caught.value)
    assert not setup_env.cfg.exists()
