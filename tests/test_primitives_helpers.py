"""Unit tests for rank-10/20 helpers that were only hit indirectly."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from bambu_cli.argutils import exit_code_from_system_exit, namespace_get, setup_args_provided
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_SUCCESS
from bambu_cli.errors import BambuError
from bambu_cli.job.support import _exit_code_from_error, _last_error_for
from bambu_cli.logging_utils import patched_logger, reset_logger, set_logger
from bambu_cli.paths import display_path, exception_for_message, expand_path, path_for_message


def test_namespace_get_reads_vars_and_falls_back_on_typeerror():
    assert namespace_get(Namespace(serial="SN"), "serial") == "SN"
    assert namespace_get(Namespace(), "missing", "d") == "d"

    class _NoVars:
        __slots__ = ()

    assert namespace_get(_NoVars(), "serial", "fallback") == "fallback"


def test_exit_code_from_system_exit_normalizes_shapes():
    assert exit_code_from_system_exit(SimpleNamespace(exit_code=6)) == 6
    assert exit_code_from_system_exit(SimpleNamespace(code=3)) == 3
    assert exit_code_from_system_exit(SimpleNamespace(code=None)) == EXIT_SUCCESS
    assert exit_code_from_system_exit(SimpleNamespace(code="nope")) == EXIT_COMMAND_ERROR


def test_setup_args_provided_any_setup_field():
    assert setup_args_provided(Namespace()) is False
    assert setup_args_provided(Namespace(printer_ip="1.2.3.4")) is True
    assert setup_args_provided(Namespace(model="P1S")) is True


def test_expand_and_display_path_none_and_home(monkeypatch, tmp_path):
    assert expand_path(None) is None
    assert display_path(None) is None
    assert path_for_message(None) is None

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    import bambu_cli.paths as paths_mod

    paths_mod._HOME_DIR = str(home)
    paths_mod._NORM_HOME_DIR = None
    assert display_path(str(home)) == "~"
    nested = home / "models" / "cube.stl"
    assert display_path(str(nested)).startswith("~")


def test_exception_for_message_compacts_filename_attrs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    import bambu_cli.paths as paths_mod

    paths_mod._HOME_DIR = str(home)
    paths_mod._NORM_HOME_DIR = None
    target = home / "secret.stl"
    err = OSError("cannot open")
    err.filename = str(target)
    compacted = exception_for_message(err)
    assert "secret.stl" in compacted
    assert str(home) not in compacted or compacted.startswith("~") or "~" in compacted


def test_job_support_exit_code_and_last_error():
    assert _exit_code_from_error(SimpleNamespace(exit_code=6)) == 6
    assert _exit_code_from_error(SimpleNamespace(code=2)) == 2
    assert _exit_code_from_error(SimpleNamespace(code=None)) == 0
    assert _exit_code_from_error(SimpleNamespace(code="x")) == EXIT_COMMAND_ERROR

    exc = BambuError("partial", failed_step="status", detail={"missing_keys": ["gcode_state"]})
    payload = _last_error_for("status", exc=exc)
    assert payload["command"] == "status"
    assert payload["failed_step"] == "status"
    assert payload["detail"]["missing_keys"] == ["gcode_state"]


def test_logger_set_reset_and_patched_roundtrip():
    class _Backend:
        def __init__(self):
            self.seen = []

        def error(self, message, **kwargs):
            self.seen.append(message)

    custom = _Backend()
    set_logger(custom)
    try:
        from bambu_cli.logging_utils import logger

        logger.error("one")
        assert custom.seen == ["one"]
    finally:
        reset_logger()

    with patched_logger(custom) as installed:
        assert installed is custom
        from bambu_cli.logging_utils import logger as proxy

        proxy.error("two")
    assert custom.seen[-1] == "two"
