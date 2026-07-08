from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestBambuDoctor(unittest.TestCase):


    @patch('bambu_cli.bambu.load_config')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_config_load_fail(self, mock_logger, mock_exit, mock_load):
        from bambu_cli.bambu import cmd_doctor
        from bambu_cli.errors import ConfigError

        mock_load.side_effect = ConfigError("config missing")

        with self.assertRaises(BambuError) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(cm.exception.exit_code, 1)
        mock_logger.error.assert_any_call("   ❌ Config check failed.")

    @patch('bambu_cli.bambu.load_config')
    @patch('bambu_cli.protocols.mqtt.get_status')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_mqtt_fail(self, mock_logger, mock_exit, mock_get_status, mock_load):
        from bambu_cli.bambu import cmd_doctor
        from bambu_cli.context import current_settings
        mock_load.return_value = {"printer_ip": "1.2.3.4"}
        mock_get_status.return_value = None

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_any_call(f"   ❌ MQTT connection failed. Ensure printer at {current_settings().printer_ip} is on and access code is correct.")

    @patch('bambu_cli.bambu.load_config')
    @patch('bambu_cli.protocols.mqtt.get_status')
    @patch('bambu_cli.protocols.ftps.get_ftp')
    @patch('sys.exit')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_doctor_ftps_fail(self, mock_logger, mock_exit, mock_get_ftp, mock_get_status, mock_load):
        from bambu_cli.bambu import cmd_doctor
        mock_load.return_value = {"printer_ip": "1.2.3.4"}
        mock_get_status.return_value = {"hw_ver": "P1P"}

        mock_get_ftp.side_effect = OSError("FTPS Fail")

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_doctor(MagicMock())

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_any_call("   ❌ FTPS connection failed: FTPS Fail")
    @patch('bambu_cli.protocols.mqtt.get_status')
    @patch('bambu_cli.protocols.ftps.get_ftp')
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


class TestOfferPinFingerprint(unittest.TestCase):
    """The doctor cert-fingerprint auto-pin offer (1.3)."""

    FP = "a" * 64

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"printer_ip": "1.2.3.4", "serial": "MOCK"}, f)

    def _read_config(self):
        with open(self.config_path, encoding="utf-8") as f:
            return json.load(f)

    def test_json_mode_never_prompts_or_writes(self):
        from bambu_cli.commands import _offer_pin_fingerprint
        with patch("builtins.input") as mock_input:
            result = _offer_pin_fingerprint(self.FP, self.config_path, json_mode=True, interactive=True)
        self.assertFalse(result)
        mock_input.assert_not_called()
        self.assertNotIn("cert_fingerprint", self._read_config())

    def test_non_interactive_never_prompts_or_writes(self):
        from bambu_cli.commands import _offer_pin_fingerprint
        with patch("builtins.input") as mock_input:
            result = _offer_pin_fingerprint(self.FP, self.config_path, json_mode=False, interactive=False)
        self.assertFalse(result)
        mock_input.assert_not_called()
        self.assertNotIn("cert_fingerprint", self._read_config())

    def test_decline_leaves_config_untouched(self):
        from bambu_cli.commands import _offer_pin_fingerprint
        with patch("builtins.input", return_value="n"):
            result = _offer_pin_fingerprint(self.FP, self.config_path, json_mode=False, interactive=True)
        self.assertFalse(result)
        self.assertNotIn("cert_fingerprint", self._read_config())

    def test_accept_pins_fingerprint_and_preserves_config(self):
        from bambu_cli.commands import _offer_pin_fingerprint
        with patch("builtins.input", return_value="y"):
            result = _offer_pin_fingerprint(self.FP, self.config_path, json_mode=False, interactive=True)
        self.assertTrue(result)
        cfg = self._read_config()
        self.assertEqual(cfg["cert_fingerprint"], self.FP)
        # Existing keys survive the read-modify-write.
        self.assertEqual(cfg["printer_ip"], "1.2.3.4")
        if os.name != "nt":
            import stat
            mode = stat.S_IMODE(os.stat(self.config_path).st_mode)
            self.assertEqual(mode, 0o600)


class TestBambuDryRun(unittest.TestCase):
    @patch('bambu_cli.printer.get_printer')
    @patch('bambu_cli.protocols.mqtt.get_status')
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

        # Enable sim mode via the runtime context for the test
        with settings_ctx(simulation=True):
            cmd_status(args)
            self.assertTrue(any("Fetching simulated printer status" in call[0][0] for call in mock_logger.info.call_args_list))
            self.assertTrue(any("State: IDLE" in call[0][0] for call in mock_logger.info.call_args_list))

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

        with settings_ctx(simulation=True):
            try:
                # Use a real file (not mock_open) so _SimFtp's fp.tell()/seek()-based
                # size bookkeeping — and upload_file's post-transfer size
                # verification against it — reflect actual byte counts.
                local_path = os.path.join(os.getcwd(), "test.3mf")
                with open(local_path, "wb") as f:
                    f.write(b"x" * 1024)
                try:
                    cmd_upload(args)
                finally:
                    os.unlink(local_path)

                self.assertTrue(any("Connecting to simulated FTPS server" in call[0][0] for call in mock_ftps_logger.info.call_args_list))
                self.assertTrue(any("Uploaded test.3mf to printer" in call[0][0] for call in mock_commands_logger.info.call_args_list))
            finally:
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


if __name__ == '__main__':
    unittest.main()
