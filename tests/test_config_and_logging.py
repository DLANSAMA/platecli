from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestLoadConfig(unittest.TestCase):
    @patch("bambu_cli.slicer.step_convert.subprocess.run")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    def test_cmd_slice_convert_step_to_stl_argument_injection(self, mock_getsize, mock_exists, mock_logger, mock_run):
        import os

        from bambu_cli.slicer import _convert_step_to_stl

        # Setup mocks
        mock_run.return_value.returncode = 0
        mock_exists.return_value = True
        mock_getsize.return_value = 1024

        filepath = "-malicious.step"
        abs_filepath = os.path.abspath(filepath)
        expected_stl_path = abs_filepath.rsplit(".", 1)[0] + ".stl"

        res_filepath, success = _convert_step_to_stl(filepath)

        self.assertTrue(success)
        self.assertTrue(res_filepath.endswith("-malicious_.stl"))
        self.assertIn("bambu_step_", res_filepath)

        # Verify that subprocess.run was called with absolute paths
        self.assertEqual(mock_run.call_count, 1)
        args_run, kwargs_run = mock_run.call_args
        cmd_run = args_run[0]
        self.assertIn("gmsh", cmd_run)
        self.assertIn(abs_filepath, cmd_run)
        self.assertIn("-o", cmd_run)
        out_idx = cmd_run.index("-o") + 1
        self.assertTrue(cmd_run[out_idx].endswith("-malicious_.stl"))
        self.assertIn("bambu_step_", cmd_run[out_idx])
        self.assertEqual(kwargs_run, {"capture_output": True, "text": True, "timeout": 60})

    @patch("os.path.exists")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_load_config_not_found(self, mock_logger, mock_exists):
        mock_exists.return_value = False

        with self.assertRaises((SystemExit, BambuError)) as cm:
            load_config()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        pass  # domain code raises BambuError; process exit is main()'s job
        # Check if instructions were logged
        self.assertTrue(any("Config not found" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch("os.stat")
    @patch("os.path.exists")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    @patch("builtins.open", new_callable=mock_open, read_data="invalid json")
    def test_load_config_invalid_json(self, mock_file, mock_exit, mock_logger, mock_exists, mock_stat):
        mock_exists.return_value = True

        with self.assertRaises((SystemExit, BambuError)) as cm:
            load_config()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        self.assertTrue(any("Error loading config" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch("os.path.exists")
    def test_load_config_not_found_no_exit(self, mock_exists):
        from bambu_cli.config import load_config

        mock_exists.return_value = False
        result = load_config(exit_on_fail=False)
        self.assertIsNone(result)

    @patch("os.stat")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="invalid json")
    def test_load_config_invalid_json_no_exit(self, mock_file, mock_exists, mock_stat):
        from bambu_cli.config import load_config

        mock_exists.return_value = True
        result = load_config(exit_on_fail=False)
        self.assertIsNone(result)


class TestLoadAccessCode(unittest.TestCase):
    def test_load_access_code_inline(self):
        import bambu_cli.bambu

        with config_ctx({"access_code": "inline_secret"}):
            self.assertEqual(bambu_cli.config.load_access_code(), "inline_secret")

    @patch("os.path.expanduser")
    @patch("builtins.open", new_callable=mock_open, read_data=" file_secret ")
    def test_load_access_code_file(self, mock_file, mock_expanduser):
        from bambu_cli.config import load_access_code

        if hasattr(load_access_code, "cache_clear"):
            load_access_code.cache_clear()
        import bambu_cli.bambu

        with config_ctx({"access_code_file": "~/.config/bambu/secret"}):
            mock_expanduser.return_value = "/home/user/.config/bambu/secret"
            self.assertEqual(bambu_cli.config.load_access_code(), "file_secret")

    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("os.path.expanduser")
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_load_access_code_file_not_found(self, mock_file, mock_expanduser, mock_logger):
        from bambu_cli.config import load_access_code

        if hasattr(load_access_code, "cache_clear"):
            load_access_code.cache_clear()
        import bambu_cli.bambu

        with config_ctx({"access_code_file": "~/.config/bambu/missing"}):
            with self.assertRaises(BambuError) as cm:
                bambu_cli.config.load_access_code()
            self.assertEqual(cm.exception.exit_code, 1)
            self.assertTrue(
                any("Access code file not found" in call[0][0] for call in mock_logger.error.call_args_list)
            )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_load_access_code_missing(self, mock_logger):
        from bambu_cli.config import load_access_code

        if hasattr(load_access_code, "cache_clear"):
            load_access_code.cache_clear()
        import bambu_cli.bambu

        with config_ctx({}):
            with self.assertRaises(BambuError) as cm:
                bambu_cli.config.load_access_code()
            self.assertEqual(cm.exception.exit_code, 1)
            mock_logger.error.assert_called_with("No 'access_code' or 'access_code_file' in config.json")


class TestSetupLogging(unittest.TestCase):
    @patch("bambu_cli.cli.logging")
    @patch("bambu_cli.cli.sys")
    def test_setup_logging_default(self, mock_sys, mock_logging):
        import bambu_cli.cli as bambu_cli_module

        mock_root = MagicMock()
        mock_handler = MagicMock()
        mock_root.handlers = [mock_handler]

        def get_logger_side_effect(name=None):
            if name is None or name == "bambu_cli":
                return mock_root
            return MagicMock()

        mock_logging.getLogger.side_effect = get_logger_side_effect

        with patch.dict(
            "sys.modules", {"rich": None, "rich.logging": None, "rich.console": None, "rich.traceback": None}
        ):
            with patch("bambu_cli.cli.logger") as mock_logger:
                bambu_cli_module.setup_logging()

                # Check root handler removal
                mock_root.removeHandler.assert_called_once_with(mock_handler)

                # Check StreamHandler and Formatter are created
                mock_logging.StreamHandler.assert_called_once_with(mock_sys.stderr)
                mock_logging.Formatter.assert_called_once_with("%(levelname)s: %(message)s")

                # Check log level setting
                mock_logger.setLevel.assert_called_once_with(mock_logging.INFO)

                # Check propagate False
                self.assertFalse(mock_logger.propagate)

    @patch("bambu_cli.cli.logging")
    @patch("bambu_cli.cli.sys")
    def test_setup_logging_verbose(self, mock_sys, mock_logging):
        import bambu_cli.cli as bambu_cli_module

        mock_root = MagicMock()

        def get_logger_side_effect(name=None):
            if name is None or name == "bambu_cli":
                return mock_root
            return MagicMock()

        mock_logging.getLogger.side_effect = get_logger_side_effect

        with patch.dict(
            "sys.modules", {"rich": None, "rich.logging": None, "rich.console": None, "rich.traceback": None}
        ):
            with patch("bambu_cli.cli.logger") as mock_logger:
                bambu_cli_module.setup_logging(verbose=True)
                mock_logger.setLevel.assert_called_once_with(mock_logging.DEBUG)
                self.assertFalse(mock_logger.propagate)


class TestCmdConfig(unittest.TestCase):
    """`config show` / `config validate` (bambu_cli.setup_cmd.config_cmd)."""

    def _args(self, action, json_mode=False, strict=False):
        import argparse

        return argparse.Namespace(cmd="config", action=action, json=json_mode, strict=strict)

    def test_config_show_redacts_access_code(self):
        import contextlib

        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        stdout = io.StringIO()
        with patch("bambu_cli.setup_cmd.config_cmd.logger"), contextlib.redirect_stdout(stdout):
            _cmd_config(self._args("show"))
        printed = stdout.getvalue()
        self.assertIn("<redacted>", printed)
        self.assertNotIn("MOCK_CODE", printed)  # base config's inline access_code
        self.assertIn("MOCK_SERIAL", printed)

    def test_config_show_json_payload(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        with patch("bambu_cli.setup_cmd.config_cmd.emit_json") as mock_emit:
            _cmd_config(self._args("show", json_mode=True))
        payload = mock_emit.call_args[0][0]
        if hasattr(payload, "to_payload"):
            payload = payload.to_payload()
        self.assertEqual(payload["command"], "config")
        self.assertEqual(payload["action"], "show")
        self.assertEqual(payload["config"]["access_code"], "<redacted>")
        self.assertNotIn("MOCK_CODE", json.dumps(payload))

    def test_config_show_missing_config_exits(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        with (
            patch("bambu_cli.setup_cmd.config_cmd._config_path", return_value="/nonexistent/config.json"),
            patch("bambu_cli.logging_utils._BACKEND") as mock_logger,
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            _cmd_config(self._args("show"))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        self.assertTrue(any("Config not found" in call[0][0] for call in mock_logger.error.call_args_list))

    def test_config_validate_filters_to_config_checks(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        checks = [
            {"status": "ok", "name": "python", "message": "irrelevant install check"},
            {"status": "ok", "name": "printer-ip", "message": "Printer address is configured."},
            {"status": "warning", "name": "access-code", "message": "inline access_code"},
            {"status": "warning", "name": "gmsh", "message": "irrelevant install warning"},
        ]
        with (
            patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks),
            patch("bambu_cli.setup_cmd.config_cmd.emit_json") as mock_emit,
        ):
            _cmd_config(self._args("validate", json_mode=True))
        payload = mock_emit.call_args[0][0]
        if hasattr(payload, "to_payload"):
            payload = payload.to_payload()
        self.assertEqual(payload["action"], "validate")
        self.assertEqual({c["name"] for c in payload["checks"]}, {"printer-ip", "access-code"})
        # Warnings without --strict still validate (same semantics as preflight).
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["warnings"], 1)

    def test_config_validate_strict_fails_on_warnings(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        checks = [{"status": "warning", "name": "access-code", "message": "inline access_code"}]
        with (
            patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks),
            patch("bambu_cli.setup_cmd.config_cmd.logger"),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            _cmd_config(self._args("validate", strict=True))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)

    def test_config_validate_errors_exit(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config

        checks = [{"status": "error", "name": "serial", "message": "Config must contain the printer serial number."}]
        with (
            patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks),
            patch("bambu_cli.setup_cmd.config_cmd.logger"),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            _cmd_config(self._args("validate"))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)


class TestConfigPlatformAndCandidates(unittest.TestCase):
    def test_default_config_path_platforms(self):
        from bambu_cli.config import _default_config_path

        # darwin
        with patch("sys.platform", "darwin"), patch("os.path.exists", return_value=False):
            p = _default_config_path()
            self.assertIn("Library/Application Support/bambu/config.json", p.replace("\\", "/"))

        # win32 with APPDATA
        with patch("sys.platform", "win32"), patch("os.path.exists", return_value=False):
            with patch.dict(os.environ, {"APPDATA": "C:\\MockAppData"}):
                p = _default_config_path()
                self.assertIn("bambu/config.json", p.replace("\\", "/"))

        # win32 without APPDATA
        with patch("sys.platform", "win32"), patch("os.path.exists", return_value=False):
            with patch.dict(os.environ, {}, clear=True):
                p = _default_config_path()
                self.assertIn("AppData/Roaming/bambu/config.json", p.replace("\\", "/"))

        # linux with XDG_CONFIG_HOME
        with patch("sys.platform", "linux"), patch("os.path.exists", return_value=False):
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/xdg"}):
                p = _default_config_path()
                self.assertIn("/custom/xdg/bambu/config.json", p.replace("\\", "/"))

        # linux without XDG_CONFIG_HOME
        with patch("sys.platform", "linux"), patch("os.path.exists", return_value=False):
            with patch.dict(os.environ, {}, clear=True):
                p = _default_config_path()
                self.assertIn(".config/bambu/config.json", p.replace("\\", "/"))

    def test_orca_binary_and_profile_candidates_platforms(self):
        from bambu_cli.config import (
            _orca_binary_candidates,
            _profiles_dir_candidates,
            _first_existing_path,
        )

        # darwin
        with patch("sys.platform", "darwin"):
            orca_cands = _orca_binary_candidates()
            self.assertTrue(any("OrcaSlicer.app" in c for c in orca_cands))
            prof_cands = _profiles_dir_candidates()
            self.assertTrue(any("OrcaSlicer.app" in c for c in prof_cands))

        # win32 with PROGRAMFILES(X86)
        with patch("sys.platform", "win32"), patch("shutil.which", return_value=None):
            with patch.dict(os.environ, {"PROGRAMFILES(X86)": r"C:\Program Files (x86)"}):
                orca_cands = _orca_binary_candidates()
                self.assertTrue(any("orca-slicer.exe" in c for c in orca_cands))
                prof_cands = _profiles_dir_candidates()
                self.assertTrue(any("OrcaSlicer" in c for c in prof_cands))

        # win32 without PROGRAMFILES(X86)
        with patch("sys.platform", "win32"), patch("shutil.which", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                orca_cands = _orca_binary_candidates()
                prof_cands = _profiles_dir_candidates()
                self.assertTrue(len(orca_cands) > 0)
                self.assertTrue(len(prof_cands) > 0)

        # _first_existing_path
        self.assertIsNone(_first_existing_path([]))
        with patch("os.path.exists", return_value=False):
            first = _first_existing_path(["/dummy/one", "/dummy/two"])
            self.assertIn("dummy/one", first)


class TestConfigErrorsAndSecurity(unittest.TestCase):
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_load_config_not_found_platforms(self, mock_logger):
        with patch("bambu_cli.config.CONFIG_PATH", "/nonexistent/path/config.json"):
            with patch("sys.platform", "win32"):
                with self.assertRaises(BambuError):
                    load_config()
                self.assertTrue(any("orca-slicer.exe" in call[0][0] for call in mock_logger.info.call_args_list))

            with patch("sys.platform", "darwin"):
                with self.assertRaises(BambuError):
                    load_config()
                self.assertTrue(any("OrcaSlicer.app" in call[0][0] for call in mock_logger.info.call_args_list))

    def test_load_config_stat_oserror(self):
        with patch("os.path.exists", return_value=True):
            with patch("sys.platform", "linux"):
                with patch("os.stat", side_effect=OSError("stat error")):
                    self.assertIsNone(load_config(exit_on_fail=False))
                    with self.assertRaises(BambuError) as cm:
                        load_config(exit_on_fail=True)
                    self.assertEqual(cm.exception.exit_code, 1)

    def test_load_config_chmod_oserror_continues(self):
        st = MagicMock()
        st.st_mode = 0o644  # world-readable, triggers chmod
        with patch("os.path.exists", return_value=True):
            with patch("sys.platform", "linux"):
                with patch("os.stat", return_value=st):
                    with patch("os.chmod", side_effect=OSError("chmod error")):
                        with patch("builtins.open", mock_open(read_data='{"printer_ip": "127.0.0.1", "serial": "S1", "access_code": "code"}')):
                            cfg = load_config(exit_on_fail=True)
                            self.assertIsNotNone(cfg)

    def test_load_config_generic_exception(self):
        with patch("os.path.exists", return_value=True):
            with patch("sys.platform", "win32"):
                with patch("builtins.open", side_effect=RuntimeError("unexpected crash")):
                    self.assertIsNone(load_config(exit_on_fail=False))
                    with self.assertRaises(BambuError) as cm:
                        load_config(exit_on_fail=True)
                    self.assertEqual(cm.exception.exit_code, 1)

    def test_apply_config_empty_and_insecure_tls(self):
        from bambu_cli.config import apply_config

        self.assertIsNone(apply_config({}))
        with patch("bambu_cli.logging_utils._BACKEND") as mock_logger:
            apply_config({"insecure_tls": True})
            self.assertTrue(any("SECURITY WARNING" in call[0][0] for call in mock_logger.warning.call_args_list))

    def test_enforce_secret_file_permissions_win32_and_chmod_error(self):
        from bambu_cli.config import _enforce_secret_file_permissions

        with patch("sys.platform", "win32"):
            # win32 returns early
            self.assertIsNone(_enforce_secret_file_permissions("dummy", "dummy"))

        with patch("sys.platform", "linux"):
            # Already 0600
            st_secure = MagicMock(st_mode=0o600)
            with patch("os.stat", return_value=st_secure), patch("os.chmod") as mock_chmod:
                _enforce_secret_file_permissions("dummy", "dummy")
                mock_chmod.assert_not_called()

            # Chmod succeeds
            st_insecure = MagicMock(st_mode=0o644)
            with patch("os.stat", return_value=st_insecure), patch("os.chmod") as mock_chmod:
                with patch("bambu_cli.logging_utils._BACKEND") as mock_logger:
                    _enforce_secret_file_permissions("dummy", "dummy")
                    mock_chmod.assert_called_once_with("dummy", 0o600)
                    self.assertTrue(any("Automatically enforced 0600" in call[0][0] for call in mock_logger.info.call_args_list))

            # Chmod error
            with patch("os.stat", return_value=st_insecure):
                with patch("os.chmod", side_effect=OSError("chmod denied")):
                    with patch("bambu_cli.logging_utils._BACKEND") as mock_logger:
                        _enforce_secret_file_permissions("dummy", "dummy")
                        self.assertTrue(any("Could not tighten permissions" in call[0][0] for call in mock_logger.warning.call_args_list))

    def test_load_access_code_both_keys_warns(self):
        from bambu_cli.config import load_access_code

        with config_ctx({"access_code": "inline_val", "access_code_file": "/path/to/code"}):
            with patch("os.stat", side_effect=OSError("missing")):
                with patch("builtins.open", mock_open(read_data="file_val")):
                    with patch("bambu_cli.logging_utils._BACKEND") as mock_logger:
                        code = load_access_code()
                        self.assertEqual(code, "file_val")
                        self.assertTrue(any("BOTH an inline access_code and an access_code_file" in call[0][0] for call in mock_logger.warning.call_args_list))

    def test_load_access_code_errors(self):
        from bambu_cli.config import load_access_code

        # Inline placeholder value
        with config_ctx({"access_code": "YOUR_ACCESS_CODE"}):
            with self.assertRaises(BambuError) as cm:
                load_access_code()
            self.assertEqual(cm.exception.exit_code, 1)

        # access_code_file OSError
        with config_ctx({"access_code_file": "/path/to/secret"}):
            with patch("os.stat", side_effect=OSError("missing")):
                with patch("builtins.open", side_effect=OSError("read denied")):
                    with self.assertRaises(BambuError) as cm:
                        load_access_code()
                    self.assertEqual(cm.exception.exit_code, 1)

        # access_code_file placeholder content
        with config_ctx({"access_code_file": "/path/to/secret"}):
            with patch("os.stat", side_effect=OSError("missing")):
                with patch("builtins.open", mock_open(read_data="ACCESS_CODE")):
                    with self.assertRaises(BambuError) as cm:
                        load_access_code()
                    self.assertEqual(cm.exception.exit_code, 1)

    def test_misc_config_helpers(self):
        import argparse
        from bambu_cli.config import (
            load_username,
            fingerprint_sha256,
            get_network_timeout,
            get_slicer_timeout,
            get_command_timeout,
            get_upload_timeout,
            _expected_fingerprint,
        )

        with settings_ctx(username="test_user"):
            self.assertEqual(load_username(), "test_user")

        self.assertIsNone(fingerprint_sha256(None))
        self.assertIsNone(fingerprint_sha256(b""))

        with config_ctx({"cert_fingerprint": "AA:BB:CC:DD"}):
            self.assertEqual(_expected_fingerprint(), "aabbccdd")

        # CLI args override config and default
        args = argparse.Namespace(network_timeout=99.0, slicer_timeout=88.0, command_timeout=77.0, upload_timeout=66.0)
        self.assertEqual(get_network_timeout(args), 99.0)
        self.assertEqual(get_slicer_timeout(args), 88.0)
        self.assertEqual(get_command_timeout(args), 77.0)
        self.assertEqual(get_upload_timeout(args), 66.0)

        # Config override for timeout
        with config_ctx({"network_timeout": 42.0, "slicer_timeout": 500.0, "command_timeout": 30.0, "upload_timeout": 120.0}):
            self.assertEqual(get_network_timeout(), 42.0)
            self.assertEqual(get_slicer_timeout(), 500.0)
            self.assertEqual(get_command_timeout(), 30.0)
            self.assertEqual(get_upload_timeout(), 120.0)


if __name__ == "__main__":
    unittest.main()
