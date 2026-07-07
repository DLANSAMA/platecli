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
with open(mock_config_path, "w", encoding="utf-8") as f:
    json.dump({
        "printer_ip": "127.0.0.1",
        "serial": "MOCK_SERIAL",
        "access_code": "MOCK_CODE",
        "orca_slicer": "/tmp/mock_orca",
        "profiles_dir": "/tmp/mock_profiles"
    }, f)

import bambu_cli.bambu as bambu
bambu.CONFIG_PATH = mock_config_path
bambu.load_config(exit_on_fail=False)

def cleanup_mock_config():
    shutil.rmtree(mock_config_dir, ignore_errors=True)

atexit.register(cleanup_mock_config)

try:
    from bambu_cli.bambu import cmd_stop, get_ftp, load_config, create_mqtt_client, cmd_light, execute_print_command, setup_logging
    import ssl
    import urllib.error
    setup_logging(verbose=True)
except ImportError:
    pass

from bambu_cli.printer import BambuPrinter


def _test_printer(ip='192.168.1.1', serial=None, access_code='MOCK_CODE', **kwargs):
    """Build a BambuPrinter matching the mocked global config for direct protocol calls."""
    return BambuPrinter(ip=ip, serial=serial or bambu.SERIAL, access_code=access_code, **kwargs)


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

class TestLoadConfig(unittest.TestCase):


    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    def test_cmd_slice_convert_step_to_stl_argument_injection(self, mock_getsize, mock_exists, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        import os

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
    @patch('sys.exit')
    def test_load_config_not_found(self, mock_exit, mock_logger, mock_exists):
        mock_exists.return_value = False
        mock_exit.side_effect = SystemExit(1)

        with self.assertRaises(SystemExit) as cm:
            load_config()

        self.assertEqual(cm.exception.code, 1)
        mock_exit.assert_called_once_with(1)
        # Check if instructions were logged
        self.assertTrue(any("Config not found" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_config_invalid_json(self, mock_file, mock_exit, mock_logger, mock_exists, mock_stat):
        mock_exists.return_value = True
        mock_exit.side_effect = SystemExit(1)

        with self.assertRaises(SystemExit) as cm:
            load_config()

        self.assertEqual(cm.exception.code, 1)
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

    @patch('bambu_cli.bambu._cfg', {'access_code': 'inline_secret'})
    def test_load_access_code_inline(self):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with patch.dict(bambu_cli.bambu._cfg, {'access_code': 'inline_secret'}, clear=True):
            self.assertEqual(bambu_cli.bambu.load_access_code(), 'inline_secret')

    @patch('os.path.expanduser')
    @patch('builtins.open', new_callable=mock_open, read_data=' file_secret ')
    def test_load_access_code_file(self, mock_file, mock_expanduser):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with patch.dict(bambu_cli.bambu._cfg, {'access_code_file': '~/.config/bambu/secret'}, clear=True):
            mock_expanduser.return_value = '/home/user/.config/bambu/secret'
            self.assertEqual(bambu_cli.bambu.load_access_code(), 'file_secret')

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('os.path.expanduser')
    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_load_access_code_file_not_found(self, mock_file, mock_expanduser, mock_exit, mock_logger):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with patch.dict(bambu_cli.bambu._cfg, {'access_code_file': '~/.config/bambu/missing'}, clear=True):
            mock_exit.side_effect = SystemExit(1)
            with self.assertRaises(SystemExit) as cm:
                bambu_cli.bambu.load_access_code()
            self.assertEqual(cm.exception.code, 1)
            self.assertTrue(any("Access code file not found" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_load_access_code_missing(self, mock_exit, mock_logger):
        from bambu_cli.bambu import load_access_code
        if hasattr(load_access_code, 'cache_clear'):
            load_access_code.cache_clear()
        import bambu_cli.bambu
        with patch.dict(bambu_cli.bambu._cfg, {}, clear=True):
            mock_exit.side_effect = SystemExit(1)
            with self.assertRaises(SystemExit) as cm:
                bambu_cli.bambu.load_access_code()
            self.assertEqual(cm.exception.code, 1)
            mock_logger.error.assert_called_with("No 'access_code' or 'access_code_file' in config.json")

class TestImplicitFTPS(unittest.TestCase):

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.SSLContext')
    def test_implicit_ftps_insecure(self, mock_ssl_context, mock_create_conn):
        from bambu_cli.bambu import ImplicitFTPS
        mock_sock = MagicMock()
        mock_sock.family = 2
        mock_create_conn.return_value = mock_sock

        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_sock
        mock_ssl_context.return_value = mock_ctx

        ftp = ImplicitFTPS()
        # TLS behavior is now driven by the printer object attached to the FTP client
        ftp.printer = _test_printer(insecure_tls=True)
        ftp.getresp = MagicMock(return_value="220 Welcome")

        welcome = ftp.connect("192.168.1.1", 990, 60)

        self.assertEqual(welcome, "220 Welcome")
        self.assertEqual(mock_ctx.check_hostname, False)
        import ssl
        self.assertEqual(mock_ctx.verify_mode, ssl.CERT_NONE)
        mock_ctx.wrap_socket.assert_called_with(mock_sock, server_hostname="192.168.1.1")

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.SSLContext')
    @patch('bambu_cli.bambu.INSECURE_TLS', False)
    def test_implicit_ftps_secure(self, mock_ssl_context, mock_create_conn):
        from bambu_cli.bambu import ImplicitFTPS
        mock_sock = MagicMock()
        mock_sock.family = 2
        mock_create_conn.return_value = mock_sock

        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_sock
        mock_ssl_context.return_value = mock_ctx

        ftp = ImplicitFTPS()
        ftp.getresp = MagicMock(return_value="220 Welcome")

        welcome = ftp.connect("192.168.1.1", 990, 60)

        self.assertEqual(welcome, "220 Welcome")
        self.assertEqual(mock_ctx.check_hostname, True)
        import ssl
        self.assertEqual(mock_ctx.verify_mode, ssl.CERT_REQUIRED)
        mock_ctx.load_default_certs.assert_called_once()

class TestSendCommand(unittest.TestCase):

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    def test_send_command_success(self, mock_create):
        from bambu_cli.bambu import send_command, SERIAL
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 0)
            mock_client.on_publish(mock_client, None, 1)

        mock_client.connect.side_effect = side_effect_connect

        printer = _test_printer(ip='192.168.1.1')
        result = send_command(printer, '{"test": "payload"}')

        self.assertTrue(result)
        mock_client.connect.assert_called_with('192.168.1.1', 8883, keepalive=10)
        topic = f"device/{SERIAL}/request"
        mock_client.publish.assert_called_once_with(topic, '{"test": "payload"}')
        mock_client.loop_start.assert_called_once()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.time.sleep')
    def test_send_command_retry_timeout(self, mock_sleep, mock_create):
        from bambu_cli.bambu import send_command
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_client.connect.side_effect = OSError("Connection error")

        result = send_command(_test_printer(), '{"test": "payload"}')

        self.assertFalse(result)
        self.assertEqual(mock_client.connect.call_count, 3)

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    def test_send_command_on_connect_rc_error(self, mock_logger, mock_create):
        from bambu_cli.bambu import send_command
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 5)

        mock_client.connect.side_effect = side_effect_connect

        result = send_command(_test_printer(ip='192.168.1.1'), '{"test": "payload"}', timeout=0.1)

        self.assertFalse(result)
        mock_logger.error.assert_called_with("Connection failed: rc=5")

import socket

class TestMain(unittest.TestCase):

    @patch('sys.argv', ['bambu.py', 'status'])
    @patch('bambu_cli.bambu.cmd_status')
    @patch('bambu_cli.bambu.setup_logging')
    @patch('socket.getaddrinfo')
    def test_main_argparse_subcommand(self, mock_getaddrinfo, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu
        mock_getaddrinfo.return_value = []
        with patch('bambu_cli.bambu.PRINTER_IP', '192.168.1.1'):
            bambu_cli.bambu.main()
        mock_cmd_status.assert_called_once()
        mock_setup_logging.assert_called_once_with(False)

    @patch('sys.argv', ['bambu.py', '--sim', 'status'])
    @patch('bambu_cli.bambu.cmd_status')
    @patch('bambu_cli.bambu.setup_logging')
    def test_main_sim_flag(self, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu
        orig_sim = bambu_cli.bambu.SIMULATION_MODE
        try:
            with patch('bambu_cli.bambu.PRINTER_IP', '192.168.1.1'):
                bambu_cli.bambu.main()
            self.assertTrue(bambu_cli.bambu.SIMULATION_MODE)
        finally:
            bambu_cli.bambu.SIMULATION_MODE = orig_sim

    @patch('sys.argv', ['bambu.py', 'status'])
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('socket.getaddrinfo', side_effect=socket.gaierror)
    def test_main_invalid_printer_ip(self, mock_getaddrinfo, mock_exit, mock_logger):
        import bambu_cli.bambu
        mock_exit.side_effect = SystemExit(1)
        with patch('bambu_cli.bambu.PRINTER_IP', 'invalid_ip'):
            with self.assertRaises(SystemExit) as cm:
                bambu_cli.bambu.main()

        self.assertEqual(cm.exception.code, 1)
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

        with self.assertRaises(SystemExit):
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

            with self.assertRaises(SystemExit):
                cmd_setup(MagicMock())

            mock_logger.warning.assert_called_with("⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled.")
        finally:
            builtins.__import__ = original_import

class TestBambuCmdUploadEdgeCases(unittest.TestCase):

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_upload_invalid_filepath(self, mock_exit, mock_logger):
        from bambu_cli.bambu import cmd_upload
        args = MagicMock()
        args.file = "-invalid.gcode"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises(SystemExit) as cm:
            cmd_upload(args)
        self.assertEqual(cm.exception.code, 3)
        mock_logger.error.assert_called_with("Invalid filepath: -invalid.gcode")

    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_upload_file_not_found(self, mock_exit, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_upload
        mock_exists.return_value = False
        args = MagicMock()
        args.file = "missing.gcode"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises(SystemExit) as cm:
            cmd_upload(args)
        self.assertEqual(cm.exception.code, 3)
        mock_logger.error.assert_called_with("File not found: missing.gcode")

    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_upload_dry_run_success(self, mock_logger, mock_get_printer, mock_getsize, mock_exists):
        from bambu_cli.bambu import cmd_upload
        mock_exists.return_value = True
        mock_getsize.return_value = 1024
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = True

        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)
        mock_logger.info.assert_any_call("   ✅ Printer reachable.")
        mock_logger.info.assert_any_call("   ✅ Local file test.gcode exists (1KB)")

    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_upload_dry_run_fail(self, mock_exit, mock_logger, mock_get_printer, mock_getsize, mock_exists):
        from bambu_cli.bambu import cmd_upload
        mock_exists.return_value = True
        mock_getsize.return_value = 1024
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = True

        mock_get_ftp = MagicMock(side_effect=OSError("FTP Error"))
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit) as cm:
            cmd_upload(args)
        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_called_with("Dry run failed: Could not reach printer.")

    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.bambu.time.sleep')
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.printer.logger')
    @patch('builtins.open', new_callable=mock_open)
    def test_cmd_upload_resume_offset(self, mock_file, mock_logger, mock_get_printer, mock_sleep, mock_getsize, mock_exists):
        from bambu_cli.bambu import cmd_upload
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = False

        mock_ftp1 = MagicMock()
        mock_ftp1.storbinary.side_effect = OSError("Upload interrupted")
        mock_ftp1.size.return_value = 1024

        mock_ftp2 = MagicMock()

        mock_get_ftp = MagicMock(side_effect=[
            MagicMock(__enter__=MagicMock(return_value=mock_ftp1)),
            MagicMock(__enter__=MagicMock(return_value=mock_ftp1)),
            MagicMock(__enter__=MagicMock(return_value=mock_ftp2))
        ])
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)

        mock_logger.info.assert_any_call("🔄 Resuming from 1KB...")
        mock_file().seek.assert_called_with(1024)
        mock_ftp2.storbinary.assert_called_with('STOR /model/test.gcode', mock_file(), blocksize=1048576, rest=1024, callback=None)

    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.bambu.time.sleep')
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('builtins.open', new_callable=mock_open)
    def test_cmd_upload_max_retries_exhausted(self, mock_file, mock_exit, mock_logger, mock_get_printer, mock_sleep, mock_getsize, mock_exists):
        from bambu_cli.bambu import cmd_upload
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = False

        mock_ftp = MagicMock()
        mock_ftp.storbinary.side_effect = OSError("Upload always fails")
        mock_ftp.size.side_effect = OSError("Can't get size")

        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit) as cm:
            cmd_upload(args)

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_called_with("❌ Upload failed after 4 attempts.")

class TestBambuCmdLight(unittest.TestCase):

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_light_on(self, mock_logger, mock_send_command, mock_seq):
        args = MagicMock()
        args.action = "on"

        cmd_light(args)

        # Expected payload
        expected_payload = json.dumps({
            "system": {"sequence_id": "0", "command": "ledctrl",
                       "led_node": "chamber_light", "led_mode": "on",
                       "led_on_time": 500, "led_off_time": 500}
        })

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("💡 Light turned on")

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_light_off(self, mock_logger, mock_send_command, mock_seq):
        args = MagicMock()
        args.action = "off"

        cmd_light(args)

        # Expected payload
        expected_payload = json.dumps({
            "system": {"sequence_id": "0", "command": "ledctrl",
                       "led_node": "chamber_light", "led_mode": "off",
                       "led_on_time": 500, "led_off_time": 500}
        })

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("💡 Light turned off")



class TestBambuCmdResume(unittest.TestCase):

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_resume(self, mock_logger, mock_send_command, mock_seq):
        from bambu_cli.bambu import cmd_resume
        args = MagicMock()

        cmd_resume(args)

        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "resume"}})
        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("▶️  Print resumed")

