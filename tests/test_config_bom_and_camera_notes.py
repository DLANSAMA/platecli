"""Regressions found during the 2026-07-25 Windows validation pass.

1. A UTF-8 BOM on config.json (what PowerShell/Notepad write by default) made
   every command report the config as *missing*, sending the user to `setup`,
   which would overwrite the file they were trying to repair.
2. preflight told P1-series owners they needed Docker for camera snapshots,
   which is false — P1/A1 capture directly over TLS port 6000. (The matching
   `doctor` note was corrected upstream; this is the remaining copy.)
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pytest

from bambu_cli import config as config_mod
from bambu_cli import setup_cmd
from bambu_cli.commands.doctor import _DIRECT_CAMERA_MODELS
from bambu_cli.errors import BambuError
from tests.bambu_test_base import settings_ctx

# Deliberately non-real values: 127.0.0.1 and CONTRACTTEST* mirror the fixture
# convention in tests/contracts/. Never put a real printer address, serial, or
# access code in a committed test.
_CFG = {
    "printer_ip": "127.0.0.1",
    "serial": "CONTRACTTESTSERIAL",
    "access_code": "CONTRACTTESTCODE",
}


class TestConfigBom(unittest.TestCase):
    def _write(self, encoding, payload=None):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding=encoding) as f:
            f.write(json.dumps(payload if payload is not None else _CFG))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_bom_config_loads_identically_to_plain_utf8(self):
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                path = self._write(encoding)
                with patch.object(config_mod, "CONFIG_PATH", path):
                    cfg = config_mod.load_config(exit_on_fail=False)
                self.assertIsNotNone(cfg, f"{encoding} config failed to load")
                self.assertEqual(cfg["printer_ip"], "127.0.0.1")

    def test_bom_config_is_byte_prefixed_as_assumed(self):
        # Guard the premise: if this stops writing a BOM the test above is vacuous.
        path = self._write("utf-8-sig")
        with open(path, "rb") as f:
            self.assertEqual(f.read(3), b"\xef\xbb\xbf")

    def test_malformed_config_is_not_reported_as_missing(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        # exit_on_fail=False still returns None — `preflight` needs to report
        # this rather than die on it — but the parse failure must be logged
        # out loud, so no caller can silently downgrade it to "not configured"
        # and send the user to `setup`, which would overwrite the file.
        with patch.object(config_mod, "CONFIG_PATH", path):
            with patch("bambu_cli.logging_utils._BACKEND") as mock_logger:
                self.assertIsNone(config_mod.load_config(exit_on_fail=False))
        logged = " ".join(str(c[0][0]) for c in mock_logger.error.call_args_list)
        self.assertIn("not valid JSON", logged)
        self.assertNotIn("not configured", logged)

    def test_malformed_config_aborts_when_exit_on_fail(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        with patch.object(config_mod, "CONFIG_PATH", path):
            with pytest.raises((BambuError, SystemExit)):
                config_mod.load_config()


class TestPreflightDockerCheck(unittest.TestCase):
    """P1/A1 capture directly over TLS port 6000 and need no Docker, so a
    missing Docker must not be reported as "snapshots unavailable" for them.
    Verified on hardware: a P1P produced a 104 KB JPEG with no Docker present.
    """

    def _docker_check(self, model):
        cfg = {"printer_ip": "127.0.0.1", "serial": "SN", "access_code": "x"}
        with (
            settings_ctx(printer_model=model),
            patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
            patch("shutil.which", return_value=None),  # no docker on PATH
        ):
            checks = setup_cmd.collect_preflight_checks()
        return [c for c in checks if c["name"] == "docker"][0]

    def test_direct_capture_models_are_not_warned(self):
        for model in _DIRECT_CAMERA_MODELS:
            with self.subTest(model=model):
                check = self._docker_check(model)
                self.assertEqual(check["status"], "ok")
                self.assertNotIn("will be unavailable", check["message"])

    def test_x1_series_still_warns(self):
        check = self._docker_check("X1C")
        self.assertEqual(check["status"], "warning")
        self.assertIn("BambuP1Streamer", check["message"])


if __name__ == "__main__":
    unittest.main()
