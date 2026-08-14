from tests.bambu_test_base import *  # noqa: F401,F403


class TestImplicitFTPS(unittest.TestCase):
    def test_implicit_ftps_insecure(self):
        from bambu_cli.protocols.ftps import ImplicitFTPS

        mock_sock = MagicMock()
        mock_sock.family = 2
        create_connection = MagicMock(return_value=mock_sock)

        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_sock
        ssl_context_cls = MagicMock(return_value=mock_ctx)

        ftp = ImplicitFTPS()
        # TLS behavior is now driven by the printer object attached to the FTP client
        ftp.printer = _test_printer(insecure_tls=True)
        ftp.getresp = MagicMock(return_value="220 Welcome")

        welcome = ftp.connect(
            "192.168.1.1",
            990,
            60,
            create_connection=create_connection,
            ssl_context_cls=ssl_context_cls,
        )

        self.assertEqual(welcome, "220 Welcome")
        self.assertEqual(mock_ctx.check_hostname, False)
        import ssl

        self.assertEqual(mock_ctx.verify_mode, ssl.CERT_NONE)
        mock_ctx.wrap_socket.assert_called_with(mock_sock, server_hostname="192.168.1.1")

    def test_implicit_ftps_secure(self):
        from bambu_cli.protocols.ftps import ImplicitFTPS

        mock_sock = MagicMock()
        mock_sock.family = 2
        create_connection = MagicMock(return_value=mock_sock)

        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_sock
        ssl_context_cls = MagicMock(return_value=mock_ctx)

        ftp = ImplicitFTPS()
        ftp.getresp = MagicMock(return_value="220 Welcome")

        welcome = ftp.connect(
            "192.168.1.1",
            990,
            60,
            create_connection=create_connection,
            ssl_context_cls=ssl_context_cls,
        )

        self.assertEqual(welcome, "220 Welcome")
        self.assertEqual(mock_ctx.check_hostname, True)
        import ssl

        self.assertEqual(mock_ctx.verify_mode, ssl.CERT_REQUIRED)
        mock_ctx.load_default_certs.assert_called_once()


class TestSendCommand(unittest.TestCase):
    def test_send_command_success(self):
        from bambu_cli.protocols.mqtt import send_command

        mock_client = MagicMock()

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 0)
            mock_client.on_publish(mock_client, None, 1)

        mock_client.connect.side_effect = side_effect_connect

        printer = _test_printer(ip="192.168.1.1")
        result = send_command(
            printer,
            '{"test": "payload"}',
            client_factory=lambda p, *a, **k: mock_client,
        )

        self.assertTrue(result)
        mock_client.connect.assert_called_with("192.168.1.1", 8883, keepalive=10)
        topic = f"device/{printer.serial}/request"
        # Published at QoS 1 so success reflects a broker PUBACK, not a bare
        # local socket write; and exactly once (no reconnect re-publish).
        mock_client.publish.assert_called_once_with(topic, '{"test": "payload"}', qos=1)
        mock_client.loop_start.assert_called_once()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    def test_send_command_retry_timeout(self):
        from bambu_cli.protocols.mqtt import send_command

        mock_client = MagicMock()
        mock_client.connect.side_effect = OSError("Connection error")
        mock_sleep = MagicMock()

        result = send_command(
            _test_printer(),
            '{"test": "payload"}',
            client_factory=lambda p, *a, **k: mock_client,
            sleep=mock_sleep,
        )

        self.assertFalse(result)
        self.assertEqual(mock_client.connect.call_count, 3)

    def test_send_command_on_connect_rc_error(self):
        from bambu_cli.protocols.mqtt import send_command

        mock_client = MagicMock()
        mock_logger = MagicMock()

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 5)

        mock_client.connect.side_effect = side_effect_connect

        with patch("bambu_cli.protocols.mqtt_cmd.logger", mock_logger):
            result = send_command(
                _test_printer(ip="192.168.1.1"),
                '{"test": "payload"}',
                timeout=0.1,
                client_factory=lambda p, *a, **k: mock_client,
            )

        self.assertFalse(result)
        mock_logger.error.assert_called_with("Connection failed: rc=5")

    def test_send_command_tears_down_if_loop_start_raises(self):
        from bambu_cli.protocols.mqtt import send_command

        mock_client = MagicMock()
        mock_client.connect.side_effect = lambda *a, **k: None
        mock_client.loop_start.side_effect = OSError("loop failed")

        result = send_command(
            _test_printer(),
            '{"test": "payload"}',
            retries=0,
            client_factory=lambda p, *a, **k: mock_client,
        )
        self.assertFalse(result)
        mock_client.loop_stop.assert_called()
        mock_client.disconnect.assert_called()