class TestBambuCmdPause(unittest.TestCase):

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_pause(self, mock_logger, mock_send_command, mock_seq):
        from bambu_cli.bambu import cmd_pause
        args = MagicMock()

        cmd_pause(args)

        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "pause"}})
        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("⏸️  Print paused")

class TestBambuCmdStop(unittest.TestCase):

    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_stop_without_confirm(self, mock_logger, mock_send_command):
        # Create a mock args object with confirm=False
        args = MagicMock()
        args.confirm = False

        with self.assertRaises(SystemExit) as cm:
            cmd_stop(args)
        self.assertEqual(cm.exception.code, 5)

        # Assert that send_command was NOT called
        mock_send_command.assert_not_called()

        # Assert that the correct message was logged
        mock_logger.warning.assert_called_once_with("⚠️  This will STOP the current print. Add --confirm to proceed.")

    @patch('bambu_cli.bambu.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_stop_with_confirm(self, mock_logger, mock_send_command):
        # Create a mock args object with confirm=True
        args = MagicMock()
        args.confirm = True

        cmd_stop(args)

        # Assert that send_command WAS called
        mock_send_command.assert_called_once()


class TestBambuCmdFiles(unittest.TestCase):
    def _printer_with_ftp(self, mock_get_printer, mock_get_ftp):
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        return printer

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_files_success(self, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_files
        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        # Mock the context manager behavior
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.return_value = ['file1.3mf', 'file2.3mf']
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)

        cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with('/model/')
        # __exit__ should be called when using context manager
        mock_get_ftp.return_value.__exit__.assert_called_once()
        mock_logger.info.assert_any_call("📁 Files on printer:")
        mock_logger.info.assert_any_call("   file1.3mf")
        mock_logger.info.assert_any_call("   file2.3mf")

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_files_empty(self, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_files
        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.return_value = []
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)

        cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with('/model/')
        mock_get_ftp.return_value.__exit__.assert_called_once()
        mock_logger.info.assert_called_with("No files on printer.")

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_files_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_files
        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.side_effect = OSError("FTP Error")
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit):
            cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with('/model/')
        mock_logger.error.assert_called_with("Error listing files: Failed to list files via printer API")

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_files_get_ftp_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_files
        args = MagicMock()
        args.json = False
        mock_get_ftp = MagicMock(side_effect=OSError("Connection Failed"))
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit):
            cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_logger.error.assert_called_with("Error listing files: Failed to list files via printer API")

