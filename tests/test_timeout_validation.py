"""Timeouts must be positive, finite numbers of seconds.

Before the 2026-09-22 fix ``--command-timeout``/``--network-timeout``/
``--upload-timeout``/``--slicer-timeout`` (and ``--scan-timeout``) accepted
``-5``, ``0``, ``nan`` and ``inf``, and config.json values were passed to
``float()`` unchecked. ``nan``/``inf`` meant the slicer never timed out (every
comparison with NaN is False); a negative value made sockets raise
ValueError deep inside a transfer; a non-numeric config value raised an
uncaught ValueError before any command handler ran.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from bambu_cli.cliparse import build_parser

FLAGS = ["--network-timeout", "--slicer-timeout", "--command-timeout", "--upload-timeout"]


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("value", ["-5", "0", "nan", "inf", "-inf", "abc"])
def test_bad_timeout_flags_are_parse_errors(flag, value, capsys):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["status", flag, value])
    assert caught.value.code == 5
    err = capsys.readouterr().err
    # "abc" fails float() and "-inf" reads as an unknown option to argparse;
    # both were already parse errors. The rest parsed as floats before the fix.
    assert "positive number of seconds" in err or value in ("abc", "-inf")


@pytest.mark.parametrize("flag", FLAGS)
def test_good_timeout_flags_parse(flag):
    ns = build_parser().parse_args(["status", flag, "2.5"])
    assert getattr(ns, flag.lstrip("-").replace("-", "_")) == 2.5


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf"])
def test_bad_scan_timeout_is_a_parse_error(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["setup", "--scan-timeout", value])


@pytest.mark.parametrize("bad", ["abc", -3, 0, "nan", "inf", [5], True])
def test_bad_config_timeout_falls_back_to_the_default(bad):
    from bambu_cli import config
    from bambu_cli.constants import COMMAND_TIMEOUT, SLICER_TIMEOUT
    from tests.bambu_test_base import settings_ctx

    with settings_ctx():
        from bambu_cli.context import get_current

        ctx = get_current()
        saved = ctx.config
        ctx.config = {"command_timeout": bad, "slicer_timeout": bad}
        try:
            assert config.get_command_timeout(argparse.Namespace()) == COMMAND_TIMEOUT
            assert config.get_slicer_timeout(None) == SLICER_TIMEOUT
        finally:
            ctx.config = saved


def test_good_config_timeout_is_used():
    from bambu_cli import config
    from tests.bambu_test_base import settings_ctx

    with settings_ctx():
        from bambu_cli.context import get_current

        ctx = get_current()
        saved = ctx.config
        ctx.config = {"upload_timeout": "600"}
        try:
            assert config.get_upload_timeout(argparse.Namespace()) == 600.0
        finally:
            ctx.config = saved


def test_config_validate_reports_bad_timeouts(tmp_path, monkeypatch):
    from bambu_cli import config as config_mod
    from bambu_cli.setup_cmd import common as common_mod
    from bambu_cli.setup_cmd.preflight import collect_preflight_checks

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "printer_ip": "192.0.2.10",
                "serial": "01S00A000000000",
                "model": "P1P",
                "access_code": "12345678",
                "network_timeout": "fast",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(cfg_path, 0o600)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    checks = {check["name"]: check for check in collect_preflight_checks()}
    assert checks["timeouts"]["status"] == "error"
    assert "network_timeout" in checks["timeouts"]["message"]