import socket


class TestGetFtp(unittest.TestCase):
    @patch("bambu_cli.protocols.ftps.ImplicitFTPS")
    def test_create_raw_ftp_success(self, mock_implicit_ftps):
        from bambu_cli.protocols.ftps import _create_raw_ftp

        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        printer = _test_printer(ip="192.168.1.100", access_code="mock_access_code")

        result = _create_raw_ftp(printer)

        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with("192.168.1.100", 990, timeout=60)
        mock_ftp_instance.login.assert_called_once_with("bblp", "mock_access_code")
        mock_ftp_instance.prot_p.assert_called_once()
        self.assertIs(result, mock_ftp_instance)

    @patch("bambu_cli.protocols.ftps.ImplicitFTPS")
    def test_create_raw_ftp_connect_failure(self, mock_implicit_ftps):
        from bambu_cli.protocols.ftps import _create_raw_ftp

        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        mock_ftp_instance.connect.side_effect = OSError("Connection Refused")
        printer = _test_printer(ip="192.168.1.100", access_code="mock_access_code")

        with self.assertRaises(Exception) as context:
            _create_raw_ftp(printer)

        self.assertEqual(str(context.exception), "Connection Refused")
        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with("192.168.1.100", 990, timeout=60)
        mock_ftp_instance.login.assert_not_called()
        mock_ftp_instance.prot_p.assert_not_called()
        mock_ftp_instance.close.assert_called_once()

    @patch("bambu_cli.protocols.ftps.ImplicitFTPS")
    def test_create_raw_ftp_login_failure_closes_socket(self, mock_implicit_ftps):
        from bambu_cli.protocols.ftps import _create_raw_ftp

        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        mock_ftp_instance.login.side_effect = OSError("530 Login incorrect")
        printer = _test_printer(ip="192.168.1.100", access_code="bad")

        with self.assertRaises(OSError) as context:
            _create_raw_ftp(printer)

        self.assertEqual(str(context.exception), "530 Login incorrect")
        mock_ftp_instance.connect.assert_called_once()
        mock_ftp_instance.close.assert_called_once()
        mock_ftp_instance.prot_p.assert_not_called()


class TestCreateMqttClient(unittest.TestCase):
    def test_create_mqtt_client_simulation(self):
        from bambu_cli.protocols.mqtt import create_mqtt_client

        client = create_mqtt_client(_test_printer(simulation_mode=True))
        from bambu_cli.protocols.mqtt import _SimMqttClient

        self.assertIsInstance(client, _SimMqttClient)

    @patch("bambu_cli.protocols.mqtt_tls.mqtt.Client")
    def test_create_mqtt_client_secure(self, mock_mqtt_client):
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        printer = _test_printer(access_code="mock_access_code")
        client = create_mqtt_client(printer, "test_client")

        # Use ANY for the version argument to avoid identity mismatches with module-level mocks
        mock_mqtt_client.assert_called_once_with(ANY, "test_client")
        mock_client_instance.username_pw_set.assert_called_once_with("bblp", "mock_access_code")
        mock_client_instance.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_REQUIRED)
        mock_client_instance.tls_insecure_set.assert_not_called()
        self.assertEqual(client, mock_client_instance)

    @patch("bambu_cli.protocols.mqtt_tls.mqtt.Client")
    def test_create_mqtt_client_insecure(self, mock_mqtt_client):
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        printer = _test_printer(access_code="mock_access_code", insecure_tls=True)
        client = create_mqtt_client(printer)

        mock_mqtt_client.assert_called_once_with(ANY, "")
        mock_client_instance.username_pw_set.assert_called_once_with("bblp", "mock_access_code")
        mock_client_instance.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_NONE)
        mock_client_instance.tls_insecure_set.assert_called_once_with(True)
        self.assertEqual(client, mock_client_instance)


