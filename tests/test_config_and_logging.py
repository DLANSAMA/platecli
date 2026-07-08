from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestLoadConfig(unittest.TestCase):


    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    def test_cmd_slice_convert_step_to_stl_argument_injection(self, mock_getsize, mock_exists, mock_logger, mock_run):
        import os

        from bambu_cli.bambu import _convert_step_to_stl

        # Setup mocks
        mock_run.return_value.returncode = 0
        mock_exists.return_value = True
        mock_getsize.return_value = 1024

        filepath = "-malicious.step"
        abs_filepath = os.path.abspath(filepath)
        expected_stl_path = abs_filepath.rsplit('.', 1)[0] + '.stl'

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
        self.assertEqual(kwargs_run, {'capture_output': True, 'text': True, 'timeout': 60})

    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    def test_load_config_not_found(self, mock_logger, mock_exists):
        mock_exists.return_value = False
        
        with self.assertRaises((SystemExit, BambuError)) as cm:
            load_config()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        pass  # domain code raises BambuError; process exit is main()'s job
        # Check if instructions were logged
        self.assertTrue(any("Config not found" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_config_invalid_json(self, mock_file, mock_exit, mock_logger, mock_exists, mock_stat):
        mock_exists.return_value = True
        
        with self.assertRaises((SystemExit, BambuError)) as cm:
            load_config()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        self.assertTrue(any("Error loading config" in call[0][0] for call in mock_logger.error.call_args_list))



    @patch('os.path.exists')
    def test_load_config_not_found_no_exit(self, mock_exists):
        from bambu_cli.bambu import load_config
        mock_exists.return_value = False
        result = load_config(exit_on_fail=False)
        self.assertIsNone(result)

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_config_invalid_json_no_exit(self, mock_file, mock_exists, mock_stat):
        from bambu_cli.bambu import load_config
        mock_exists.return_value = True
        result = load_config(exit_on_fail=False)
        self.assertIsNone(result)


class TestLoadAccessCode(unittest.TestCase):

    def test_load_access_code_inline(self):
        import bambu_cli.bambu
        with config_ctx({'access_code': 'inline_secret'}):
            self.assertEqual(bambu_cli.bambu.load_access_code(), 'inline_secret')

    @patch('os.path.expanduser')
    @patch('builtins.open', new_callable=mock_open, read_data=' file_secret ')
    def test_load_access_code_file(self, mock_file, mock_expanduser):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with config_ctx({'access_code_file': '~/.config/bambu/secret'}):
            mock_expanduser.return_value = '/home/user/.config/bambu/secret'
            self.assertEqual(bambu_cli.bambu.load_access_code(), 'file_secret')

    @patch('bambu_cli.bambu.logger')
    @patch('os.path.expanduser')
    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_load_access_code_file_not_found(self, mock_file, mock_expanduser, mock_logger):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with config_ctx({'access_code_file': '~/.config/bambu/missing'}):
            with self.assertRaises(BambuError) as cm:
                bambu_cli.bambu.load_access_code()
            self.assertEqual(cm.exception.exit_code, 1)
            self.assertTrue(any("Access code file not found" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.bambu.logger')
    def test_load_access_code_missing(self, mock_logger):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with config_ctx({}):
            with self.assertRaises(BambuError) as cm:
                bambu_cli.bambu.load_access_code()
            self.assertEqual(cm.exception.exit_code, 1)
            mock_logger.error.assert_called_with("No 'access_code' or 'access_code_file' in config.json")


class TestSetupLogging(unittest.TestCase):
    @patch('bambu_cli.cli.logging')
    @patch('bambu_cli.cli.sys')
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

        with patch.dict('sys.modules', {'rich': None, 'rich.logging': None, 'rich.console': None, 'rich.traceback': None}):
            with patch('bambu_cli.cli.logger') as mock_logger:
                bambu_cli_module.setup_logging()

                # Check root handler removal
                mock_root.removeHandler.assert_called_once_with(mock_handler)

                # Check StreamHandler and Formatter are created
                mock_logging.StreamHandler.assert_called_once_with(mock_sys.stderr)
                mock_logging.Formatter.assert_called_once_with('%(levelname)s: %(message)s')

                # Check log level setting
                mock_logger.setLevel.assert_called_once_with(mock_logging.INFO)

                # Check propagate False
                self.assertFalse(mock_logger.propagate)

    @patch('bambu_cli.cli.logging')
    @patch('bambu_cli.cli.sys')
    def test_setup_logging_verbose(self, mock_sys, mock_logging):
        import bambu_cli.cli as bambu_cli_module

        mock_root = MagicMock()

        def get_logger_side_effect(name=None):
            if name is None or name == "bambu_cli":
                return mock_root
            return MagicMock()

        mock_logging.getLogger.side_effect = get_logger_side_effect

        with patch.dict('sys.modules', {'rich': None, 'rich.logging': None, 'rich.console': None, 'rich.traceback': None}):
            with patch('bambu_cli.cli.logger') as mock_logger:
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
        self.assertEqual(payload["command"], "config")
        self.assertEqual(payload["action"], "show")
        self.assertEqual(payload["config"]["access_code"], "<redacted>")
        self.assertNotIn("MOCK_CODE", json.dumps(payload))

    def test_config_show_missing_config_exits(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config
        with patch("bambu_cli.setup_cmd.config_cmd._config_path", return_value="/nonexistent/config.json"), \
             patch("bambu_cli.setup_cmd.config_cmd.logger") as mock_logger, self.assertRaises((SystemExit, BambuError)) as cm:
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
        with patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks), \
             patch("bambu_cli.setup_cmd.config_cmd.emit_json") as mock_emit:
            _cmd_config(self._args("validate", json_mode=True))
        payload = mock_emit.call_args[0][0]
        self.assertEqual(payload["action"], "validate")
        self.assertEqual({c["name"] for c in payload["checks"]}, {"printer-ip", "access-code"})
        # Warnings without --strict still validate (same semantics as preflight).
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["warnings"], 1)

    def test_config_validate_strict_fails_on_warnings(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config
        checks = [{"status": "warning", "name": "access-code", "message": "inline access_code"}]
        with patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks), \
             patch("bambu_cli.setup_cmd.config_cmd.logger"), self.assertRaises((SystemExit, BambuError)) as cm:
            _cmd_config(self._args("validate", strict=True))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)

    def test_config_validate_errors_exit(self):
        from bambu_cli.setup_cmd.config_cmd import _cmd_config
        checks = [{"status": "error", "name": "serial", "message": "Config must contain the printer serial number."}]
        with patch("bambu_cli.setup_cmd.config_cmd.collect_preflight_checks", return_value=checks), \
             patch("bambu_cli.setup_cmd.config_cmd.logger"), self.assertRaises((SystemExit, BambuError)) as cm:
            _cmd_config(self._args("validate"))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)


if __name__ == '__main__':
    unittest.main()
