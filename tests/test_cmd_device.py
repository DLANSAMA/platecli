"""Device state commands: light, pause, resume, stop -- including the --confirm gate."""

from tests.bambu_test_base import *  # noqa: F401,F403

class TestBambuCmdLight(unittest.TestCase):
    @patch("bambu_cli.commands.device.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_light_on(self, mock_logger, mock_send_command, mock_seq):
        args = MagicMock()
        args.action = "on"

        cmd_light(args)

        # Expected payload
        expected_payload = json.dumps(
            {
                "system": {
                    "sequence_id": "0",
                    "command": "ledctrl",
                    "led_node": "chamber_light",
                    "led_mode": "on",
                    "led_on_time": 500,
                    "led_off_time": 500,
                }
            }
        )

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("💡 Light turned on")

    @patch("bambu_cli.commands.device.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_light_off(self, mock_logger, mock_send_command, mock_seq):
        args = MagicMock()
        args.action = "off"

        cmd_light(args)

        # Expected payload
        expected_payload = json.dumps(
            {
                "system": {
                    "sequence_id": "0",
                    "command": "ledctrl",
                    "led_node": "chamber_light",
                    "led_mode": "off",
                    "led_on_time": 500,
                    "led_off_time": 500,
                }
            }
        )

        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("💡 Light turned off")

class TestBambuCmdResume(unittest.TestCase):
    @patch("bambu_cli.commands.device.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_resume(self, mock_logger, mock_send_command, mock_seq):
        from bambu_cli.commands import cmd_resume

        args = MagicMock()

        cmd_resume(args)

        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "resume"}})
        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("▶️  Print resumed")

class TestBambuCmdPause(unittest.TestCase):
    @patch("bambu_cli.commands.device.get_sequence_id", return_value="0")
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_pause(self, mock_logger, mock_send_command, mock_seq):
        from bambu_cli.commands import cmd_pause

        args = MagicMock()

        cmd_pause(args)

        expected_payload = json.dumps({"print": {"sequence_id": "0", "command": "pause"}})
        mock_send_command.assert_called_once_with(ANY, expected_payload, timeout=None, retries=2)
        mock_logger.info.assert_called_once_with("⏸️  Print paused")

class TestBambuCmdStop(unittest.TestCase):
    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
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

    @patch("bambu_cli.protocols.mqtt.send_command")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_stop_with_confirm(self, mock_logger, mock_send_command):
        # Create a mock args object with confirm=True
        args = MagicMock()
        args.confirm = True

        cmd_stop(args)

        # Assert that send_command WAS called
        mock_send_command.assert_called_once()
