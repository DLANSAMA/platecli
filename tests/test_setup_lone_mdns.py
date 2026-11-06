"""Setup confirms what mDNS found (2026-11-05 audit, S-3).

mDNS is unauthenticated, and the access code goes to whatever address is
configured. Before this fix a lone result was taken without asking and its
address was never looked at, so one spoofed answer on the LAN (``0.0.0.0``, a
public address) became the printer. Each test failed before the fix.
"""

from __future__ import annotations

import argparse
import builtins
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.errors import BambuError
from bambu_cli.setup_cmd import common as common_mod
from bambu_cli.setup_cmd import wizard as wizard_mod
from tests.test_wizard_guided import create_mock_zeroconf, mock_service_browser

P1P = "BBLP-P1P-01P00A123._bblp._tcp.local."
A1 = "BBLP-A1-03000A111._bblp._tcp.local."


def _run_setup(tmp_path, monkeypatch, services, answers):
    """Guided setup against fake mDNS results; returns (config or None, prompts, log lines, error)."""
    cfg = tmp_path / "config.json"
    pending = list(answers)
    asked = []

    def fake_prompt(msg, args=None, default=None):
        asked.append(msg)
        return pending.pop(0) if pending else (default or "")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = argparse.Namespace(
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
    log = MagicMock()
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name == "zeroconf":
            zc = MagicMock()
            zc.Zeroconf = create_mock_zeroconf(services)
            zc.ServiceBrowser = mock_service_browser(services)
            return zc
        return real_import(name, *a, **k)

    error = None
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
        try:
            wizard_mod._cmd_setup(args)
        except BambuError as exc:
            error = exc
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else None
    logged = [str(c.args[0]) for c in log.method_calls if c.args]
    return data, asked, logged, error


def test_a_lone_result_is_shown_and_asked_about(tmp_path, monkeypatch):
    data, asked, logged, _ = _run_setup(tmp_path, monkeypatch, [(P1P, "192.168.1.50")], ["", "87654321"])
    assert asked[0] == "Use this printer? [Y/n]: "
    assert any("192.168.1.50" in line and "BBLP-P1P" in line for line in logged)
    assert data["printer_ip"] == "192.168.1.50"


def test_declining_a_lone_result_falls_back_to_manual_entry(tmp_path, monkeypatch):
    answers = ["n", "192.168.1.77", "01P00AMANUAL", "87654321", "P1S"]
    data, asked, _, _ = _run_setup(tmp_path, monkeypatch, [(P1P, "192.168.1.50")], answers)
    assert asked[1].startswith("Enter Printer IP Address")
    assert data["printer_ip"] == "192.168.1.77"
    assert data["serial"] == "01P00AMANUAL"


@pytest.mark.parametrize(("ip", "why"), [("8.8.8.8", "not a local network address"), ("127.0.0.1", "this computer")])
def test_a_flagged_address_needs_an_explicit_yes(tmp_path, monkeypatch, ip, why):
    answers = ["", "192.168.1.77", "01P00AMANUAL", "87654321", "P1S"]
    data, asked, logged, _ = _run_setup(tmp_path, monkeypatch, [(P1P, ip)], answers)
    assert asked[0].startswith(ip) and why in asked[0]
    assert asked[0].endswith("Use it anyway? [y/N]: ")
    assert any(why in line for line in logged), "the address was not pointed out in the listing"
    assert data["printer_ip"] == "192.168.1.77", "Enter (the default) must not accept a flagged address"


def test_a_flagged_address_can_still_be_chosen_on_purpose(tmp_path, monkeypatch):
    data, _, _, _ = _run_setup(tmp_path, monkeypatch, [(P1P, "8.8.8.8")], ["yes", "87654321"])
    assert data["printer_ip"] == "8.8.8.8"


@pytest.mark.parametrize("ip", ["0.0.0.0", "224.0.0.251", "::"])
def test_an_unusable_address_is_dropped(tmp_path, monkeypatch, ip):
    data, asked, logged, error = _run_setup(tmp_path, monkeypatch, [(P1P, ip)], [])
    assert data is None
    assert error is not None and error.exit_code == 2  # no printers found
    assert asked == []
    assert any("Skipping" in line and "cannot be a printer's address" in line for line in logged)


def test_choosing_a_flagged_one_of_several_asks_again(tmp_path, monkeypatch):
    services = [(P1P, "192.168.1.50"), (A1, "8.8.4.4")]
    answers = ["1", "n", "192.168.1.77", "01P00AMANUAL", "87654321", "P1S"]
    data, asked, _, _ = _run_setup(tmp_path, monkeypatch, services, answers)
    assert asked[:2] == ["Choice: ", "8.8.4.4 is not a local network address. Use it anyway? [y/N]: "]
    assert data["printer_ip"] == "192.168.1.77"


def test_choosing_a_local_one_of_several_needs_no_second_question(tmp_path, monkeypatch):
    services = [(P1P, "192.168.1.50"), (A1, "8.8.4.4")]
    data, asked, _, _ = _run_setup(tmp_path, monkeypatch, services, ["0", "87654321"])
    assert asked[0] == "Choice: "
    assert not any("Use" in question for question in asked)
    assert data["printer_ip"] == "192.168.1.50"


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("192.168.1.5", None),
        ("10.1.2.3", None),
        ("172.20.0.9", None),
        ("169.254.10.1", None),
        ("fe80::1%eth0", None),
        ("fd00::5", None),
        ("8.8.8.8", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("224.0.0.251", False),
        ("not-an-ip", False),
    ],
)
def test_discovered_address_problem(ip, expected):
    problem = wizard_mod._discovered_address_problem(ip)
    if expected is None:
        assert problem is None
    else:
        assert problem is not None and problem[0] is expected
