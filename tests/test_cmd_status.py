"""Status command: one-shot query and the --monitor NDJSON stream."""

from tests.bambu_test_base import *  # noqa: F401,F403

def _full_status_snapshot(**overrides):
    """A pushall reply: carries every key `status` treats as always-present."""
    snapshot = {
        "gcode_state": "RUNNING",
        "mc_percent": 37,
        "layer_num": 74,
        "total_layer_num": 200,
        "bed_temper": 60.0,
        "bed_target_temper": 60.0,
        "nozzle_temper": 219.9375,
        "nozzle_target_temper": 220.0,
    }
    snapshot.update(overrides)
    return snapshot


def _mqtt_message(print_payload):
    msg = MagicMock()
    msg.payload = json.dumps({"print": print_payload}).encode()
    return msg


class TestBambuGetStatus(unittest.TestCase):
    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_status_on_connect_rc_error(self, mock_logger, mock_create):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 5)

        mock_client.connect.side_effect = side_effect_connect

        result = get_status(_test_printer(), timeout=0.1)

        self.assertIsNone(result)
        mock_logger.error.assert_called_with("Connection failed: rc=5")

    @patch("bambu_cli.protocols.mqtt.get_status")
    def test_cmd_status_connect_fail(self, mock_get_status):
        from bambu_cli.commands import cmd_status
        from bambu_cli.errors import PrinterConnectionError

        mock_get_status.return_value = None

        with self.assertRaises(PrinterConnectionError) as cm:
            cmd_status(MagicMock())

        self.assertEqual(str(cm.exception), "Could not connect to printer.")
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertEqual(cm.exception.failed_step, "mqtt")

    @patch("bambu_cli.commands.status.emit_json")
    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_status_json_output(self, mock_logger, mock_get_status, mock_emit_json):
        from bambu_cli.commands import cmd_status

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

    @patch("bambu_cli.commands.status.emit_json")
    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_status_json_never_emits_partial_printer(self, mock_logger, mock_get_status, mock_emit_json):
        """`--json status` must error rather than hand agents a printer map with no gcode_state."""
        from bambu_cli.commands import cmd_status
        from bambu_cli.errors import PrinterStatusIncomplete

        mock_get_status.side_effect = PrinterStatusIncomplete(
            "Printer returned only partial status updates, never a full snapshot (missing gcode_state).",
            detail={"missing_keys": ["gcode_state"], "received_keys": ["nozzle_temper"]},
        )

        args = MagicMock()
        args.json = True
        args.monitor = False

        with self.assertRaises(PrinterStatusIncomplete):
            cmd_status(args)

        mock_emit_json.assert_not_called()

    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_status_running_formatting(self, mock_logger, mock_get_status):
        from bambu_cli.commands import cmd_status

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
            "wifi_signal": "-50dBm",
        }

        args = MagicMock()
        args.json = False

        cmd_status(args)

        mock_logger.info.assert_any_call("   File: test.gcode")
        mock_logger.info.assert_any_call("   Progress: 50% | Layer 10/20")
        mock_logger.info.assert_any_call("   Time left: 2h 5m")

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("time.sleep")
    def test_get_status_success(self, mock_sleep, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        snapshot = _full_status_snapshot(gcode_state="IDLE", mc_percent=0)

        def mock_connect(*args, **kwargs):
            # Call on_connect directly
            mock_client.on_connect(mock_client, None, None, 0)

            # Simulate the pushall reply arriving with 'print' data
            mock_client.on_message(mock_client, None, _mqtt_message(snapshot))

        mock_client.connect.side_effect = mock_connect

        result = get_status(_test_printer(), timeout=1)

        self.assertEqual(result, snapshot)
        mock_create_mqtt.assert_called_once()
        mock_client.connect.assert_called_once()
        mock_client.subscribe.assert_called_once()
        mock_client.publish.assert_called_once()
        mock_client.disconnect.assert_called()

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("time.sleep")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_status_timeout(self, mock_logger, mock_sleep, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)

        mock_client.connect.side_effect = mock_connect

        # No status message ever arrives -> 3 attempts (2 retries)
        result = get_status(_test_printer(), timeout=0.0001)

        self.assertIsNone(result)
        self.assertEqual(mock_client.connect.call_count, 3)

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    def test_get_status_connection_failure(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        # Mock connect to raise an exception
        mock_client.connect.side_effect = OSError("Connection error")

        result = get_status(_test_printer(), timeout=0.0001)

        self.assertIsNone(result)
        self.assertTrue(
            any("MQTT status error: Connection error" in call[0][0] for call in mock_logger.error.call_args_list)
        )

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    def test_get_status_ignore_non_print_messages(self, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        snapshot = _full_status_snapshot(gcode_state="RUNNING")

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
            mock_client.on_message(mock_client, None, _mqtt_message(snapshot))

        mock_client.connect.side_effect = mock_connect

        with patch("time.sleep"):
            result = get_status(_test_printer(), timeout=1)

        self.assertEqual(result, snapshot)

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("time.sleep")
    def test_get_status_waits_through_delta_for_full_snapshot(self, mock_sleep, mock_create_mqtt):
        """A delta arriving before the pushall reply must not be returned as the state.

        Reproduces the live-printer intermittent: mid-print the report topic
        delivers a lone nozzle_temper reading first, and returning it hands
        agents a `printer` object with no gcode_state.
        """
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        snapshot = _full_status_snapshot()

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            # Incremental delta first — exactly what was observed at ~37%.
            mock_client.on_message(mock_client, None, _mqtt_message({"nozzle_temper": 219.9375}))
            # Then the pushall reply.
            mock_client.on_message(mock_client, None, _mqtt_message(snapshot))

        mock_client.connect.side_effect = mock_connect

        result = get_status(_test_printer(), timeout=1)

        self.assertIn("gcode_state", result)
        self.assertEqual(result["gcode_state"], "RUNNING")
        self.assertEqual(result["mc_percent"], 37)
        self.assertEqual(result["total_layer_num"], 200)

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("time.sleep")
    def test_get_status_merges_delta_over_earlier_snapshot(self, mock_sleep, mock_create_mqtt):
        """Later values win when a delta follows the snapshot in the same window."""
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            # Snapshot missing one required key, so the wait continues...
            partial_snapshot = _full_status_snapshot()
            del partial_snapshot["bed_temper"]
            mock_client.on_message(mock_client, None, _mqtt_message(partial_snapshot))
            # ...and the next delta both completes and freshens the state.
            mock_client.on_message(mock_client, None, _mqtt_message({"bed_temper": 61.0, "mc_percent": 38}))

        mock_client.connect.side_effect = mock_connect

        result = get_status(_test_printer(), timeout=1)

        self.assertEqual(result["bed_temper"], 61.0)
        self.assertEqual(result["mc_percent"], 38)
        self.assertEqual(result["gcode_state"], "RUNNING")

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    def test_get_status_deltas_only_raises_instead_of_returning_partial(
        self, mock_sleep, mock_logger, mock_create_mqtt
    ):
        """If no full snapshot ever arrives, error clearly rather than emit a partial."""
        from bambu_cli.errors import PrinterStatusIncomplete
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            mock_client.on_message(mock_client, None, _mqtt_message({"nozzle_temper": 219.9375}))

        mock_client.connect.side_effect = mock_connect

        with self.assertRaises(PrinterStatusIncomplete) as cm:
            get_status(_test_printer(), timeout=0.05, retries=1)

        self.assertEqual(cm.exception.exit_code, 6)
        self.assertEqual(cm.exception.failed_step, "status")
        self.assertIn("gcode_state", cm.exception.detail["missing_keys"])
        self.assertEqual(cm.exception.detail["received_keys"], ["nozzle_temper"])
        # Every attempt re-issues pushall rather than settling for the delta.
        self.assertEqual(mock_client.connect.call_count, 2)

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("time.sleep")
    def test_get_status_liveness_probe_accepts_partial(self, mock_sleep, mock_create_mqtt):
        """doctor / --dry-run only prove MQTT works, so a delta is good enough."""
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client

        def mock_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)
            mock_client.on_message(mock_client, None, _mqtt_message({"nozzle_temper": 219.9375}))

        mock_client.connect.side_effect = mock_connect

        result = get_status(_test_printer(), timeout=1, require_complete=False)

        self.assertEqual(result, {"nozzle_temper": 219.9375})
        mock_client.connect.assert_called_once()

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    def test_get_status_exception(self, mock_sleep, mock_logger, mock_create_mqtt):
        from bambu_cli.protocols.mqtt import get_status

        mock_client = MagicMock()
        mock_create_mqtt.return_value = mock_client
        mock_client.connect.side_effect = OSError("Network error")

        result = get_status(_test_printer(), timeout=1)

        self.assertIsNone(result)
        self.assertTrue(
            any("MQTT status error: Network error" in call[0][0] for call in mock_logger.error.call_args_list)
        )

class TestMonitorStatusStreaming(unittest.TestCase):
    """`status --monitor --json` streams one NDJSON event per change (agent contract)."""

    def test_status_event_shape_and_coercion(self):
        from bambu_cli.protocols.mqtt import _status_event

        p = {
            "gcode_state": "RUNNING",
            "mc_percent": "42",  # firmware sometimes sends numbers as strings
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
        self.assertEqual(ev["mc_percent"], 42)  # coerced to int
        self.assertEqual(ev["mc_remaining_time"], 33)  # coerced to int
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

        from bambu_cli.printer import get_printer
        from bambu_cli.protocols import mqtt

        args = types.SimpleNamespace(json=True, monitor=True, sim=True)
        with settings_ctx(simulation=True), patch.object(mqtt.time, "sleep"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mqtt.monitor_status(args, get_printer())

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
