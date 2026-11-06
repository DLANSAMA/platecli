"""A doctor run that fails early must not leave an empty capabilities file behind."""

import glob
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bambu_cli.errors import BambuError, ConfigError


def test_failed_doctor_leaves_no_temp_file(tmp_path, monkeypatch):
    from bambu_cli.commands import cmd_doctor

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with patch("bambu_cli.commands.doctor.load_config", side_effect=ConfigError("nope")):
        with pytest.raises(BambuError):
            cmd_doctor(SimpleNamespace(json=False, output=None))
    assert glob.glob(str(tmp_path / "printer_capabilities_*")) == []
