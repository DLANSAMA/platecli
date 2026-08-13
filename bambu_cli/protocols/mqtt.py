"""MQTT transport facade.

Implementations live in ``mqtt_tls``, ``mqtt_cmd``, ``mqtt_print``,
``mqtt_monitor``, and ``mqtt_session``. This module re-exports the public
names so existing imports and test patches on ``bambu_cli.protocols.mqtt``
keep working.
"""

from __future__ import annotations

import time

from bambu_cli.protocols.mqtt_cmd import (
    _REQUIRED_STATUS_KEYS,
    TERMINAL_GCODE_STATES,
    get_status,
    get_version,
    send_command,
    status_is_complete,
)
from bambu_cli.protocols.mqtt_monitor import _status_event, monitor_status
from bambu_cli.protocols.mqtt_print import _printer_error_hex, execute_print_command
from bambu_cli.protocols.mqtt_session import MqttSession
from bambu_cli.protocols.mqtt_tls import (
    PinningSSLContext,
    _mqtt_connect,
    _require_mqtt,
    _SimMqttClient,
    create_mqtt_client,
    mqtt,
    pinning_ssl_context,
    probe_cert_fingerprint,
)

__all__ = [
    "TERMINAL_GCODE_STATES",
    "PinningSSLContext",
    "_REQUIRED_STATUS_KEYS",
    "_SimMqttClient",
    "_mqtt_connect",
    "_printer_error_hex",
    "_require_mqtt",
    "_status_event",
    "MqttSession",
    "create_mqtt_client",
    "execute_print_command",
    "get_status",
    "get_version",
    "monitor_status",
    "mqtt",
    "pinning_ssl_context",
    "probe_cert_fingerprint",
    "send_command",
    "status_is_complete",
    "time",
]
