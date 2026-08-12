"""The --json error envelope must survive a logging layer that blows up.

Every ``--json`` run promises a parseable envelope on stdout (README "Built for AI
agents"). Two properties keep that promise on error paths:

1. ``emit_json_error(...)`` runs BEFORE the human-readable log line, so an exploding
   handler cannot pre-empt the envelope.
2. the log call goes through ``bambu_cli.logging_utils.safe_log_error``, which degrades
   to a bare stderr write instead of propagating.

Together those mean a broken handler still yields a valid envelope AND the normal
``BambuError`` for the failure, not a logging traceback. One case per swept module.
"""

from tests.bambu_test_base import *  # noqa: F401,F403

import json
import os
from argparse import Namespace

import pytest

from bambu_cli.errors import BambuError


def _broken_logger():
    log = MagicMock()
    log.error.side_effect = RuntimeError("handler exploded")
    return log


def _envelope(capsys):
    out = capsys.readouterr().out
    # Some paths print progress lines before failing; the envelope is the last document.
    start = out.index("{")
    return json.loads(out[start:])


def _camera_localhost_guard(args):
    from bambu_cli.commands import snapshot

    snapshot._require_localhost_streamer_url(args, "http://camera.example.com:1984/x", "/tmp/snap.jpg")


def _downloader_bad_output_dir(args):
    from bambu_cli.download.downloader import _cmd_download

    args.url = "https://example.com/model.stl"
    args.output = "-not-a-dir"
    _cmd_download(args)


def _validation_bad_max_mb(args):
    from bambu_cli.download.validation import _validate_max_download_mb_or_exit

    args.max_download_mb = 0
    _validate_max_download_mb_or_exit(args)


def _slice_invalid_filepath(args):
    from bambu_cli.slicer import cmd_slice

    args.file = "-invalid"
    args.list_settings = False
    cmd_slice(args)


def _finalize_slice_failed(args):
    from bambu_cli.slicer.output import _finalize_slice

    args.file = "model.stl"
    _finalize_slice(None, "/tmp/does-not-exist.3mf", args, "model.stl", False)


def _config_show_missing(args):
    from bambu_cli.setup_cmd.config_cmd import _cmd_config_show

    with patch("bambu_cli.setup_cmd.config_cmd._config_path", return_value="/nonexistent/config.json"):
        _cmd_config_show(args)


def _setup_headless(args):
    from bambu_cli.setup_cmd.wizard import _cmd_setup

    args.migrate_access_code = False
    args.printer_ip = None
    args.serial = None
    args.access_code_file = None
    args.access_code = None
    with patch("bambu_cli.setup_cmd.wizard.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        _cmd_setup(args)


@pytest.mark.parametrize(
    ("label", "invoke", "command", "failed_step"),
    [
        ("camera", _camera_localhost_guard, "snapshot", "validate"),
        ("downloader", _downloader_bad_output_dir, "download", "validate"),
        ("validation", _validation_bad_max_mb, "download", "validate"),
        ("slicer_cmd", _slice_invalid_filepath, "slice", "validate"),
        ("slicer_output", _finalize_slice_failed, "slice", "slicer"),
        ("config_cmd", _config_show_missing, "config", "config"),
        ("wizard", _setup_headless, "setup", "validate"),
    ],
)
def test_json_envelope_survives_logger_failure(label, invoke, command, failed_step, capsys):
    from bambu_cli import utils

    args = Namespace(json=True)
    broken = _broken_logger()
    utils._JSON_EMITTED = False
    with patch("bambu_cli.logging_utils._BACKEND", broken), pytest.raises(BambuError):
        invoke(args)

    payload = _envelope(capsys)
    assert payload["status"] == "error", label
    assert payload["command"] == command, label
    assert payload["failed_step"] == failed_step, label
    # The handler really did explode, and safe_log_error absorbed it.
    assert broken.error.called, label


def test_safe_log_error_falls_back_to_stderr(capsys):
    from bambu_cli.logging_utils import safe_log_error

    with patch("bambu_cli.logging_utils._BACKEND", _broken_logger()):
        safe_log_error("boom [not-markup]")
    assert "ERROR: boom [not-markup]" in capsys.readouterr().err


def test_safe_log_error_lets_keyboard_interrupt_propagate():
    from bambu_cli.logging_utils import safe_log_error

    log = MagicMock()
    log.error.side_effect = KeyboardInterrupt
    with patch("bambu_cli.logging_utils._BACKEND", log), pytest.raises(KeyboardInterrupt):
        safe_log_error("interrupt me")


def test_safe_log_error_survives_broken_stderr():
    """Both the handler and the stderr fallback failing must still not raise."""
    from bambu_cli.logging_utils import safe_log_error

    class _BrokenStream:
        def write(self, _data):
            raise OSError("stream closed")

        def flush(self):
            raise OSError("stream closed")

    with patch("bambu_cli.logging_utils._BACKEND", _broken_logger()), patch("sys.stderr", _BrokenStream()):
        safe_log_error("nowhere to go")


def test_cli_safe_log_error_delegates_to_shared_helper():
    """cli._safe_log_error is kept as an alias; it must reach the lifted implementation."""
    from bambu_cli import cli

    broken = _broken_logger()
    with patch("bambu_cli.logging_utils._BACKEND", broken):
        cli._safe_log_error("still fine")
    assert broken.error.called


def test_no_local_artifacts_left_behind():
    """Guard: the parametrized cases must not write files into the repo."""
    assert not os.path.exists("-not-a-dir")
