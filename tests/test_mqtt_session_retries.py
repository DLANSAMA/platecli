"""Tests for MqttSession connection timeout retrying and broker failure handling."""

from unittest.mock import MagicMock

from bambu_cli.protocols.mqtt_session import MqttSession


def test_get_status_retries_on_connection_timeout():
    printer = MagicMock()
    printer.ip = "192.168.1.10"
    printer.access_code = "12345678"
    printer.serial = "SERIAL001"
    printer.cert_fingerprint = None
    printer.simulation_mode = False

    session = MqttSession(printer)

    attempts = 0

    def mock_connect(client):
        nonlocal attempts
        attempts += 1
        # Never trigger on_connect to simulate timeout

    session._connect = mock_connect
    session._sleep = MagicMock()

    # timeout very short so test runs fast
    res = session.get_status(timeout=0.01, retries=2)
    assert res is None
    # 1 initial attempt + 2 retries = 3 attempts
    assert attempts == 3
    assert session._sleep.call_count == 2


def test_get_status_aborts_immediately_on_broker_failure():
    printer = MagicMock()
    printer.ip = "192.168.1.10"
    printer.access_code = "12345678"
    printer.serial = "SERIAL001"
    printer.cert_fingerprint = None
    printer.simulation_mode = False

    session = MqttSession(printer)

    attempts = 0

    def mock_connect(client):
        nonlocal attempts
        attempts += 1
        # Trigger on_connect with rc=5 (bad auth)
        if client.on_connect:
            client.on_connect(client, None, None, 5)

    session._connect = mock_connect
    session._sleep = MagicMock()

    res = session.get_status(timeout=0.1, retries=2)
    assert res is None
    # Must NOT retry if broker explicitly rejected connection
    assert attempts == 1
    assert session._sleep.call_count == 0


def test_send_command_retries_on_connection_timeout():
    printer = MagicMock()
    printer.ip = "192.168.1.10"
    printer.access_code = "12345678"
    printer.serial = "SERIAL001"
    printer.cert_fingerprint = None
    printer.simulation_mode = False

    session = MqttSession(printer)

    attempts = 0

    def mock_connect(client):
        nonlocal attempts
        attempts += 1
        # Never trigger on_connect to simulate connection timeout

    session._connect = mock_connect
    session._sleep = MagicMock()

    res = session.send_command('{"test": "payload"}', timeout=0.01, retries=2)
    assert res is False
    assert attempts == 3
    assert session._sleep.call_count == 2


def test_send_command_aborts_immediately_on_broker_failure():
    printer = MagicMock()
    printer.ip = "192.168.1.10"
    printer.access_code = "12345678"
    printer.serial = "SERIAL001"
    printer.cert_fingerprint = None
    printer.simulation_mode = False

    session = MqttSession(printer)

    attempts = 0

    def mock_connect(client):
        nonlocal attempts
        attempts += 1
        if client.on_connect:
            client.on_connect(client, None, None, 5)

    session._connect = mock_connect
    session._sleep = MagicMock()

    res = session.send_command('{"test": "payload"}', timeout=0.1, retries=2)
    assert res is False
    assert attempts == 1
    assert session._sleep.call_count == 0
