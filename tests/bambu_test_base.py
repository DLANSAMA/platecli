"""Shared setup for the split test_bambu suite.

Import-time side effects (paho-mqtt mock, isolated temp config) must run
before bambu_cli.bambu is imported, so every test module in this suite
starts with `from tests.bambu_test_base import *`.
"""

import unittest
import sys
import io
import json
import os
import platform
from unittest.mock import patch, MagicMock, mock_open, ANY

# Capture the host platform once, before any test-time mocking is active.
# cmd_slice calls platform.system(); on Python 3.8 that eagerly shells out to
# `uname` (for the processor field) via subprocess, which collides with the
# subprocess.Popen mock in the slice tests and raises a spurious ValueError.
# Patching platform.system to this constant keeps the real code branch while
# eliminating the subprocess call. (Python 3.9+ makes the field lazy, so the
# failure only reproduces on 3.8 when platform._uname_cache is cold.)
_HOST_SYSTEM = platform.system()

# Mock paho-mqtt before importing bambu_cli.bambu
mock_mqtt = MagicMock()
sys.modules["paho"] = mock_mqtt
sys.modules["paho.mqtt"] = mock_mqtt
sys.modules["paho.mqtt.client"] = mock_mqtt

# Setup global isolated mock config
import tempfile
import atexit
import shutil

mock_config_dir = tempfile.mkdtemp()
mock_config_path = os.path.join(mock_config_dir, "config.json")
_BASELINE_CONFIG = {
    "printer_ip": "127.0.0.1",
    "serial": "MOCK_SERIAL",
    "access_code": "MOCK_CODE",
    "orca_slicer": "/tmp/mock_orca",
    "profiles_dir": "/tmp/mock_profiles",
}
with open(mock_config_path, "w", encoding="utf-8") as f:
    json.dump(_BASELINE_CONFIG, f)

import bambu_cli.bambu as bambu
import bambu_cli.config as _config_mod

# Pin the isolated temp config path used by every test (production reads
# bambu_cli.config.CONFIG_PATH directly; do not set bambu.CONFIG_PATH).
_config_mod.CONFIG_PATH = mock_config_path
bambu.CONFIG_PATH = mock_config_path  # legacy attr for any remaining readers

from bambu_cli import context as _context
from bambu_cli.context import RuntimeContext as _RuntimeContext
from bambu_cli.context import Settings as _Settings


def install_baseline_context():
    """Install the RuntimeContext every test starts from (mirrors the mock config).

    Config state now lives on the process-wide RuntimeContext rather than
    module globals, so tests establish (and reset to) this baseline instead of
    relying on globals that persist across tests.
    """
    _context.set_current(
        _RuntimeContext(
            settings=_Settings.from_config(_BASELINE_CONFIG),
            config=dict(_BASELINE_CONFIG),
        )
    )


_config_mod.load_config(exit_on_fail=False)
install_baseline_context()

import contextlib as _contextlib
from dataclasses import replace as _replace


@_contextlib.contextmanager
def config_ctx(cfg):
    """Temporarily install a RuntimeContext built from the given raw config dict.

    Replaces the old ``patch.dict(bambu._cfg, ...)`` idiom now that config
    state lives on the RuntimeContext.
    """
    prev = _context.get_current()
    try:
        _context.set_current(_RuntimeContext(settings=_Settings.from_config(cfg), config=dict(cfg)))
        yield
    finally:
        _context.set_current(prev)


@_contextlib.contextmanager
def settings_ctx(simulation=None, **setting_overrides):
    """Temporarily install a RuntimeContext with specific Settings overrides.

    Replaces the old ``patch('bambu_cli.bambu.PROFILES_DIR', ...)`` idiom.
    ``simulation`` overrides the sim flag; keyword args override Settings fields.
    """
    prev = _context.get_current()
    new_settings = _replace(prev.settings, **setting_overrides) if setting_overrides else prev.settings
    sim = prev.simulation if simulation is None else simulation
    try:
        _context.set_current(_RuntimeContext(settings=new_settings, config=prev.config, simulation=sim))
        yield
    finally:
        _context.set_current(prev)


def cleanup_mock_config():
    shutil.rmtree(mock_config_dir, ignore_errors=True)


atexit.register(cleanup_mock_config)

try:
    from bambu_cli.cli import setup_logging
    from bambu_cli.commands import cmd_stop, cmd_light
    from bambu_cli.config import load_config
    from bambu_cli.protocols.ftps import get_ftp
    from bambu_cli.protocols.mqtt import create_mqtt_client, execute_print_command
    import ssl
    import urllib.error

    setup_logging(verbose=True)
except ImportError:
    pass

import socket

from bambu_cli.printer import BambuPrinter
from bambu_cli.errors import BambuError


def _test_printer(ip="192.168.1.1", serial=None, access_code="MOCK_CODE", **kwargs):
    """Build a BambuPrinter matching the mocked global config for direct protocol calls."""
    return BambuPrinter(ip=ip, serial=serial or _context.current_settings().serial, access_code=access_code, **kwargs)


def _setup_slice_proc(mock_proc, returncode=0, stdout=b"", stderr=b""):
    """Configure a mock Popen process for cmd_slice's reader-thread loop.

    cmd_slice now reads process.stdout/stderr with read1() in pump threads,
    so the fakes must expose real byte streams plus poll()/wait()/returncode.
    """
    mock_proc.stdout = io.BytesIO(stdout)
    mock_proc.stderr = io.BytesIO(stderr)
    mock_proc.poll.return_value = returncode
    mock_proc.wait.return_value = returncode
    mock_proc.returncode = returncode
    return mock_proc


__all__ = [
    name
    for name in (
        "BambuError",
        "unittest",
        "sys",
        "io",
        "json",
        "os",
        "platform",
        "patch",
        "MagicMock",
        "mock_open",
        "ANY",
        "_HOST_SYSTEM",
        "mock_mqtt",
        "tempfile",
        "atexit",
        "shutil",
        "mock_config_dir",
        "mock_config_path",
        "bambu",
        "cmd_stop",
        "get_ftp",
        "load_config",
        "create_mqtt_client",
        "cmd_light",
        "execute_print_command",
        "setup_logging",
        "ssl",
        "socket",
        "urllib",
        "BambuPrinter",
        "_test_printer",
        "_setup_slice_proc",
        "install_baseline_context",
        "config_ctx",
        "settings_ctx",
    )
    if name in globals()
]