class TestBambuCmdDelete(unittest.TestCase):
    @patch('bambu_cli.bambu.get_ftp')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_delete_no_confirm(self, mock_exit, mock_logger, mock_get_ftp):
        from bambu_cli.bambu import cmd_delete
        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = False

        mock_exit.side_effect = SystemExit(5)

        with self.assertRaises(SystemExit) as cm:
            cmd_delete(args)

        self.assertEqual(cm.exception.code, 5)
        mock_get_ftp.assert_not_called()
        mock_logger.warning.assert_called_once_with("⚠️  This will DELETE 'test.3mf' from the printer. Add --confirm to proceed.")

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_delete_success(self, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_delete
        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = True
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_delete(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.delete.assert_called_once_with('/model/test.3mf')
        mock_logger.info.assert_called_once_with("🗑️  Deleted test.3mf from printer")

    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_delete_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_delete
        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = True
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.delete.side_effect = OSError("Delete Error")
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit):
            cmd_delete(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.delete.assert_called_once_with('/model/test.3mf')
        mock_logger.error.assert_called_with("Delete failed: Delete operation failed in printer client.")

class TestGetFtp(unittest.TestCase):

    def setUp(self):
        from bambu_cli.protocols.ftps import connection_manager
        connection_manager.clear()
        self.addCleanup(connection_manager.clear)

    @patch('bambu_cli.protocols.ftps.ImplicitFTPS')
    def test_get_ftp_success(self, mock_implicit_ftps):
        # Setup mocks
        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        printer = _test_printer(ip='192.168.1.100', access_code='mock_access_code')

        # get_ftp/_create_raw_ftp now take the printer object
        result = get_ftp(printer)

        # Assertions
        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with('192.168.1.100', 990, timeout=60)
        mock_ftp_instance_login = mock_ftp_instance.login
        mock_ftp_instance_login.assert_called_once_with('bblp', 'mock_access_code')
        mock_ftp_instance.prot_p.assert_called_once()

        from bambu_cli.protocols.ftps import PooledFTPWrapper
        self.assertIsInstance(result, PooledFTPWrapper)
        self.assertEqual(result._ftp, mock_ftp_instance)

    @patch('bambu_cli.protocols.ftps.ImplicitFTPS')
    def test_get_ftp_connect_failure(self, mock_implicit_ftps):
        # Setup mock to raise an exception on connect
        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        mock_ftp_instance.connect.side_effect = OSError("Connection Refused")
        printer = _test_printer(ip='192.168.1.100', access_code='mock_access_code')

        # Call the function and assert it raises
        with self.assertRaises(Exception) as context:
            get_ftp(printer)

        self.assertEqual(str(context.exception), "Connection Refused")
        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with('192.168.1.100', 990, timeout=60)
        # Ensure it doesn't try to login if connect fails
        mock_ftp_instance.login.assert_not_called()
        mock_ftp_instance.prot_p.assert_not_called()


class TestBambuCmdSlice(unittest.TestCase):
    def setUp(self):
        self.access_patcher = patch('os.access', return_value=True)
        self.mock_access = self.access_patcher.start()

    def tearDown(self):
        self.access_patcher.stop()

    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_invalid_filepath(self, mock_logger):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "-invalid"

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 3)

        mock_logger.error.assert_called_with("Invalid filepath: -invalid")

    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_file_not_found(self, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "notfound.stl"
        mock_exists.return_value = False

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 3)

        mock_logger.error.assert_called_with("File not found: notfound.stl")

    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_step_conversion_fail(self, mock_logger, mock_subprocess_run, mock_exists):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.step"

        # os.path.exists initially true for test.step, false for converted test.stl
        def exists_side_effect(path):
            if path == "test.step":
                return True
            return False
        mock_exists.side_effect = exists_side_effect

        # subprocess.run returns failure
        mock_run_result = MagicMock()
        mock_run_result.returncode = 1
        mock_subprocess_run.return_value = mock_run_result

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 5)

        self.assertTrue(any("STEP conversion failed" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('subprocess.Popen')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_missing_machine_profile(self, mock_logger, mock_exists, mock_popen):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1

        mock_process = MagicMock()
        mock_process.communicate.return_value = ("", "")
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Mock os.path.exists to be True for the STL file, the slicer, and the process file,
        # but False for the machine profile
        def exists_side_effect(path):
            if path == "test.stl":
                return True
            if "OrcaSlicer" in path:
                return True
            if "process" in path and path.endswith(".json"):
                return True # Assuming standard profile exists
            return False # Machine profile or others fail

        mock_exists.side_effect = exists_side_effect

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 1)

        # We need to find what missing profile message is logged
        self.assertTrue(any("Fallback machine profile" in call[0][0] or "not found. Using standard P1P" in call[0][0] for call in mock_logger.warning.call_args_list))

    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.path.getsize')
    @patch('os.unlink')
    @patch('subprocess.Popen')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('json.load')
    @patch('json.dump')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.slicer.platform.system', new=lambda: _HOST_SYSTEM)
    def test_cmd_slice_success(self, *mocks):
        mock_logger, mock_json_dump, mock_json_load, mock_open = mocks[:4]
        mock_tempfile, mock_subprocess_run, mock_unlink, mock_getsize = mocks[4:8]
        mock_listdir, mock_isdir, mock_exists = mocks[8:11]
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1
        args.infill = 15
        args.pattern = "3dhoneycomb"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60

        # Let's just make all exists return True
        mock_exists.return_value = True

        # Mock json load
        mock_json_load.return_value = {}

        # Mock tempfile
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/mock_temp.json"
        mock_tempfile.return_value = mock_temp

        # Mock subprocess Popen
        mock_process = _setup_slice_proc(MagicMock())
        mock_subprocess_run.return_value = mock_process

        # Mock getsize
        mock_getsize.return_value = 10240 # 10KB

        cmd_slice(args)

        # Check success message
        self.assertTrue(any("✅ Sliced: ./test_sliced.3mf" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.path.getsize')
    @patch('os.unlink')
    @patch('subprocess.Popen')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('json.load')
    @patch('json.dump')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.slicer.platform.system', new=lambda: _HOST_SYSTEM)
    def test_cmd_slice_advanced_settings(self, *mocks):
        mock_logger, mock_json_dump, mock_json_load, mock_open = mocks[:4]
        mock_tempfile, mock_subprocess_run, mock_unlink, mock_getsize = mocks[4:8]
        mock_listdir, mock_isdir, mock_exists = mocks[8:11]
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1
        args.infill = 15
        args.pattern = "3dhoneycomb"
        args.supports = True
        args.support_type = "tree"
        args.support_interface_density = 50.0
        args.support_interface_pattern = "rectilinear"
        args.walls = 4
        args.wall_type = "archaic"
        args.top_layers = 5
        args.bottom_layers = 4
        args.accel_wall = 500
        args.accel_wall_outer = 300
        args.accel_infill = 800
        args.accel_travel = 1000
        args.accel_first_layer = 200
        args.nozzle_temp = 220
        args.bed_temp = 60

        mock_exists.return_value = True
        mock_json_load.return_value = {}

        mock_temp = MagicMock()
        mock_temp.name = "/tmp/mock_temp.json"
        mock_tempfile.return_value = mock_temp

        # Mock subprocess Popen
        mock_process = _setup_slice_proc(MagicMock())
        mock_subprocess_run.return_value = mock_process
        mock_getsize.return_value = 10240

        cmd_slice(args)

        # Verify advanced settings were passed to json.dump
        process_data = mock_json_dump.call_args_list[0][0][0]
        self.assertEqual(process_data['support_style'], 'tree')
        self.assertEqual(process_data['support_interface_density'], '50.0%')
        self.assertEqual(process_data['support_interface_pattern'], 'rectilinear')
        self.assertEqual(process_data['wall_loops'], '4')
        self.assertEqual(process_data['wall_generator'], 'classic')
        self.assertEqual(process_data['top_shell_layers'], '5')
        self.assertEqual(process_data['bottom_shell_layers'], '4')
        self.assertEqual(process_data['inner_wall_acceleration'], '500')
        self.assertEqual(process_data['outer_wall_acceleration'], '300')
        self.assertEqual(process_data['sparse_infill_acceleration'], '800')
        self.assertEqual(process_data['travel_acceleration'], '1000')
        self.assertEqual(process_data['initial_layer_acceleration'], '200')

        # Check success message with advanced settings
        self.assertTrue(any("✅ Sliced: ./test_sliced.3mf" in call[0][0] for call in mock_logger.info.call_args_list))


class TestResolvePrintablesUrl(unittest.TestCase):





    @patch("bambu_cli.bambu.logger")
    def test_get_printables_model_not_found(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": {"print": None}}).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with("Model #123 not found on Printables")

    @patch("bambu_cli.bambu.logger")
    def test_get_printables_no_valid_files(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": {"print": {"name": "Test", "stls": [{"id": "1", "name": "part1.txt", "fileSize": 1024}], "gcodes": []}}}).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with("No STL, STEP, or 3MF files found for this model")

    @patch("bambu_cli.bambu.logger")
    def test_get_printables_url_error(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import urllib.error
        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.URLError("Network unreachable")

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with("Network error querying Printables API: <urlopen error Network unreachable>")


    @patch('bambu_cli.bambu.logger')
    def test_get_printables_multiple_stls(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test",
                    "stls": [
                        {"id": "1", "name": "part1.stl", "fileSize": 1024},
                        {"id": "2", "name": "part2.stl", "fileSize": 2048}
                    ]
                }
            }
        }).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        self.assertEqual(ftype, "stl")
        mock_logger.info.assert_any_call("   Found 2 STL files:")

    @patch('bambu_cli.bambu.logger')
    def test_get_printables_multiple_steps(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test",
                    "stls": [
                        {"id": "1", "name": "part1.step", "fileSize": 1024},
                        {"id": "2", "name": "part2.step", "fileSize": 2048}
                    ]
                }
            }
        }).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        self.assertEqual(ftype, "stl")
        mock_logger.info.assert_any_call("   Found 2 STEP files:")

    @patch('bambu_cli.bambu.logger')
    def test_get_printables_3mf_fallback(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test",
                    "stls": [
                        {"id": "1", "name": "part1.3mf", "fileSize": 1024}
                    ],
                    "gcodes": [
                        {"id": "2", "name": "part2.3mf", "fileSize": 2048}
                    ]
                }
            }
        }).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        # 3MF from gcodes sets type="gcode"
        self.assertEqual(ftype, "gcode")

    @patch('bambu_cli.bambu.logger')
    def test_get_printables_generic_exception(self, mock_logger):
        from bambu_cli.bambu import _get_printables_file_info
        mock_opener = MagicMock()
        mock_opener.open.side_effect = Exception("Generic Fetch Error")

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        mock_logger.error.assert_called_with("Failed to query Printables API: Generic Fetch Error")

    @patch('bambu_cli.bambu.logger')
    def test_get_printables_download_link_error(self, mock_logger):
        from bambu_cli.bambu import _get_printables_download_link
        import json
        mock_opener = MagicMock()

        mock_resp = MagicMock()
        # Mock API returning None link
        mock_resp.read.return_value = json.dumps({
            "data": {
                "fileDownloadLink": None
            }
        }).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        result = _get_printables_download_link("1", "1", "stl", "name.stl", {}, mock_opener)
        self.assertEqual(result, (None, None))
        mock_logger.error.assert_called_with("Failed to get download link: unknown error")

        # Test exception path
        mock_opener.open.side_effect = Exception("Link Fetch Error")
        result = _get_printables_download_link("1", "1", "stl", "name.stl", {}, mock_opener)
        self.assertEqual(result, (None, None))
        mock_logger.error.assert_called_with("Failed to get download link: Link Fetch Error")

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_url_success(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        # First call: GraphQL query for model details
        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test Model",
                    "stls": [{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}],
                    "gcodes": []
                }
            }
        }).encode()

        # Second call: GraphQL mutation for download link
        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps({
            "data": {
                "getDownloadLink": {
                    "ok": True,
                    "output": {"link": "https://download.example.com/part1.stl"}
                }
            }
        }).encode()

        # Set side effect for urlopen context manager
        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.stl")
        self.assertEqual(filename, "part1.stl")

    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_url_not_printables(self, mock_logger):
        from bambu_cli.bambu import resolve_printables_url

        url = "https://www.thingiverse.com/thing:12345"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_model_not_found(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"data": {"print": None}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(any("Model #12345 not found on Printables" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_no_valid_files(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test Model",
                    "stls": [],
                    "gcodes": []
                }
            }
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(any("No STL, STEP, or 3MF files found for this model" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_prioritize_step(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test Model",
                    "stls": [{"name": "part1.step", "fileSize": 1024, "id": "file_123"}],
                    "gcodes": []
                }
            }
        }).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps({
            "data": {
                "getDownloadLink": {
                    "ok": True,
                    "output": {"link": "https://download.example.com/part1.step"}
                }
            }
        }).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.step")
        self.assertEqual(filename, "part1.step")

        self.assertTrue(any("→ Using STEP: part1.step (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_prioritize_3mf(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test Model",
                    "stls": [],
                    "gcodes": [{"name": "part1.3mf", "fileSize": 1024, "id": "file_123"}]
                }
            }
        }).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps({
            "data": {
                "getDownloadLink": {
                    "ok": True,
                    "output": {"link": "https://download.example.com/part1.3mf"}
                }
            }
        }).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.3mf")
        self.assertEqual(filename, "part1.3mf")

        self.assertTrue(any("falling back to 3MF" in call[0][0] for call in mock_logger.warning.call_args_list))
        self.assertTrue(any("→ Using 3MF: part1.3mf (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_download_link_error(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps({
            "data": {
                "print": {
                    "name": "Test Model",
                    "stls": [{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}],
                    "gcodes": []
                }
            }
        }).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps({
            "data": {
                "getDownloadLink": {
                    "ok": False,
                    "errors": [{"field": "link", "messages": ["Download limit reached"]}]
                }
            }
        }).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(any("Failed to get download link: Download limit reached" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_resolve_printables_exception(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import resolve_printables_url

        mock_urlopen.return_value.__enter__.side_effect = urllib.error.URLError("Network failure")

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(any("Network error querying Printables API" in call[0][0] for call in mock_logger.error.call_args_list))


class TestBambuCmdDownload(unittest.TestCase):

    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_invalid_output_dir(self, mock_logger):
        from bambu_cli.bambu import cmd_download
        args = MagicMock()
        args.url = "http://example.com/test.stl"
        args.output = "-invalid_dir"

        with self.assertRaises(SystemExit) as cm:
            cmd_download(args)
        self.assertEqual(cm.exception.code, 5)

        mock_logger.error.assert_called_with("Invalid output directory: -invalid_dir")

    @patch('urllib.request.Request')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    @patch('builtins.open', new_callable=mock_open)
    def test_cmd_download_sanitization_fallback(self, mock_file, mock_logger, mock_build, mock_req):
        from bambu_cli.bambu import cmd_download
        import urllib.request

        args = MagicMock()
        # Create a URL where os.path.basename(unquote(path)) evaluates to something invalid
        # For instance, URL path is just /.. or /... -> basename evaluates to ..
        args.url = "http://example.com/.."
        args.output = "/tmp/out"
        args.name = None

        mock_opener = MagicMock()
        mock_build.return_value = mock_opener
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"data", b""]
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        cmd_download(args)

        # Path should fall back to model.stl, then get appended to output
        mock_file.assert_called_with(os.path.join("/tmp/out", "model.stl"), 'wb')
        mock_logger.info.assert_any_call("⬇️  Downloading model.stl...")
    def setUp(self):
        self.safe_opener_patcher = patch('bambu_cli.download.build_safe_opener')
        self.mock_safe_opener = self.safe_opener_patcher.start()
        self.mock_safe_opener.return_value.open = MagicMock()
        self.exists_patcher = patch('os.path.exists', return_value=False)
        self.mock_exists = self.exists_patcher.start()
        self.getsize_patcher = patch('os.path.getsize', return_value=1024)
        self.mock_getsize = self.getsize_patcher.start()
        # These tests mock the filesystem, so collision-avoidance (which
        # creates real placeholder files) must be a pass-through.
        self.noncolliding_patcher = patch(
            'bambu_cli.download._noncolliding_path', side_effect=lambda p: p)
        self.noncolliding_patcher.start()

    def tearDown(self):
        self.safe_opener_patcher.stop()
        self.exists_patcher.stop()
        self.getsize_patcher.stop()
        self.noncolliding_patcher.stop()


    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_with_printables_url(self, mock_logger, mock_open, mock_getsize, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        # Mock resolve to return a resolved URL and filename
        mock_resolve.return_value = ("https://download.example.com/part1.stl", "part1.stl")

        args = MagicMock()
        args.url = "https://www.printables.com/model/12345"
        args.output = "."
        args.name = None

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.return_value.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        cmd_download(args)

        mock_resolve.assert_called_once_with("https://www.printables.com/model/12345")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(any("✅ Downloaded: ./part1.stl" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_direct_url_success(self, mock_logger, mock_open, mock_getsize, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.return_value.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        cmd_download(args)

        mock_resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(any("✅ Downloaded: ./model.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_custom_name(self, mock_logger, mock_open, mock_getsize, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = "custom.stl"

        mock_resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.return_value.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        cmd_download(args)

        mock_resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(any("✅ Downloaded: ./custom.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_printables_fail(self, mock_logger, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "https://www.printables.com/model/12345"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        with self.assertRaises(SystemExit) as cm:
            cmd_download(args)
        self.assertEqual(cm.exception.code, 5)

        mock_resolve.assert_called_once_with("https://www.printables.com/model/12345")
        mock_urlopen.assert_not_called()

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_http_error(self, mock_logger, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download
        import urllib.error

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        mock_urlopen.side_effect = urllib.error.HTTPError(url="https://example.com/model.stl", code=404, msg="Not Found", hdrs={}, fp=None)

        with self.assertRaises(SystemExit) as cm:
            cmd_download(args)
        self.assertEqual(cm.exception.code, 2)

        mock_resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        self.assertTrue(any("Download failed: HTTP Error 404" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_generic_error(self, mock_logger, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(SystemExit) as cm:
            cmd_download(args)
        self.assertEqual(cm.exception.code, 2)

        mock_resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        self.assertTrue(any("Network error during download: <urlopen error Connection refused>" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_missing_extension(self, mock_logger, mock_open, mock_getsize, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.return_value.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        cmd_download(args)

        mock_resolve.assert_called_once_with("https://example.com/model")
        mock_urlopen.assert_called_once()

        # Check success message with .stl appended
        self.assertTrue(any("✅ Downloaded: ./model.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_invalid_scheme(self, mock_logger, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        args = MagicMock()
        args.url = "file:///etc/passwd"
        args.output = "."
        args.name = None

        mock_resolve.return_value = (None, None)

        with self.assertRaises(SystemExit) as cm:
            cmd_download(args)
        self.assertEqual(cm.exception.code, 5)

        # urllib.request.urlopen should NOT be called
        mock_urlopen.assert_not_called()

        # Check for invalid scheme error message
        self.assertTrue(any("Invalid URL scheme: file" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.download.resolve_printables_url')
    @patch('bambu_cli.download.build_safe_opener')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_path_traversal_sanitization(self, mock_logger, mock_open, mock_getsize, mock_safe_opener, mock_resolve):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.bambu import cmd_download

        # A URL containing an encoded path traversal attempt
        args = MagicMock()
        args.url = "https://example.com/models/file.stl%2f..%2f..%2fetc%2fpasswd"
        args.output = "/tmp"
        args.name = None

        mock_resolve.return_value = (None, None)
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.return_value.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_getsize.return_value = 1024

        cmd_download(args)

        expected_filename = "passwd.stl"
        expected_path = os.path.join("/tmp", expected_filename)

        # open() is called with the sanitized native path (native separators).
        mock_open.assert_called_once_with(expected_path, 'wb')

        # The success log normalizes separators to '/' via _path_for_message, so
        # compare against a separately-normalized display string. This keeps the
        # native-path mock_open check above intact while matching the log on Windows.
        expected_display = expected_path.replace(os.sep, "/")
        self.assertTrue(any(f"✅ Downloaded: {expected_display}" in call[0][0] for call in mock_logger.info.call_args_list))


class TestCreateMqttClient(unittest.TestCase):

    def test_create_mqtt_client_simulation(self):
        from bambu_cli.bambu import create_mqtt_client
        client = create_mqtt_client(_test_printer(simulation_mode=True))
        from bambu_cli.protocols.mqtt import _SimMqttClient
        self.assertIsInstance(client, _SimMqttClient)

    @patch('bambu_cli.protocols.mqtt.mqtt.Client')
    def test_create_mqtt_client_secure(self, mock_mqtt_client):
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        printer = _test_printer(access_code='mock_access_code')
        client = create_mqtt_client(printer, "test_client")

        # Use ANY for the version argument to avoid identity mismatches with module-level mocks
        mock_mqtt_client.assert_called_once_with(ANY, "test_client")
        mock_client_instance.username_pw_set.assert_called_once_with('bblp', 'mock_access_code')
        mock_client_instance.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_REQUIRED)
        mock_client_instance.tls_insecure_set.assert_not_called()
        self.assertEqual(client, mock_client_instance)

    @patch('bambu_cli.protocols.mqtt.mqtt.Client')
    def test_create_mqtt_client_insecure(self, mock_mqtt_client):
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        printer = _test_printer(access_code='mock_access_code', insecure_tls=True)
        client = create_mqtt_client(printer)

        mock_mqtt_client.assert_called_once_with(ANY, "")
        mock_client_instance.username_pw_set.assert_called_once_with('bblp', 'mock_access_code')
        mock_client_instance.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_NONE)
        mock_client_instance.tls_insecure_set.assert_called_once_with(True)
        self.assertEqual(client, mock_client_instance)



class TestBambuCmdGcode(unittest.TestCase):

    @patch('bambu_cli.bambu.send_command')
    @patch('sys.exit')
    def test_cmd_gcode_send_command_fail(self, mock_exit, mock_send):
        from bambu_cli.bambu import cmd_gcode
        mock_send.return_value = False
        args = MagicMock()
        args.code = "G28"

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises(SystemExit) as cm:
            cmd_gcode(args)

        self.assertEqual(cm.exception.code, 2)

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.bambu.send_command')
    def test_cmd_gcode(self, mock_send_command, mock_seq):
        from bambu_cli.bambu import cmd_gcode

        args = MagicMock()
        args.code = "M104 S220"

        cmd_gcode(args)

        # Expected payload
        expected_payload = json.dumps({
            "print": {
                "sequence_id": "0",
                "command": "gcode_line",
                "param": "M104 S220"
            }
        })

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)

class TestBambuGetStatus(unittest.TestCase):

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    def test_get_status_on_connect_rc_error(self, mock_logger, mock_create):
        from bambu_cli.bambu import get_status
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 5)

        mock_client.connect.side_effect = side_effect_connect

        result = get_status(_test_printer(), timeout=0.1)

        self.assertIsNone(result)
        mock_logger.error.assert_called_with("Connection failed: rc=5")

    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_status_connect_fail(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.bambu import cmd_status
        mock_get_status.return_value = None
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit) as cm:
            cmd_status(MagicMock())

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_called_with("Could not connect to printer.")

    @patch('bambu_cli.utils.emit_json')
    @patch('bambu_cli.protocols.mqtt.get_status')
    @patch('bambu_cli.commands.logger')
    def test_cmd_status_json_output(self, mock_logger, mock_get_status, mock_emit_json):
        from bambu_cli.bambu import cmd_status
        mock_get_status.return_value = {"gcode_state": "IDLE"}

        args = MagicMock()
        args.json = True
        args.monitor = False

        cmd_status(args)

        mock_emit_json.assert_called_once()
        payload = mock_emit_json.call_args[0][0]
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["command"], "status")
        self.assertEqual(payload["gcode_state"], "IDLE")

    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_status_running_formatting(self, mock_logger, mock_get_status):
        from bambu_cli.bambu import cmd_status
        mock_get_status.return_value = {
            "gcode_state": "RUNNING",
            "gcode_file": "test.gcode",
            "mc_percent": 50,
            "layer_num": 10,
            "total_layer_num": 20,
            "mc_remaining_time": 125,
            "bed_temper": 60,
            "bed_target_temper": 60,
            "nozzle_temper": 220,
            "nozzle_target_temper": 220,
            "cooling_fan_speed": 100,
            "wifi_signal": "-50dBm"
        }

        args = MagicMock()
        args.json = False

        cmd_status(args)

        mock_logger.info.assert_any_call("   File: test.gcode")
        mock_logger.info.assert_any_call("   Progress: 50% | Layer 10/20")
        mock_logger.info.assert_any_call("   Time left: 2h 5m")

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('time.sleep')
    def test_get_status_success(self, mock_sleep, mock_create_mqtt):
        from bambu_cli.bambu import get_status
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            # Call on_connect directly
            mock_client.on_connect(mock_client, None, None, 0)

            # Simulate a message arriving with 'print' data
            msg = MagicMock()
            msg.payload = json.dumps({"print": {"status": "idle"}}).encode()
            mock_client.on_message(mock_client, None, msg)

        mock_client.connect.side_effect = mock_connect

        result = get_status(_test_printer(), timeout=1)

        self.assertEqual(result, {"status": "idle"})
        mock_create_mqtt.assert_called_once()
        mock_client.connect.assert_called_once()
        mock_client.subscribe.assert_called_once()
        mock_client.publish.assert_called_once()
        mock_client.disconnect.assert_called()

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('time.sleep')
    @patch('bambu_cli.bambu.logger')
    def test_get_status_timeout(self, mock_logger, mock_sleep, mock_create_mqtt):
        from bambu_cli.bambu import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)

        mock_client.connect.side_effect = mock_connect

        # No status message ever arrives -> 3 attempts (2 retries)
        result = get_status(_test_printer(), timeout=0.0001)

        self.assertIsNone(result)
        self.assertEqual(mock_client.connect.call_count, 3)

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    @patch('time.sleep')
    def test_get_status_connection_failure(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.bambu import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        # Mock connect to raise an exception
        mock_client.connect.side_effect = OSError("Connection error")

        result = get_status(_test_printer(), timeout=0.0001)

        self.assertIsNone(result)
        self.assertTrue(any("MQTT status error: Connection error" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    def test_get_status_ignore_non_print_messages(self, mock_create_mqtt):
        from bambu_cli.bambu import get_status
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)

            # Send message without 'print' key
            msg1 = MagicMock()
            msg1.payload = json.dumps({"other": "data"}).encode()
            mock_client.on_message(mock_client, None, msg1)

            # Send invalid JSON
            msg2 = MagicMock()
            msg2.payload = b"invalid json"
            mock_client.on_message(mock_client, None, msg2)

            # Send valid print message
            msg3 = MagicMock()
            msg3.payload = json.dumps({"print": {"status": "printing"}}).encode()
            mock_client.on_message(mock_client, None, msg3)

        mock_client.connect.side_effect = mock_connect

        with patch('time.sleep'):
            result = get_status(_test_printer(), timeout=1)

        self.assertEqual(result, {"status": "printing"})

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    @patch('time.sleep')
    def test_get_status_exception(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.bambu import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        mock_client.connect.side_effect = OSError("Network error")

        result = get_status(_test_printer(), timeout=1)

        self.assertIsNone(result)
        self.assertTrue(any("MQTT status error: Network error" in call[0][0] for call in mock_logger.error.call_args_list))

class TestBambuCmdPrint(unittest.TestCase):

    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_execute_print_command_dry_run_file_not_found(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.bambu import execute_print_command
        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["other.3mf"]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp

        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises(SystemExit) as cm:
            execute_print_command(printer, "payload", "missing.3mf", dry_run=True)

        self.assertEqual(cm.exception.code, 3)
        mock_logger.error.assert_any_call("   ❌ File missing.3mf NOT found on printer. Upload it first.")

    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_execute_print_command_dry_run_mqtt_fail(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.bambu import execute_print_command
        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["test.3mf"]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp

        mock_get_status.return_value = None

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises(SystemExit) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_any_call("   ❌ MQTT connection failed.")

    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_execute_print_command_dry_run_exception(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.bambu import execute_print_command
        printer = _test_printer()
        printer.get_ftp_client = MagicMock(side_effect=OSError("FTP Error"))

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises(SystemExit) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_any_call("Dry run failed: FTP Error")

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.time.sleep')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_execute_print_command_non_sd_error(self, mock_exit, mock_logger, mock_sleep, mock_create):
        from bambu_cli.bambu import execute_print_command
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def fake_connect(ip, port, keepalive):
            # simulate receiving message with error 1234
            msg = MagicMock()
            msg.payload = b'{"print": {"print_error": 1234}}'
            mock_client.on_message(mock_client, None, msg)

        mock_client.connect.side_effect = fake_connect

        mock_exit.side_effect = SystemExit(4)

        with self.assertRaises(SystemExit) as cm:
            execute_print_command(_test_printer(), "payload", "test.3mf", dry_run=False)

        self.assertEqual(cm.exception.code, 4)
        mock_logger.error.assert_called_with("Print failed with error code 1234")
    def test_generate_print_payload(self):
        from bambu_cli.bambu import generate_print_payload
        import json

        basename = "test_model.gcode"
        payload = generate_print_payload(basename)

        parsed = json.loads(payload)
        self.assertIn("print", parsed)
        self.assertEqual(parsed["print"]["subtask_name"], "test_model.gcode")
        self.assertEqual(parsed["print"]["url"], "file:///sdcard/model/test_model.gcode")

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    @patch('time.sleep')
    def test_execute_print_command_success(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.bambu import execute_print_command
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        # Simulate on_connect
        def trigger_on_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            msg = MagicMock()
            msg.payload = b'{"print": {"command": "project_file"}}'
            mock_client.on_message(mock_client, None, msg)
        mock_client.connect.side_effect = trigger_on_connect

        payload = '{"test": "payload"}'
        basename = "test_model.gcode"

        printer = _test_printer()
        execute_print_command(printer, payload, basename)

        mock_create_mqtt.assert_called_once_with(printer, "bambu_print")
        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

        # Check success log
        self.assertTrue(any(f"🖨️  Print started: {basename}" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    @patch('time.sleep')
    @patch('sys.exit')
    def test_execute_print_command_with_error(self, mock_exit, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.bambu import execute_print_command
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        mock_exit.side_effect = SystemExit(3)

        # Simulate receiving an error message
        def trigger_on_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            # Simulate on_message with error code
            msg = MagicMock()
            msg.payload = json.dumps({"print": {"print_error": 83935248}}).encode()
            mock_client.on_message(mock_client, None, msg)

        mock_client.connect.side_effect = trigger_on_connect

        payload = '{"test": "payload"}'
        basename = "test_model.gcode"

        with self.assertRaises(SystemExit):
            execute_print_command(_test_printer(), payload, basename)

        self.assertTrue(any("Print failed with error code 83935248" in call[0][0] for call in mock_logger.error.call_args_list))
        self.assertTrue(any("File not found on printer SD card" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.protocols.mqtt.create_mqtt_client')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    @patch('time.sleep')
    def test_execute_print_command_exception(self, mock_sleep, mock_exit, mock_logger, mock_create_mqtt):
        from bambu_cli.bambu import execute_print_command
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        mock_client.connect.side_effect = OSError("Connection refused")
        mock_exit.side_effect = SystemExit(2)

        payload = '{"test": "payload"}'
        basename = "test_model.gcode"

        with self.assertRaises(SystemExit):
            execute_print_command(_test_printer(), payload, basename)

        self.assertTrue(any("Error: Connection refused" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.bambu.generate_print_payload')
    @patch('bambu_cli.bambu.execute_print_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_print_no_confirm(self, mock_logger, mock_execute, mock_generate):
        from bambu_cli.bambu import cmd_print

        args = MagicMock()
        args.confirm = False
        args.file = "test.gcode"
        args.dry_run = False
        args.ams_mapping = None

        cmd_print(args)

        mock_generate.assert_not_called()
        mock_execute.assert_not_called()

        self.assertTrue(any("⚠️  This will START a print. Add --confirm to proceed." in call[0][0] for call in mock_logger.warning.call_args_list))

    @patch('bambu_cli.bambu.generate_print_payload')
    @patch('bambu_cli.bambu.execute_print_command')
    def test_cmd_print_with_confirm(self, mock_execute, mock_generate):
        from bambu_cli.bambu import cmd_print

        args = MagicMock()
        args.confirm = True
        args.file = "test.gcode"
        args.dry_run = False
        args.ams_mapping = None

        mock_generate.return_value = "test_payload"

        cmd_print(args)

        mock_generate.assert_called_once_with(
            "test.gcode",
            use_ams=args.use_ams,
            ams_mapping=None,
            timelapse=args.timelapse,
            bed_leveling=False,
            flow_cali=False
        )
        mock_execute.assert_called_once_with(ANY, "test_payload", "test.gcode", dry_run=False)

class TestGrabCameraFrameDirect(unittest.TestCase):

    def _mock_net(self, mock_ssl, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls
        mock_ssl.return_value = mock_ctx
        mock_tls.recv.side_effect = [
            # first recv: size header (16 bytes)
            (4).to_bytes(4, "little") + b"\x00" * 12,
            # second recv: 4 bytes data representing valid JPEG
            b"\xff\xd8\xff\xd9",
        ]
        return mock_sock, mock_tls, mock_ctx

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_no_pin_fails_closed(self, mock_ssl, mock_create_conn):
        """Without a pinned fingerprint (and insecure_tls unset) the camera
        connection must fail closed before the access code is sent."""
        import ssl as ssl_mod
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code")

        with self.assertRaises(ssl_mod.SSLError):
            _grab_camera_frame_direct(printer)
        mock_tls.sendall.assert_not_called()

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_insecure(self, mock_ssl, mock_create_conn):
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", insecure_tls=True)

        res = _grab_camera_frame_direct(printer)
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_create_conn.assert_called_once_with(("192.168.1.100", 6000), timeout=12)
        mock_ctx.wrap_socket.assert_called_once_with(mock_sock, server_hostname="192.168.1.100")
        mock_tls.sendall.assert_called_once()
        mock_tls.getpeercert.assert_not_called()

    @patch('bambu_cli.config.fingerprint_sha256')
    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_with_pin(self, mock_ssl, mock_create_conn, mock_fp):
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "mock_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code",
                                cert_fingerprint="mock_fingerprint")

        res = _grab_camera_frame_direct(printer)
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_tls.getpeercert.assert_called_once_with(binary_form=True)
        mock_fp.assert_called_once_with(b"der_cert")

    @patch('bambu_cli.config.fingerprint_sha256')
    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_pin_mismatch(self, mock_ssl, mock_create_conn, mock_fp):
        import ssl as ssl_mod
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "wrong_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code",
                                cert_fingerprint="mock_fingerprint")

        with self.assertRaises(ssl_mod.SSLError):
            _grab_camera_frame_direct(printer)
        mock_tls.sendall.assert_not_called()

class TestBambuCmdSnapshot(unittest.TestCase):

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_invalid_output_path(self, mock_exit, mock_logger):
        from bambu_cli.bambu import cmd_snapshot
        args = MagicMock()
        args.output = "-invalid.jpg"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises(SystemExit) as cm:
            cmd_snapshot(args)

        self.assertEqual(cm.exception.code, 3)
        mock_logger.error.assert_called_with("Invalid output path: -invalid.jpg")

    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('bambu_cli.bambu.subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_url_error(self, mock_exit, mock_logger, mock_urlopen, mock_run, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot
        import urllib.error

        args = MagicMock()
        args.output = "snap.jpg"

        # mock docker running
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen.side_effect = urllib.error.URLError("Network Error")
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(SystemExit) as cm:
            cmd_snapshot(args)

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_called_with("Snapshot network error: <urlopen error Network Error>")

    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('bambu_cli.bambu.subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_generic_error(self, mock_exit, mock_logger, mock_urlopen, mock_run, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot

        args = MagicMock()
        args.output = "snap.jpg"

        # mock docker running
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen.side_effect = Exception("Generic Error")
        mock_exit.side_effect = SystemExit(5)

        with self.assertRaises(SystemExit) as cm:
            cmd_snapshot(args)

        self.assertEqual(cm.exception.code, 5)
        mock_logger.error.assert_called_with("Snapshot failed: Generic Error")
    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('bambu_cli.bambu.load_access_code')
    def test_cmd_snapshot_start_container(self, mock_load_access, mock_logger, mock_urlopen, mock_subproc, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot

        # 1st call: docker inspect (returns not running)
        # 2nd call: docker rm
        # 3rd call: docker run
        mock_subproc.side_effect = [
            MagicMock(returncode=1), # inspect fails
            MagicMock(returncode=0), # rm
            MagicMock(returncode=0)  # run
        ]

        mock_load_access.return_value = "MOCK_CODE"

        args = MagicMock()
        args.output = "snap.jpg"

        # Mock urlopen success
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"image data", b""]
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Need to mock os.path.exists for the temp file and the output file
        with patch('os.path.exists', return_value=True), \
             patch('os.fdopen', mock_open()), \
             patch('os.unlink'), \
             patch('time.sleep'), \
             patch('os.path.getsize', return_value=2048), \
             patch('bambu_cli.camera._write_snapshot_atomic'), \
             patch('builtins.open', new_callable=mock_open):
            cmd_snapshot(args)

        self.assertTrue(any("🔄 Starting camera streamer..." in call[0][0] for call in mock_logger.info.call_args_list))
        self.assertTrue(any("✅ Snapshot saved: snap.jpg (2KB)" in call[0][0] for call in mock_logger.info.call_args_list))

        # Verify docker run was called
        run_call = [call for call in mock_subproc.call_args_list if "run" in call[0][0]][0]
        self.assertIn("bambu_camera", run_call[0][0])
        self.assertIn("-e", run_call[0][0])
        self.assertIn("PRINTER_ACCESS_CODE", run_call[0][0])

class TestBambuDoctor(unittest.TestCase):


    @patch('bambu_cli.bambu.load_config')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_config_load_fail(self, mock_logger, mock_exit, mock_load):
        from bambu_cli.bambu import cmd_doctor
        mock_load.side_effect = SystemExit(1)
        mock_exit.side_effect = SystemExit(1)

        with self.assertRaises(SystemExit) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(cm.exception.code, 1)
        mock_logger.error.assert_any_call("   ❌ Config check failed.")

    @patch('bambu_cli.bambu.load_config')
    @patch('bambu_cli.bambu.get_status')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_mqtt_fail(self, mock_logger, mock_exit, mock_get_status, mock_load):
        from bambu_cli.bambu import cmd_doctor, PRINTER_IP
        mock_load.return_value = {"printer_ip": "1.2.3.4"}
        mock_get_status.return_value = None

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises(SystemExit) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_any_call(f"   ❌ MQTT connection failed. Ensure printer at {PRINTER_IP} is on and access code is correct.")

    @patch('bambu_cli.bambu.load_config')
    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.get_ftp')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_ftps_fail(self, mock_logger, mock_exit, mock_get_ftp, mock_get_status, mock_load):
        from bambu_cli.bambu import cmd_doctor
        mock_load.return_value = {"printer_ip": "1.2.3.4"}
        mock_get_status.return_value = {"hw_ver": "P1P"}

        mock_get_ftp.side_effect = OSError("FTPS Fail")

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises(SystemExit) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(cm.exception.code, 2)
        mock_logger.error.assert_any_call("   ❌ FTPS connection failed: FTPS Fail")
    @patch('bambu_cli.bambu.get_status')
    @patch('bambu_cli.bambu.get_ftp')
    @patch('bambu_cli.bambu.logger')
    @patch('builtins.open')
    def test_cmd_doctor_success(self, mock_file_open, mock_logger, mock_get_ftp, mock_get_status):
        from bambu_cli.bambu import cmd_doctor
        import tempfile
        args = MagicMock()
        args.output = None  # let the command fall back to its system-temp default

        import io
        original_open = io.open
        def custom_open(file, *args, **kwargs):
            if "config.json" in str(file):
                return original_open(file, *args, **kwargs)
            return MagicMock()
        mock_file_open.side_effect = custom_open

        mock_get_status.return_value = {"hw_ver": "P1P", "sw_ver": "01.05.00.00", "ams": {}}
        mock_get_ftp.return_value.__enter__.return_value = MagicMock()

        cmd_doctor(args)

        self.assertTrue(any("✅ All checks passed!" in call[0][0] for call in mock_logger.info.call_args_list))
        expected_path = os.path.join(tempfile.gettempdir(), "printer_capabilities.json")
        any_caps_open = any(
            call[0][0] == expected_path and call[0][1] == 'w'
            for call in mock_file_open.call_args_list
        )
        self.assertTrue(any_caps_open, f"Expected {expected_path} to be opened for writing")

class TestBambuUploadRetry(unittest.TestCase):
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.printer.logger')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('builtins.open', new_callable=mock_open)
    @patch('bambu_cli.bambu.logger')
    @patch('time.sleep')
    def test_cmd_upload_retry_success(self, mock_sleep, mock_logger, mock_file_open, mock_getsize, mock_exists, mock_printer_logger, mock_get_printer):
        from bambu_cli.bambu import cmd_upload
        args = MagicMock()
        args.file = "test.3mf"
        args.dry_run = False

        mock_exists.return_value = True
        mock_getsize.return_value = 2048

        mock_ftp = MagicMock()
        # Fail once, then succeed
        mock_ftp.storbinary.side_effect = [OSError("Timeout"), None]
        mock_ftp.size.return_value = 0
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)

        self.assertEqual(mock_ftp.storbinary.call_count, 2)
        self.assertTrue(any("⚠️ Upload attempt 1 failed" in call[0][0] for call in mock_printer_logger.warning.call_args_list))
        self.assertTrue(any("✅ Uploaded test.3mf to printer" in call[0][0] for call in mock_logger.info.call_args_list))

class TestBambuDryRun(unittest.TestCase):
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.bambu.get_status')
    def test_cmd_print_dry_run_success(self, mock_get_status, mock_get_printer):
        from bambu_cli.bambu import cmd_print
        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = False
        args.dry_run = True
        args.ams_mapping = None
        args.use_ams = False
        args.timelapse = False
        args.skip_bed_leveling = False
        args.skip_flow_cali = False

        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["test.3mf"]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_get_status.return_value = {"status": "idle"}

        # Should not raise SystemExit
        cmd_print(args)

        mock_ftp.nlst.assert_called_once_with('/model/')
        mock_get_status.assert_called_once()

class TestBambuSimulation(unittest.TestCase):
    @patch('bambu_cli.bambu.logger')
    @patch('bambu_cli.bambu.ImplicitFTPS')
    def test_simulation_mode_status(self, mock_ftps, mock_logger):
        from bambu_cli.bambu import cmd_status
        import bambu_cli.bambu as bambu

        args = MagicMock()
        args.json = False
        args.verbose = False
        args.sim = True

        # Manually enable sim mode for the test
        bambu.SIMULATION_MODE = True
        try:
            cmd_status(args)
            self.assertTrue(any("Fetching simulated printer status" in call[0][0] for call in mock_logger.info.call_args_list))
            self.assertTrue(any("State: IDLE" in call[0][0] for call in mock_logger.info.call_args_list))
        finally:
            bambu.SIMULATION_MODE = False

    @patch('bambu_cli.logging_utils.logger')
    @patch('bambu_cli.commands.logger')
    def test_simulation_mode_upload(self, mock_commands_logger, mock_ftps_logger):
        from bambu_cli.bambu import cmd_upload
        import bambu_cli.bambu as bambu

        args = MagicMock()
        args.file = "test.3mf"
        args.dry_run = False
        args.sim = True

        from bambu_cli.protocols.ftps import connection_manager
        connection_manager.clear()
        
        bambu.SIMULATION_MODE = True
        try:
            with patch('os.path.exists', return_value=True), \
                 patch('os.path.getsize', return_value=1024), \
                 patch('builtins.open', mock_open(read_data=b"data")):
                cmd_upload(args)

            self.assertTrue(any("Connecting to simulated FTPS server" in call[0][0] for call in mock_ftps_logger.info.call_args_list))
            self.assertTrue(any("Uploaded test.3mf to printer" in call[0][0] for call in mock_commands_logger.info.call_args_list))
        finally:
            bambu.SIMULATION_MODE = False
            connection_manager.clear()

class TestBambuSecurity(unittest.TestCase):
    @patch('bambu_cli.bambu.logger')
    def test_cmd_download_path_traversal_evasion(self, mock_logger):
        from bambu_cli.bambu import cmd_download
        args = MagicMock()
        args.url = "https://example.com/file.stl%00../../../../etc/passwd"
        args.output = "/tmp"
        args.name = None

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"data", b""]
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp
        with patch('bambu_cli.download.build_safe_opener', return_value=mock_opener), \
             patch('builtins.open', mock_open()), \
             patch('bambu_cli.download._noncolliding_path', side_effect=lambda p: p), \
             patch('os.path.getsize', return_value=1024):
            cmd_download(args)

        self.assertTrue(any("/tmp/passwd.stl" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('bambu_cli.bambu.logger')
    @patch('json.dump')
    def test_slice_parameter_injection_safety(self, mock_json_dump, mock_logger):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1 # Ensure copies is an int
        # Inject malicious string into a parameter that gets dumped to JSON
        args.support_interface_pattern = 'rectilinear", "malicious": "injected'

        mock_proc = _setup_slice_proc(MagicMock())

        original_open = io.open
        original_load = json.load

        def custom_open(file, *args, **kwargs):
            if "config.json" in str(file):
                return original_open(file, *args, **kwargs)
            m = mock_open(read_data='{}')
            return m(file, *args, **kwargs)

        def custom_load(fp, *args, **kwargs):
            try:
                if hasattr(fp, 'name') and "config.json" in str(fp.name):
                    return original_load(fp, *args, **kwargs)
            except Exception:
                pass
            return {}

        with patch('os.path.exists', return_value=True), \
             patch('os.access', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('os.listdir', return_value=["0.20mm Standard @BBL P1P.json"]), \
             patch('builtins.open', side_effect=custom_open), \
             patch('json.load', side_effect=custom_load), \
             patch('tempfile.NamedTemporaryFile'), \
             patch('subprocess.Popen', return_value=mock_proc), \
             patch('os.unlink'), \
             patch('os.path.getsize', return_value=1024):
            cmd_slice(args)

        # Verify that json.dump was called and it escaped the quotes
        process_data = mock_json_dump.call_args_list[0][0][0]
        self.assertEqual(process_data['support_interface_pattern'], 'rectilinear", "malicious": "injected')







class TestConvertStepToStl(unittest.TestCase):

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_success(self, mock_logger, mock_getsize, mock_exists, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_exists.return_value = True
        mock_getsize.return_value = 2048

        mock_conv = MagicMock()
        mock_conv.returncode = 0
        mock_run.return_value = mock_conv

        import os
        abs_step = os.path.abspath("test.step")
        abs_stl = os.path.abspath("test.stl")
        stl_path, success = _convert_step_to_stl("test.step")

        self.assertTrue(success)
        self.assertTrue(stl_path.endswith("test_.stl"))
        self.assertIn("bambu_step_", stl_path)

        self.assertEqual(mock_run.call_count, 1)
        cmd_run = mock_run.call_args[0][0]
        self.assertIn("gmsh", cmd_run)
        self.assertIn(abs_step, cmd_run)
        self.assertIn("-o", cmd_run)
        out_idx = cmd_run.index("-o") + 1
        self.assertEqual(cmd_run[out_idx], stl_path)
        mock_logger.info.assert_called_with(f"   Converted: {os.path.basename(stl_path)} (2KB)")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_failure_return_code(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_conv = MagicMock()
        mock_conv.returncode = 1
        mock_run.return_value = mock_conv

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed.")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_failure_no_file(self, mock_logger, mock_exists, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_exists.return_value = False

        mock_conv = MagicMock()
        mock_conv.returncode = 0
        mock_run.return_value = mock_conv

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed.")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_filenotfounderror(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_run.side_effect = FileNotFoundError()

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed. Please install gmsh for your platform.")

class TestBambuCmdSliceEdgeCases(unittest.TestCase):
    def setUp(self):
        self.access_patcher = patch('os.access', return_value=True)
        self.mock_access = self.access_patcher.start()

    def tearDown(self):
        self.access_patcher.stop()

    @patch('bambu_cli.bambu._convert_step_to_stl')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_step_conversion_error(self, mock_logger, mock_exists, mock_convert):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True
        mock_convert.return_value = (None, False)

        args = MagicMock()
        args.file = "test.step"

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 5)

        mock_convert.assert_called_once_with("test.step")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_convert_step_to_stl_file_not_found(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_run.side_effect = FileNotFoundError("gmsh not found")

        filepath, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        mock_logger.error.assert_called_with("STEP conversion failed. Please install gmsh for your platform.")

    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_invalid_output_dir(self, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True

        args = MagicMock()
        args.file = "test.stl"
        args.output = "-invalid_dir"

        with self.assertRaises(SystemExit) as cm:
            cmd_slice(args)
        self.assertEqual(cm.exception.code, 5)

        mock_logger.error.assert_called_with("Invalid output directory: -invalid_dir")

    @patch('subprocess.Popen')
    @patch('bambu_cli.slicer._create_temp_profiles')
    @patch('os.unlink')
    @patch('os.path.getsize', return_value=1024)
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.bambu.PROFILES_DIR', '/tmp')
    @patch('bambu_cli.bambu.ORCA_SLICER', '/tmp/orca')
    def test_cmd_slice_copies_logic(self, mock_logger, mock_exists, mock_getsize, mock_unlink, mock_create, mock_run):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True

        mock_process = MagicMock()
        mock_process.name = "process.json"
        mock_filament = MagicMock()
        mock_filament.name = "filament.json"
        mock_create.return_value = (mock_process, mock_filament)

        # Mock Popen
        mock_proc = _setup_slice_proc(MagicMock())
        mock_run.return_value = mock_proc

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 2
        args.quality = "standard"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60
        args.infill = 15
        args.pattern = "grid"

        with patch('platform.system', return_value="Windows"):
            cmd_slice(args)

        call_args = mock_run.call_args[0][0]
        self.assertIn("--arrange", call_args)
        self.assertIn("1", call_args)

        outfile_idx = call_args.index("--export-3mf") + 1
        self.assertEqual(call_args[outfile_idx], "test_x2_sliced.3mf")

    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_slice_orca_slicer_not_found(self, mock_exit, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        def fake_exists(path):
            if "orca" in path.lower():
                return False
            return True
        mock_exists.side_effect = fake_exists

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 1

        mock_exit.side_effect = SystemExit(1)

        with patch('bambu_cli.bambu.ORCA_SLICER', '/tmp/missing_orca'):
            with self.assertRaises(SystemExit):
                cmd_slice(args)

        mock_logger.error.assert_called_with("OrcaSlicer not found at /tmp/missing_orca")

    @patch('os.listdir')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.bambu.PROFILES_DIR', '/tmp')
    def test_cmd_slice_discover_process_profile_branches(self, mock_logger, mock_exists, mock_isdir, mock_listdir):
        from bambu_cli.bambu import _discover_process_profile
        mock_isdir.return_value = True

        mock_listdir.return_value = ["0.20mm Standard @BBL P1P.json"]

        quality_map = {"standard": "0.20mm Standard @BBL P1P"}
        result = _discover_process_profile("standard", quality_map)
        self.assertTrue("0.20mm Standard @BBL P1P.json" in result)

        mock_listdir.return_value = ["0.20mm Extra @BBL P1P.json"]

        result = _discover_process_profile("missing", quality_map)
        self.assertTrue("0.20mm Extra @BBL P1P.json" in result)

        # Test fallback to 0.20mm when requested layer height is not found
        mock_listdir.return_value = ["0.20mm Extra @BBL P1P.json"]
        result = _discover_process_profile("0.16mm", quality_map)
        self.assertTrue("0.20mm Extra @BBL P1P.json" in result)
        mock_logger.warning.assert_called_with("⚠️  Requested quality not found, using: 0.20mm Extra @BBL P1P.json")

        # Test no slicer profiles found at all
        mock_listdir.return_value = []
        result = _discover_process_profile("standard", quality_map)
        self.assertIsNone(result)
        mock_logger.error.assert_called_with(f"No slicer profiles found in {os.path.join('/tmp', 'process')}")

        # Test directory does not exist
        mock_isdir.return_value = False
        result = _discover_process_profile("standard", quality_map)
        self.assertIsNone(result)

    @patch('subprocess.Popen')
    @patch('bambu_cli.slicer._create_temp_profiles')
    @patch('os.unlink')
    @patch('os.path.getsize', return_value=1024)
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.bambu.PROFILES_DIR', '/tmp')
    @patch('bambu_cli.bambu.ORCA_SLICER', '/tmp/orca')
    def test_cmd_slice_error_message_parsing(self, mock_logger, mock_exists, mock_getsize, mock_unlink, mock_create, mock_run):
        from bambu_cli.bambu import cmd_slice

        def fake_exists(path):
            if path == os.path.join("/tmp/out", "test_sliced.3mf"):
                return False
            return True
        mock_exists.side_effect = fake_exists

        mock_process = MagicMock()
        mock_process.name = "process.json"
        mock_filament = MagicMock()
        mock_filament.name = "filament.json"
        mock_create.return_value = (mock_process, mock_filament)

        # Mock Popen
        mock_proc = _setup_slice_proc(
            MagicMock(),
            returncode=1,
            stdout=b"2024-01-01 ] [error] Missing wall settings\n2024-01-01 nothing to be sliced",
        )
        mock_run.return_value = mock_proc

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 1
        args.quality = "standard"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60
        args.infill = 15
        args.pattern = "grid"

        with patch('platform.system', return_value="Windows"):
            with self.assertRaises(SystemExit) as cm:
                cmd_slice(args)
            self.assertEqual(cm.exception.code, 5)

        mock_logger.error.assert_any_call("   Missing wall settings")
        mock_logger.error.assert_any_call("   2024-01-01 nothing to be sliced")

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

if __name__ == '__main__':
    unittest.main()
