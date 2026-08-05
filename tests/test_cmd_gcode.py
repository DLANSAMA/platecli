"""Raw G-code command, including the command-injection rejection path."""

from tests.bambu_test_base import *  # noqa: F401,F403

class TestBambuCmdGcode(unittest.TestCase):
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("sys.exit")
    def test_cmd_gcode_send_command_fail(self, mock_exit, mock_send):
        from bambu_cli.commands import cmd_gcode

        mock_send.return_value = False
        args = MagicMock()
        args.code = "G28"
        args.confirm = True
        args.json = False

        mock_exit.side_effect = SystemExit(2)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_gcode(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)

    @patch("bambu_cli.commands.gcode.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    def test_cmd_gcode(self, mock_send_command, mock_seq):
        from bambu_cli.commands import cmd_gcode

        args = MagicMock()
        args.code = "M104 S220"
        args.confirm = True
        args.json = False

        cmd_gcode(args)

        # Expected payload
        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "gcode_line", "param": "M104 S220"}})

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)

    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_gcode_no_confirm_aborts_without_send(self, mock_logger, mock_send):
        """Raw G-code is a physical action: require --confirm before MQTT send."""
        from bambu_cli.commands import cmd_gcode
        from bambu_cli.constants import EXIT_COMMAND_ERROR

        args = MagicMock()
        args.code = "G28"
        args.confirm = False
        args.json = False

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_gcode(args)

        self.assertEqual(
            getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)),
            EXIT_COMMAND_ERROR,
        )
        mock_send.assert_not_called()
        self.assertTrue(any("Add --confirm to proceed" in str(call) for call in mock_logger.warning.call_args_list))

    @patch("bambu_cli.commands.gcode.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    def test_cmd_gcode_with_confirm_sends(self, mock_send, mock_seq):
        from bambu_cli.commands import cmd_gcode

        mock_send.return_value = True
        args = MagicMock()
        args.code = "G28"
        args.confirm = True
        args.json = False

        cmd_gcode(args)

        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        self.assertIn("G28", payload)

    @patch("bambu_cli.protocols.mqtt.send_command")
    def test_cmd_gcode_rejects_empty_code(self, mock_send):
        from bambu_cli.commands import cmd_gcode
        from bambu_cli.constants import EXIT_COMMAND_ERROR

        for bad in ("", "   ", "\t"):
            args = MagicMock()
            args.code = bad
            args.confirm = True
            args.json = False
            with self.assertRaises((SystemExit, BambuError)) as cm:
                cmd_gcode(args)
            self.assertEqual(
                getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)),
                EXIT_COMMAND_ERROR,
            )
        mock_send.assert_not_called()

    @patch("bambu_cli.protocols.mqtt.send_command")
    def test_cmd_gcode_rejects_control_chars(self, mock_send):
        """CR/LF/NUL in G-code can smuggle extra MQTT/serial commands."""
        from bambu_cli.commands import cmd_gcode
        from bambu_cli.constants import EXIT_COMMAND_ERROR

        for bad in ("G28\nM104 S999", "G28\rM104", "G28\x00M104"):
            args = MagicMock()
            args.code = bad
            args.confirm = True
            args.json = False
            with self.assertRaises((SystemExit, BambuError)) as cm:
                cmd_gcode(args)
            self.assertEqual(
                getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)),
                EXIT_COMMAND_ERROR,
            )
        mock_send.assert_not_called()
