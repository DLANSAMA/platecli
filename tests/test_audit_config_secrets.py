"""Regression tests for the config/secrets hardening deep audit.

Each test corresponds to a confirmed audit finding; every one was
sabotage-verified (revert the fix -> the test fails).
"""

import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

import bambu_cli.config as config
import bambu_cli.setup_cmd as setup_cmd
from bambu_cli import context
from bambu_cli.errors import BambuError
from bambu_cli.setup_cmd import wizard as wizard_mod


# --- Finding 4: insecure_tls strict, fail-CLOSED coercion --------------------


class TestInsecureTlsCoercion(unittest.TestCase):
    def test_json_string_false_does_not_disable_tls(self):
        # A hand-edited "insecure_tls": "false" is a truthy str; it must NOT
        # disable TLS validation (fail-open). Fail closed instead.
        s = context.Settings.from_config({"insecure_tls": "false"})
        self.assertIs(s.insecure_tls, False)

    def test_json_string_no_and_zero_stay_closed(self):
        for val in ("no", "0", "off", ""):
            self.assertIs(context.Settings.from_config({"insecure_tls": val}).insecure_tls, False)

    def test_bool_true_still_enables(self):
        self.assertIs(context.Settings.from_config({"insecure_tls": True}).insecure_tls, True)

    def test_string_true_spellings_enable(self):
        for val in ("true", "1", "yes", "on", "True", " TRUE "):
            self.assertIs(context.Settings.from_config({"insecure_tls": val}).insecure_tls, True)

    def test_non_bool_type_stays_closed_and_warns(self):
        with patch.object(config.logger, "warning") as mock_warn:
            s = context.Settings.from_config({"insecure_tls": 5})
        self.assertIs(s.insecure_tls, False)
        self.assertTrue(mock_warn.called)


# --- Finding 5: chmod failure degrades to a warning, still reads config ------


@unittest.skipIf(os.name == "nt", "POSIX permission enforcement only")
class TestChmodFailureDegradesToWarning(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"printer_ip": "127.0.0.1", "serial": "MOCK", "access_code": "SECRET123"}, f)
        os.chmod(self.config_path, 0o644)  # world-readable -> triggers the chmod path

    def test_chmod_failure_warns_and_still_loads(self):
        with (
            patch.object(config, "CONFIG_PATH", self.config_path),
            patch("os.chmod", side_effect=OSError("EPERM")),
        ):
            with self.assertLogs("bambu", level="WARNING") as cm:
                cfg = config.load_config(exit_on_fail=True)
        # The config is readable, so it must load rather than abort.
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["serial"], "MOCK")
        joined = "\n".join(cm.output)
        self.assertIn("Could not tighten permissions", joined)


# --- Finding 1: migration leaves no plaintext-secret .bak --------------------


class TestMigrationNoSecretBackup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        self.access_code_file = os.path.join(self.tmpdir, "access_code")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"printer_ip": "127.0.0.1", "serial": "MOCK", "access_code": "SECRET123"}, f)

    def test_no_bak_with_inline_secret_after_migration(self):
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "migrated")
        bak = self.config_path + ".bak"
        # The whole point of migration is to get the secret out of config.json;
        # a config.json.bak holding the plaintext access_code would defeat it.
        if os.path.exists(bak):
            with open(bak, encoding="utf-8") as f:
                self.assertNotIn("SECRET123", f.read())


# --- Finding 7: migration is idempotent / retryable across its two writes -----


class TestMigrationRetryable(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        self.access_code_file = os.path.join(self.tmpdir, "access_code")

    def _write_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"printer_ip": "127.0.0.1", "serial": "MOCK", "access_code": "SECRET123"}, f)

    def test_config_write_failure_cleans_up_orphan_secret_file(self):
        self._write_config()
        with patch(
            "bambu_cli.setup_cmd.migrate._secure_write_json_no_secret_backup",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                setup_cmd.migrate_access_code(
                    config_path=self.config_path,
                    access_code_file_path=self.access_code_file,
                )
        # The just-created secret file must be removed so a retry is not wedged
        # behind "target already exists".
        self.assertFalse(os.path.exists(self.access_code_file))

    def test_retry_tolerates_identical_pre_existing_secret_file(self):
        # A prior attempt wrote the secret file, then the config write failed.
        self._write_config()
        with open(self.access_code_file, "w", encoding="utf-8") as f:
            f.write("SECRET123\n")
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "migrated")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertNotIn("access_code", cfg)
        self.assertEqual(cfg["access_code_file"], self.access_code_file)

    def test_error_when_target_exists_with_different_contents(self):
        self._write_config()
        with open(self.access_code_file, "w", encoding="utf-8") as f:
            f.write("SOMETHING_ELSE\n")
        result = setup_cmd.migrate_access_code(
            config_path=self.config_path,
            access_code_file_path=self.access_code_file,
        )
        self.assertEqual(result["status"], "error")
        # Config untouched on error.
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["access_code"], "SECRET123")


# --- Findings 3 & 9: BOM-tolerant config reads --------------------------------


