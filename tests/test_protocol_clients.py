from tests.bambu_test_base import *  # noqa: F401,F403


class TestImplicitFTPS(unittest.TestCase):
    @patch("bambu_cli.bambu.socket.create_connection")
    @patch("bambu_cli.bambu.ssl.SSLContext")
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

    @patch("bambu_cli.bambu.socket.create_connection")
    @patch("bambu_cli.bambu.ssl.SSLContext")
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
    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    def test_send_command_success(self, mock_create):
        from bambu_cli.bambu import send_command

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 0)
            mock_client.on_publish(mock_client, None, 1)

        mock_client.connect.side_effect = side_effect_connect

        printer = _test_printer(ip="192.168.1.1")
        result = send_command(printer, '{"test": "payload"}')

        self.assertTrue(result)
        mock_client.connect.assert_called_with("192.168.1.1", 8883, keepalive=10)
        topic = f"device/{printer.serial}/request"
        mock_client.publish.assert_called_once_with(topic, '{"test": "payload"}')
        mock_client.loop_start.assert_called_once()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.bambu.time.sleep")
    def test_send_command_retry_timeout(self, mock_sleep, mock_create):
        from bambu_cli.bambu import send_command

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_client.connect.side_effect = OSError("Connection error")

        result = send_command(_test_printer(), '{"test": "payload"}')

        self.assertFalse(result)
        self.assertEqual(mock_client.connect.call_count, 3)

    @patch("bambu_cli.protocols.mqtt.create_mqtt_client")
    @patch("bambu_cli.bambu.logger")
    def test_send_command_on_connect_rc_error(self, mock_logger, mock_create):
        from bambu_cli.bambu import send_command

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        def side_effect_connect(host, port, keepalive):
            mock_client.on_connect(mock_client, None, None, 5)

        mock_client.connect.side_effect = side_effect_connect

        result = send_command(_test_printer(ip="192.168.1.1"), '{"test": "payload"}', timeout=0.1)

        self.assertFalse(result)
        mock_logger.error.assert_called_with("Connection failed: rc=5")


import socket


class TestGetFtp(unittest.TestCase):
    def setUp(self):
        from bambu_cli.protocols.ftps import connection_manager

        connection_manager.clear()
        self.addCleanup(connection_manager.clear)

    @patch("bambu_cli.protocols.ftps.ImplicitFTPS")
    def test_get_ftp_success(self, mock_implicit_ftps):
        # Setup mocks
        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        printer = _test_printer(ip="192.168.1.100", access_code="mock_access_code")

        # get_ftp/_create_raw_ftp now take the printer object
        result = get_ftp(printer)

        # Assertions
        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with("192.168.1.100", 990, timeout=60)
        mock_ftp_instance_login = mock_ftp_instance.login
        mock_ftp_instance_login.assert_called_once_with("bblp", "mock_access_code")
        mock_ftp_instance.prot_p.assert_called_once()

        from bambu_cli.protocols.ftps import PooledFTPWrapper

        self.assertIsInstance(result, PooledFTPWrapper)
        self.assertEqual(result._ftp, mock_ftp_instance)

    @patch("bambu_cli.protocols.ftps.ImplicitFTPS")
    def test_get_ftp_connect_failure(self, mock_implicit_ftps):
        # Setup mock to raise an exception on connect
        mock_ftp_instance = MagicMock()
        mock_implicit_ftps.return_value = mock_ftp_instance
        mock_ftp_instance.connect.side_effect = OSError("Connection Refused")
        printer = _test_printer(ip="192.168.1.100", access_code="mock_access_code")

        # Call the function and assert it raises
        with self.assertRaises(Exception) as context:
            get_ftp(printer)

        self.assertEqual(str(context.exception), "Connection Refused")
        mock_implicit_ftps.assert_called_once()
        mock_ftp_instance.connect.assert_called_once_with("192.168.1.100", 990, timeout=60)
        # Ensure it doesn't try to login if connect fails
        mock_ftp_instance.login.assert_not_called()
        mock_ftp_instance.prot_p.assert_not_called()


class TestCreateMqttClient(unittest.TestCase):
    def test_create_mqtt_client_simulation(self):
        from bambu_cli.bambu import create_mqtt_client

        client = create_mqtt_client(_test_printer(simulation_mode=True))
        from bambu_cli.protocols.mqtt import _SimMqttClient

        self.assertIsInstance(client, _SimMqttClient)

    @patch("bambu_cli.protocols.mqtt.mqtt.Client")
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

    @patch("bambu_cli.protocols.mqtt.mqtt.Client")
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
    def test_mqtt_connect_honors_configured_timeout_and_restores_socket_default(self):
        import socket as socket_mod

        from bambu_cli.protocols import mqtt

        client = MagicMock()
        client._connect_timeout = 5.0
        printer = MagicMock()
        printer.ip = "192.168.1.5"
        printer.mqtt_timeout = 30.0

        before = socket_mod.getdefaulttimeout()
        with patch.object(mqtt, "_resolve_ip", return_value="192.168.1.5"):
            mqtt._mqtt_connect(printer, client)

        # paho's own connect cap is raised to the configured timeout...
        self.assertEqual(client._connect_timeout, 30.0)
        client.connect.assert_called_once_with("192.168.1.5", 8883, keepalive=10)
        # ...and the process-wide socket default is left untouched afterwards.
        self.assertEqual(socket_mod.getdefaulttimeout(), before)


if __name__ == "__main__":
    unittest.main()
