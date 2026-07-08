"""Tests for the inline access_code deprecation path (P2 security item):

- one-time-per-process runtime warning when access_code is inline and no
  access_code_file is set
- no warning when access_code_file is used, or when there is no config
- preflight warning-severity check surfaces the same remediation hint
- the --migrate-access-code helper moves an inline code into a file
"""
import json
import os
import stat
import unittest
from unittest.mock import patch

import bambu_cli.config as config
import bambu_cli.setup_cmd as setup_cmd
from bambu_cli import bambu


class ResetWarnFlagMixin:
    def setUp(self):
        super().setUp()
        self._orig_warned = config._INLINE_ACCESS_CODE_WARNED
        config._INLINE_ACCESS_CODE_WARNED = False
        self._orig_cfg = dict(bambu._cfg) if bambu._cfg else {}

    def tearDown(self):
        config._INLINE_ACCESS_CODE_WARNED = self._orig_warned
        bambu._cfg = self._orig_cfg
        super().tearDown()


class TestInlineAccessCodeWarning(ResetWarnFlagMixin, unittest.TestCase):
    def test_warns_once_when_inline_only(self):
        bambu._cfg = {"access_code": "SECRET123"}
        with self.assertLogs("bambu", level="WARNING") as cm:
            config.load_access_code()
            config.load_access_code()
        warnings = [line for line in cm.output if "inline access_code" in line]
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("SECRET123", "\n".join(cm.output))

    def test_no_warning_when_access_code_file_used(self):
        bambu._cfg = {"access_code_file_marker": True}
        with patch("bambu_cli.cli._expand_path", return_value="/tmp/does-not-matter"), \
             patch("builtins.open", side_effect=lambda *a, **k: _fake_file("filecode\n")):
            bambu._cfg = {"access_code_file": "/tmp/does-not-matter"}
            # assertNoLogs needs Python 3.10+; assert via the logger mock instead.
            with patch.object(config.logger, "warning") as mock_warn:
                config.load_access_code()
            mock_warn.assert_not_called()

    def test_no_warning_when_both_inline_and_file_present(self):
        # access_code branch is checked first, but if access_code_file is also
        # configured we should not nag about migrating (nothing to migrate to).
        bambu._cfg = {"access_code": "SECRET123", "access_code_file": "/tmp/somewhere"}
        with patch.object(config.logger, "warning") as mock_warn:
            config.load_access_code()
        mock_warn.assert_not_called()

    def test_no_warning_with_no_config(self):
        # Simulation / no-config: _cfg empty, neither key present -> error path,
        # not the deprecation warning.
        bambu._cfg = {}
        with self.assertRaises(SystemExit):
            with patch.object(config.logger, "warning") as mock_warn:
                config.load_access_code()
        mock_warn.assert_not_called()


def _fake_file(contents):
    import io
    return io.StringIO(contents)


class TestPreflightAccessCodeCheck(unittest.TestCase):
    def test_inline_access_code_warning_shape(self):
        cfg = {
            "printer_ip": "127.0.0.1",
            "serial": "MOCK",
            "access_code": "SECRET123",
        }
        with patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg), \
             patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"), \
             patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p), \
             patch("bambu_cli.slicer._slicer_executable_problem", return_value=None), \
             patch("os.path.isdir", return_value=True), \
             patch("shutil.which", return_value=None):
            checks = setup_cmd.collect_preflight_checks()
        access_checks = [c for c in checks if c["name"] == "access-code"]
        self.assertEqual(len(access_checks), 1)
        check = access_checks[0]
        self.assertEqual(check["status"], "warning")
        self.assertIn("status", check)
        self.assertIn("name", check)
        self.assertIn("message", check)
        self.assertIn("inline access_code", check["message"])
        self.assertIn("migrate-access-code", check["message"])
        self.assertNotIn("SECRET123", check["message"])


class TestMigrateAccessCode(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        self.access_code_file = os.path.join(self.tmpdir, "access_code")

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_happy_path_moves_inline_code_to_file(self):
        self._write_config({
            "printer_ip": "127.0.0.1",
            "serial": "MOCK",
            "access_code": "SECRET123",
        })
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "migrated")

        with open(self.config_path, encoding="utf-8") as f:
            new_cfg = json.load(f)
        self.assertNotIn("access_code", new_cfg)
        self.assertEqual(new_cfg["access_code_file"], self.access_code_file)

        with open(self.access_code_file, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "SECRET123")

        if os.name != "nt":  # chmod(0o600) can't restrict group/other bits on Windows
            mode = stat.S_IMODE(os.stat(self.access_code_file).st_mode)
            self.assertEqual(mode, 0o600)

    def test_noop_when_no_inline_code_present(self):
        self._write_config({
            "printer_ip": "127.0.0.1",
            "serial": "MOCK",
        })
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "noop")
        self.assertFalse(os.path.exists(self.access_code_file))
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertNotIn("access_code_file", cfg)

    def test_noop_when_access_code_file_already_set(self):
        existing_file = os.path.join(self.tmpdir, "existing_secret")
        with open(existing_file, "w", encoding="utf-8") as f:
            f.write("already-there\n")
        self._write_config({
            "printer_ip": "127.0.0.1",
            "serial": "MOCK",
            "access_code": "SECRET123",
            "access_code_file": existing_file,
        })
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "noop")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        # Left untouched: still has the inline code + original file reference.
        self.assertEqual(cfg["access_code"], "SECRET123")
        self.assertEqual(cfg["access_code_file"], existing_file)

    def test_error_when_target_file_already_exists(self):
        self._write_config({
            "printer_ip": "127.0.0.1",
            "serial": "MOCK",
            "access_code": "SECRET123",
        })
        with open(self.access_code_file, "w", encoding="utf-8") as f:
            f.write("pre-existing\n")
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "error")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        # Config must be untouched on error.
        self.assertEqual(cfg["access_code"], "SECRET123")
        self.assertNotIn("access_code_file", cfg)
        with open(self.access_code_file, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "pre-existing")


if __name__ == "__main__":
    unittest.main()
