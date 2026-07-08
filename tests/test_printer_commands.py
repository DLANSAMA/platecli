from tests.bambu_test_base import *  # noqa: F401,F403


class TestBambuCmdUploadEdgeCases(unittest.TestCase):

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_upload_invalid_filepath(self, mock_exit, mock_logger):
        from bambu_cli.bambu import cmd_upload
        args = MagicMock()
        args.file = "-invalid.gcode"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
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
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
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

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
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
        mock_ftp2.size.return_value = 2048

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

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_called_with("❌ Upload failed after 4 attempts.")


class TestBambuCmdLight(unittest.TestCase):

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.protocols.mqtt.send_command')
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
    @patch('bambu_cli.protocols.mqtt.send_command')
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
    @patch('bambu_cli.protocols.mqtt.send_command')
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
    @patch('bambu_cli.protocols.mqtt.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_pause(self, mock_logger, mock_send_command, mock_seq):
        from bambu_cli.bambu import cmd_pause
        args = MagicMock()

        cmd_pause(args)

        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "pause"}})
        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("⏸️  Print paused")


class TestBambuCmdStop(unittest.TestCase):

    @patch('bambu_cli.protocols.mqtt.send_command')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_stop_without_confirm(self, mock_logger, mock_send_command):
        # Create a mock args object with confirm=False
        args = MagicMock()
        args.confirm = False

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_stop(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        # Assert that send_command was NOT called
        mock_send_command.assert_not_called()

        # Assert that the correct message was logged
        mock_logger.warning.assert_called_once_with("⚠️  This will STOP the current print. Add --confirm to proceed.")

    @patch('bambu_cli.protocols.mqtt.send_command')
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

        with self.assertRaises((SystemExit, BambuError)):
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

        with self.assertRaises((SystemExit, BambuError)):
            cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_logger.error.assert_called_with("Error listing files: Failed to list files via printer API")


class TestBambuCmdDelete(unittest.TestCase):
    @patch('bambu_cli.protocols.ftps.get_ftp')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_delete_no_confirm(self, mock_exit, mock_logger, mock_get_ftp):
        from bambu_cli.bambu import cmd_delete
        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = False

        mock_exit.side_effect = SystemExit(5)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_delete(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)
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

        with self.assertRaises((SystemExit, BambuError)):
            cmd_delete(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.delete.assert_called_once_with('/model/test.3mf')
        mock_logger.error.assert_called_with("Delete failed: Delete operation failed in printer client.")


class TestBambuCmdGcode(unittest.TestCase):

    @patch('bambu_cli.protocols.mqtt.send_command')
    @patch('sys.exit')
    def test_cmd_gcode_send_command_fail(self, mock_exit, mock_send):
        from bambu_cli.bambu import cmd_gcode
        mock_send.return_value = False
        args = MagicMock()
        args.code = "G28"

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_gcode(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)

    @patch('bambu_cli.commands.get_sequence_id', return_value="0")
    @patch('bambu_cli.protocols.mqtt.send_command')
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

    @patch('bambu_cli.protocols.mqtt.get_status')
    def test_cmd_status_connect_fail(self, mock_get_status):
        from bambu_cli.bambu import cmd_status
        from bambu_cli.errors import PrinterConnectionError
        mock_get_status.return_value = None

        with self.assertRaises(PrinterConnectionError) as cm:
            cmd_status(MagicMock())

        self.assertEqual(str(cm.exception), "Could not connect to printer.")
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertEqual(cm.exception.failed_step, "mqtt")

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

    @patch('bambu_cli.protocols.mqtt.get_status')
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

    @patch('bambu_cli.protocols.mqtt.get_status')
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
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "missing.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        mock_logger.error.assert_any_call("   ❌ File missing.3mf NOT found on printer. Upload it first.")

    @patch('bambu_cli.protocols.mqtt.get_status')
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
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_any_call("   ❌ MQTT connection failed.")

    @patch('bambu_cli.protocols.mqtt.get_status')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_execute_print_command_dry_run_exception(self, mock_exit, mock_logger, mock_get_status):
        from bambu_cli.bambu import execute_print_command
        printer = _test_printer()
        printer.get_ftp_client = MagicMock(side_effect=OSError("FTP Error"))

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(printer, "payload", "test.3mf", dry_run=True)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
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

        with self.assertRaises((SystemExit, BambuError)) as cm:
            execute_print_command(_test_printer(), "payload", "test.3mf", dry_run=False)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 4)
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

        with self.assertRaises((SystemExit, BambuError)):
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

        with self.assertRaises((SystemExit, BambuError)):
            execute_print_command(_test_printer(), payload, basename)

        self.assertTrue(any("Error: Connection refused" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('bambu_cli.bambu.generate_print_payload')
    @patch('bambu_cli.protocols.mqtt.execute_print_command')
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
    @patch('bambu_cli.protocols.mqtt.execute_print_command')
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
        # First size() call is the mid-failure resume probe (mismatch keeps
        # uploaded_bytes at 0); second is the post-success verification.
        mock_ftp.size.side_effect = [0, 2048]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)

        self.assertEqual(mock_ftp.storbinary.call_count, 2)
        self.assertTrue(any("⚠️ Upload attempt 1 failed" in call[0][0] for call in mock_printer_logger.warning.call_args_list))
        self.assertTrue(any("✅ Uploaded test.3mf to printer" in call[0][0] for call in mock_logger.info.call_args_list))


class TestMonitorStatusStreaming(unittest.TestCase):
    """`status --monitor --json` streams one NDJSON event per change (agent contract)."""

    def test_status_event_shape_and_coercion(self):
        from bambu_cli.protocols.mqtt import _status_event

        p = {
            "gcode_state": "RUNNING",
            "mc_percent": "42",           # firmware sometimes sends numbers as strings
            "layer_num": 10,
            "total_layer_num": 200,
            "mc_remaining_time": "33",
            "nozzle_temper": 220,
            "bed_temper": 60,
            "gcode_file": "model.gcode",
        }
        ev = _status_event(p, "update")
        self.assertEqual(ev["event"], "update")
        self.assertEqual(ev["command"], "status")
        self.assertEqual(ev["gcode_state"], "RUNNING")
        self.assertEqual(ev["mc_percent"], 42)          # coerced to int
        self.assertEqual(ev["mc_remaining_time"], 33)   # coerced to int
        self.assertEqual(ev["layer_num"], 10)
        self.assertEqual(ev["total_layer_num"], 200)
        self.assertEqual(ev["gcode_file"], "model.gcode")
        # Missing/garbage numeric fields degrade to 0 rather than raising.
        self.assertEqual(_status_event({}, "update")["mc_percent"], 0)
        self.assertEqual(_status_event({"mc_percent": "?"}, "update")["mc_percent"], 0)

    def test_sim_monitor_streams_ndjson_events(self):
        import contextlib
        import io
        import json
        import types

        from bambu_cli.protocols import mqtt

        args = types.SimpleNamespace(json=True, monitor=True, sim=True)
        with settings_ctx(simulation=True), patch.object(mqtt.time, "sleep"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mqtt.monitor_status(args)

        events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        self.assertEqual(
            [(e["event"], e["gcode_state"], e["mc_percent"]) for e in events],
            [("update", "PREPARE", 0), ("update", "RUNNING", 50), ("terminal", "FINISH", 100)],
        )
        # Every streamed line is a self-contained one-line JSON object (NDJSON).
        for line in buf.getvalue().splitlines():
            if line.strip():
                self.assertNotIn("\n", line)
                obj = json.loads(line)
                self.assertEqual(obj["command"], "status")


class TestBambuDownloadFile(unittest.TestCase):
    """download_file streams to a temp sibling then atomically replaces, so a
    failed transfer never corrupts an existing file at local_path."""

    def _printer_with_ftp(self, mock_ftp):
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        return printer

    def test_download_file_success_writes_content_no_temp_left(self):
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        mock_ftp = MagicMock()
        mock_ftp.retrbinary.side_effect = lambda cmd, cb, blocksize=None: cb(b"new content")

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertTrue(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"new content")
        self.assertEqual([p for p in os.listdir(d) if p.endswith(".part")], [])

    def test_download_file_failure_preserves_existing_and_cleans_temp(self):
        import ftplib
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        with open(local, "wb") as f:
            f.write(b"original good file")

        mock_ftp = MagicMock()
        mock_ftp.retrbinary.side_effect = ftplib.error_temp("connection dropped")

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertFalse(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"original good file")  # untouched
        self.assertEqual([p for p in os.listdir(d) if p.endswith(".part")], [])


if __name__ == '__main__':
    unittest.main()