class TestMqttConnectTimeout(unittest.TestCase):
    def test_mqtt_connect_sets_client_timeout_without_mutating_socket_default(self):
        import socket as socket_mod

        from bambu_cli.protocols import mqtt_tls

        client = MagicMock()
        client._connect_timeout = 5.0
        printer = MagicMock()
        printer.ip = "192.168.1.5"
        printer.mqtt_timeout = 30.0

        before = socket_mod.getdefaulttimeout()
        with (
            patch.object(mqtt_tls, "_resolve_ip", return_value="192.168.1.5"),
            patch.object(mqtt_tls.socket, "setdefaulttimeout") as set_default,
        ):
            mqtt_tls._mqtt_connect(printer, client)

        self.assertEqual(client._connect_timeout, 30.0)
        client.connect.assert_called_once_with("192.168.1.5", 8883, keepalive=10)
        set_default.assert_not_called()
        self.assertEqual(socket_mod.getdefaulttimeout(), before)

    def test_mqtt_port_rejects_unusable_values(self):
        from bambu_cli.protocols.mqtt_tls import _mqtt_port

        class _P:
            def __init__(self, mqtt_port):
                self.mqtt_port = mqtt_port

        self.assertEqual(_mqtt_port(_P(1883)), 1883)
        self.assertEqual(_mqtt_port(_P("1883")), 1883)
        self.assertEqual(_mqtt_port(_P(0)), 8883)
        self.assertEqual(_mqtt_port(_P(-1)), 8883)
        self.assertEqual(_mqtt_port(_P(70000)), 8883)
        self.assertEqual(_mqtt_port(_P("nope")), 8883)
        self.assertEqual(_mqtt_port(_P(True)), 8883)
        self.assertEqual(_mqtt_port(_P(None)), 8883)
        self.assertEqual(_mqtt_port(object()), 8883)

    def test_mqtt_connect_uses_configured_mqtt_port(self):
        from bambu_cli.protocols import mqtt_tls

        client = MagicMock()
        client._connect_timeout = 5.0
        printer = MagicMock()
        printer.ip = "192.168.1.5"
        printer.mqtt_timeout = 10.0
        printer.mqtt_port = 1883
        with patch.object(mqtt_tls, "_resolve_ip", return_value="192.168.1.5"):
            mqtt_tls._mqtt_connect(printer, client)
        client.connect.assert_called_once_with("192.168.1.5", 1883, keepalive=10)

    def test_mqtt_connect_uses_paho_public_connect_timeout(self):
        from bambu_cli.protocols import mqtt_tls

        class _PahoLike:
            def __init__(self):
                self._connect_timeout = 5.0
                self.connected = None

            @property
            def connect_timeout(self):
                return self._connect_timeout

            @connect_timeout.setter
            def connect_timeout(self, value):
                self._connect_timeout = value

            def connect(self, host, port, keepalive=10):
                self.connected = (host, port, keepalive)

        client = _PahoLike()
        printer = MagicMock()
        printer.ip = "192.168.1.5"
        printer.mqtt_timeout = 30.0
        with patch.object(mqtt_tls, "_resolve_ip", return_value="192.168.1.5"):
            mqtt_tls._mqtt_connect(printer, client)

        self.assertEqual(client.connect_timeout, 30.0)
        self.assertEqual(client.connected, ("192.168.1.5", 8883, 10))


if __name__ == "__main__":
    unittest.main()
