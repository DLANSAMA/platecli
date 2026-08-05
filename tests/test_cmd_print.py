"""Print command: the physical-action path and its --confirm gate."""

from tests.bambu_test_base import *  # noqa: F401,F403

class TestBambuCmdPrint(unittest.TestCase):
    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_execute_print_command_dry_run_file_not_found(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.protocols.mqtt import execute_print_command

        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["other.3mf"]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp

        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "missing.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        mock_logger.error.assert_any_call("   ❌ File missing.3mf NOT found on printer. Upload it first.")

    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_execute_print_command_dry_run_mqtt_fail(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.protocols.mqtt import execute_print_command

        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["test.3mf"]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp

        mock_get_status.return_value = None

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_any_call("   ❌ MQTT connection failed.")

    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_execute_print_command_dry_run_exception(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.protocols.mqtt import execute_print_command

        printer = _test_printer()
        printer.get_ftp_client = MagicMock(side_effect=OSError("FTP Error"))

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_any_call("Dry run failed: FTP Error")

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.protocols.mqtt.time.sleep")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_execute_print_command_non_sd_error(self, mock_exit, mock_logger, mock_sleep, mock_create):
        from bambu_cli.protocols.mqtt import execute_print_command

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def fake_connect(ip, port, keepalive):
            # simulate receiving message with error 1234
            msg = MagicMock()
            msg.payload = b'{"print": {"print_error": 1234}}'
            mock_client.on_message(mock_client, None, msg)

        mock_client.connect.side_effect = fake_connect

        mock_exit.side_effect = SystemExit(4)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(_test_printer(), "payload", "test.3mf", dry_run=False)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 4)
        mock_logger.error.assert_called_with("Print failed with error code 1234 (hex 0x000004D2)")

    def test_generate_print_payload(self):
        from bambu_cli.job import generate_print_payload
        import json

        basename = "test_model.gcode"
        payload = generate_print_payload(basename)

        parsed = json.loads(payload)
        self.assertIn("print", parsed)
        self.assertEqual(parsed["print"]["subtask_name"], "test_model.gcode")
        self.assertEqual(parsed["print"]["url"], "file:///sdcard/model/test_model.gcode")

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    def test_execute_print_command_success(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import execute_print_command
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

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    @patch("sys.exit")
    def test_execute_print_command_with_error(self, mock_exit, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import execute_print_command
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

        with self.assertRaises((SystemExit, BambuError)):
            execute_print_command(_test_printer(), payload, basename)

        self.assertTrue(
            any("Print failed with error code 83935248" in call[0][0] for call in mock_logger.error.call_args_list)
        )
        self.assertTrue(
            any("File not found on printer SD card" in call[0][0] for call in mock_logger.info.call_args_list)
        )

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    @patch("time.sleep")
    def test_execute_print_command_exception(self, mock_sleep, mock_exit, mock_logger, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import execute_print_command
        import json

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        mock_client.connect.side_effect = OSError("Connection refused")
        mock_exit.side_effect = SystemExit(2)

        payload = '{"test": "payload"}'
        basename = "test_model.gcode"

        with self.assertRaises((SystemExit, BambuError)):
            execute_print_command(_test_printer(), payload, basename)

        self.assertTrue(any("Error: Connection refused" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch("bambu_cli.job.generate_print_payload")
    @patch("bambu_cli.protocols.mqtt.execute_print_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_print_no_confirm(self, mock_logger, mock_execute, mock_generate):
        from bambu_cli.commands import cmd_print

        args = MagicMock()
        args.confirm = False
        args.file = "test.gcode"
        args.dry_run = False
        args.ams_mapping = None
        args.use_ams = False

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_print(args)
        self.assertEqual(cm.exception.exit_code, 5)

        mock_generate.assert_not_called()
        mock_execute.assert_not_called()

        self.assertTrue(
            any(
                "⚠️  This will START a print. Add --confirm to proceed." in call[0][0]
                for call in mock_logger.warning.call_args_list
            )
        )

    @patch("bambu_cli.commands.print_cmd.generate_print_payload")
    @patch("bambu_cli.protocols.mqtt.execute_print_command")
    def test_cmd_print_with_confirm(self, mock_execute, mock_generate):
        from bambu_cli.commands import cmd_print

        args = MagicMock()
        args.confirm = True
        args.file = "test.gcode"
        args.dry_run = False
        args.ams_mapping = None
        args.use_ams = False
        args.timelapse = False
        args.skip_bed_leveling = True
        args.skip_flow_cali = True

        mock_generate.return_value = "test_payload"

        cmd_print(args)

        mock_generate.assert_called_once_with(
            "test.gcode", use_ams=False, ams_mapping=None, timelapse=False, bed_leveling=False, flow_cali=False
        )
        mock_execute.assert_called_once_with(ANY, "test_payload", "test.gcode", dry_run=False)