class TestBomTolerantConfigReads(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        # Write WITH a UTF-8 BOM, as a Windows editor would.
        with open(self.config_path, "w", encoding="utf-8-sig") as f:
            json.dump({"printer_ip": "127.0.0.1", "serial": "MOCK", "access_code": "SECRET123"}, f)

    def test_read_config_json_tolerates_bom(self):
        cfg = config.read_config_json(self.config_path)
        self.assertEqual(cfg["serial"], "MOCK")

    def test_migrate_tolerates_bom(self):
        acf = os.path.join(self.tmpdir, "access_code")
        result = setup_cmd.migrate_access_code(config_path=self.config_path, access_code_file_path=acf)
        self.assertEqual(result["status"], "migrated")


# --- Findings 2 & 4: preflight warnings for config conflicts ------------------


class TestPreflightWarnings(unittest.TestCase):
    def _run_preflight(self, cfg):
        with (
            patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
            patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
            patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
            patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value=None),
            patch("os.path.isdir", return_value=True),
            patch("os.path.exists", return_value=True),
            patch("shutil.which", return_value=None),
        ):
            return setup_cmd.collect_preflight_checks()

    def test_non_boolean_insecure_tls_warns(self):
        checks = self._run_preflight(
            {"printer_ip": "127.0.0.1", "serial": "MOCK", "access_code": "SECRET123", "insecure_tls": "false"}
        )
        tls = [c for c in checks if c["name"] == "insecure-tls"]
        self.assertEqual(len(tls), 1)
        self.assertEqual(tls[0]["status"], "warning")

    def test_inline_alongside_file_warns(self):
        import io

        with (
            patch("bambu_cli.setup_cmd.preflight.load_config", return_value={
                "printer_ip": "127.0.0.1",
                "serial": "MOCK",
                "access_code": "STALE",
                "access_code_file": "/tmp/access_code",
            }),
            patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
            patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
            patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value=None),
            patch("os.path.isdir", return_value=True),
            patch("os.path.exists", return_value=True),
            patch("shutil.which", return_value=None),
            patch("builtins.open", side_effect=lambda *a, **k: io.StringIO("FRESH_FILE_CODE\n")),
        ):
            checks = setup_cmd.collect_preflight_checks()
        conflict = [c for c in checks if c["name"] == "access-code-inline-conflict"]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(conflict[0]["status"], "warning")
        self.assertNotIn("STALE", conflict[0]["message"])


# --- Finding 6: interactive wizard rejects an empty access code ---------------


class TestInteractiveEmptyAccessCode(unittest.TestCase):
    def test_empty_input_is_rejected(self):
        args = Namespace(json=False)
        with patch.object(wizard_mod, "_prompt_secret", return_value="   "):
            with self.assertRaises(BambuError):
                wizard_mod._prompt_interactive_access_code(args, max_attempts=2)

    def test_reprompts_then_accepts_valid(self):
        args = Namespace(json=False)
        with patch.object(wizard_mod, "_prompt_secret", side_effect=["", "REAL_CODE"]):
            code = wizard_mod._prompt_interactive_access_code(args, max_attempts=3)
        self.assertEqual(code, "REAL_CODE")


# --- Finding 8: setup refuses to clobber an existing differing secret file ----


class TestSetupBothFlagsNoClobber(unittest.TestCase):
    def _args(self, tmp_path, code_file, force=False):
        return Namespace(
            printer_ip="10.0.0.4",
            serial="SNCLOBBER01",
            access_code="NEW_CODE",
            access_code_file=str(code_file),
            access_code_env=None,
            config=str(tmp_path / "c.json"),
            model="A1",
            nozzle="0.4",
            orca_slicer="/bin/true",
            profiles_dir=str(tmp_path),
            json=True,
            cert_fingerprint=None,
            insecure_tls=False,
            force=force,
        )

    def test_refuses_to_overwrite_existing_differing_file(self):
        tmpdir = tempfile.mkdtemp()
        import pathlib

        tmp_path = pathlib.Path(tmpdir)
        code_file = tmp_path / "shared_secret"
        code_file.write_text("OTHER_PRINTER_CODE\n", encoding="utf-8")
        cfg = tmp_path / "c.json"
        args = self._args(tmp_path, code_file, force=False)
        with (
            patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)),
            patch("bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)),
        ):
            with self.assertRaises(BambuError):
                wizard_mod._cmd_setup_noninteractive(args)
        # The pre-existing secret must be left intact.
        self.assertEqual(code_file.read_text(encoding="utf-8").strip(), "OTHER_PRINTER_CODE")

    def test_force_overwrites(self):
        tmpdir = tempfile.mkdtemp()
        import pathlib

        tmp_path = pathlib.Path(tmpdir)
        code_file = tmp_path / "shared_secret"
        code_file.write_text("OTHER_PRINTER_CODE\n", encoding="utf-8")
        cfg = tmp_path / "c.json"
        args = self._args(tmp_path, code_file, force=True)
        with (
            patch("bambu_cli.setup_cmd.wizard._config_path", return_value=str(cfg)),
            patch("bambu_cli.setup_cmd.common._config_path", return_value=str(cfg)),
        ):
            wizard_mod._cmd_setup_noninteractive(args)
        self.assertEqual(code_file.read_text(encoding="utf-8").strip(), "NEW_CODE")


if __name__ == "__main__":
    unittest.main()
