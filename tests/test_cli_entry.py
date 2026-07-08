from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestMain(unittest.TestCase):

    def tearDown(self):
        # main() installs a process-wide RuntimeContext; reset it to the shared
        # baseline so it can't leak into later tests (the pytest suite does this
        # via a conftest fixture, but the CI `unittest` line needs it here).
        install_baseline_context()

    @patch('sys.argv', ['bambu.py', 'status'])
    @patch('bambu_cli.bambu.cmd_status')
    @patch('bambu_cli.cli.setup_logging')
    @patch('socket.getaddrinfo')
    def test_main_argparse_subcommand(self, mock_getaddrinfo, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu
        mock_getaddrinfo.return_value = []
        bambu_cli.bambu.main()
        mock_cmd_status.assert_called_once()
        mock_setup_logging.assert_called_once_with(False)

    @patch('sys.argv', ['bambu.py', '--sim', 'status'])
    @patch('bambu_cli.bambu.cmd_status')
    @patch('bambu_cli.cli.setup_logging')
    def test_main_sim_flag(self, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu
        from bambu_cli import context
        bambu_cli.bambu.main()
        self.assertTrue(context.get_current().simulation)

    @patch('sys.argv', ['bambu.py', 'status'])
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('socket.getaddrinfo', side_effect=socket.gaierror)
    def test_main_invalid_printer_ip(self, mock_getaddrinfo, mock_exit, mock_logger):
        import bambu_cli.bambu
        from bambu_cli import context
        from bambu_cli.context import RuntimeContext, Settings
        mock_exit.side_effect = SystemExit(1)
        # Install a context with an unresolvable IP; mock load_config so main()
        # doesn't overwrite it from the on-disk config.
        context.set_current(RuntimeContext(settings=Settings(printer_ip='invalid_ip')))
        with patch('bambu_cli.bambu.load_config', return_value=None):
            with self.assertRaises((SystemExit, BambuError)) as cm:
                bambu_cli.bambu.main()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        mock_logger.error.assert_called_with("Invalid printer_ip or hostname in config: invalid_ip")


class TestBambuCmdSetup(unittest.TestCase):

    def setUp(self):
        self.isatty_patcher = patch('sys.stdin.isatty', return_value=True)
        self.isatty_patcher.start()
        self.input_patcher = patch('builtins.input', return_value="")
        self.input_mock_obj = self.input_patcher.start()

    def tearDown(self):
        self.isatty_patcher.stop()
        self.input_patcher.stop()

    @patch('getpass.getpass')
    @patch('os.makedirs')
    @patch('os.open')
    @patch('builtins.open', new_callable=mock_open)
    @patch('bambu_cli.bambu.time.sleep')
    @patch('bambu_cli.bambu.logger')
    @patch('bambu_cli.bambu.socket.inet_ntoa')
    def test_cmd_setup_zeroconf_success(self, mock_ntoa, mock_logger, mock_sleep, mock_file, mock_open_fd, mock_makedirs, mock_getpass):
        import sys
        import os
        from bambu_cli.bambu import CONFIG_PATH

        mock_zc_module = MagicMock()
        mock_zc_class = MagicMock()
        mock_zc_instance = MagicMock()
        mock_zc_class.return_value = mock_zc_instance
        mock_browser_class = MagicMock()

        def fake_browser(zc, type_, listener):
            mock_info = MagicMock()
            mock_info.addresses = [b'\xc0\xa8\x01\x01']
            mock_info.parsed_addresses.return_value = ["192.168.1.1"]
            mock_zc_instance.get_service_info.return_value = mock_info
            mock_ntoa.return_value = "192.168.1.1"
            listener.add_service(zc, type_, "BBLP-00112233._bblp._tcp.local.")
            return mock_browser_class

        mock_browser_class.side_effect = fake_browser

        mock_zc_module.Zeroconf = mock_zc_class
        mock_zc_module.ServiceBrowser = fake_browser

        sys.modules['zeroconf'] = mock_zc_module

        from bambu_cli.bambu import cmd_setup

        mock_getpass.return_value = "12345678"
        mock_open_fd.return_value = 5
        self.input_mock_obj.side_effect = ["", "", "n"]

        cmd_setup(MagicMock(json=False))

        from bambu_cli.utils import _display_path
        mock_logger.info.assert_any_call(f"\n✅ Config saved to {_display_path(CONFIG_PATH)}")
        mock_file.assert_called_with(5, 'w', encoding="utf-8")
        import json
        written_content = "".join(call[0][0] for call in mock_file().write.call_args_list)
        data = json.loads(written_content)
        self.assertEqual(data["printer_ip"], "192.168.1.1")
        self.assertEqual(data["serial"], "00112233")
        self.assertEqual(data["access_code"], "12345678")

        del sys.modules['zeroconf']

    @patch('bambu_cli.bambu.time.sleep')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_setup_zeroconf_no_devices(self, mock_exit, mock_logger, mock_sleep):
        import sys

        mock_zc_module = MagicMock()
        mock_zc_class = MagicMock()
        mock_zc_instance = MagicMock()
        mock_zc_class.return_value = mock_zc_instance

        mock_zc_module.Zeroconf = mock_zc_class
        mock_zc_module.ServiceBrowser = MagicMock()

        sys.modules['zeroconf'] = mock_zc_module

        from bambu_cli.bambu import cmd_setup
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises((SystemExit, BambuError)):
            cmd_setup(MagicMock())

        mock_logger.error.assert_called_with("No printers found. Ensure printer is on the same network.")
        del sys.modules['zeroconf']

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_setup_zeroconf_not_installed(self, mock_exit, mock_logger):
        import sys
        if 'zeroconf' in sys.modules:
            del sys.modules['zeroconf']

        original_import = __import__
        def mocked_import(name, *args, **kwargs):
            if name == 'zeroconf':
                raise ImportError("No module named 'zeroconf'")
            return original_import(name, *args, **kwargs)

        import builtins
        builtins.__import__ = mocked_import

        try:
            from bambu_cli.bambu import cmd_setup
            mock_exit.side_effect = SystemExit(1)

            with self.assertRaises((SystemExit, BambuError)):
                cmd_setup(MagicMock())

            mock_logger.warning.assert_called_with("⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled.")
        finally:
            builtins.__import__ = original_import


if __name__ == '__main__':
    unittest.main()
