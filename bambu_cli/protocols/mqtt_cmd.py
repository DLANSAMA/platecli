"""MQTT command and status request/response."""

from __future__ import annotations

import json
import ssl
import threading

from bambu_cli.errors import PrinterStatusIncomplete
from bambu_cli.logging_utils import logger
from bambu_cli.utils import get_sequence_id

_REQUIRED_STATUS_KEYS = ("gcode_state", "mc_percent", "bed_temper", "nozzle_temper")
TERMINAL_GCODE_STATES = frozenset({"FINISH", "FAILED", "STOP", "IDLE"})


def _client_factory(client_factory):
    if client_factory is not None:
        return client_factory
    from bambu_cli.protocols import mqtt as mqtt_mod

    return mqtt_mod.create_mqtt_client


def _connect(printer, client):
    from bambu_cli.protocols import mqtt as mqtt_mod

    return mqtt_mod._mqtt_connect(printer, client)


def _sleep(sleep):
    if sleep is not None:
        return sleep
    from bambu_cli.protocols import mqtt as mqtt_mod

    return mqtt_mod.time.sleep


def _teardown_mqtt_client(client):
    """Stop the paho loop and drop the socket. Safe if connect/loop never started."""
    try:
        client.loop_stop()
    except Exception:
        pass
    try:
        client.disconnect()
    except Exception:
        pass


def send_command(
    printer,
    payload,
    timeout=None,
    retries=2,
    *,
    client_factory=None,
    sleep=None,
):
    """Send a command to the printer with retries.

    ``client_factory`` and ``sleep`` default to create_mqtt_client / time.sleep;
    tests inject fakes instead of patching module globals.
    """
    _factory = _client_factory(client_factory)
    _sleep_fn = _sleep(sleep)
    if timeout is None:
        timeout = printer.mqtt_timeout

    if printer.simulation_mode:
        logger.info(f"🤖 [SIM] Sending command: {payload}")
        return True

    session = getattr(printer, "_mqtt_session", None)
    if session is not None:
        return session.send_command(payload, timeout, retries)

    for attempt in range(retries + 1):
        client = _factory(printer)
        client.user_data_set({})
        publish_done = threading.Event()
        success = [False]
        published = [False]

        def on_connect(client, userdata, flags, rc, properties=None, published=published):
            if rc == 0:
                if not published[0]:
                    published[0] = True
                    client.publish(f"device/{printer.serial}/request", payload, qos=1)
            else:
                logger.error(f"Connection failed: rc={rc}")
                publish_done.set()

        def on_publish(client, userdata, mid, reason_code=None, properties=None):
            success[0] = True
            publish_done.set()

        client.on_connect = on_connect
        client.on_publish = on_publish

        try:
            _connect(printer, client)
            client.loop_start()
            if publish_done.wait(timeout):
                return success[0]
            if attempt < retries:
                logger.warning(f"MQTT command timeout on attempt {attempt + 1}. Retrying...")
                _sleep_fn(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT command attempt {attempt + 1} failed: {e}. Retrying...")
                _sleep_fn(2**attempt)
            else:
                logger.error(f"MQTT command error: {e}")
        finally:
            _teardown_mqtt_client(client)

    return False


def status_is_complete(data):
    """True when ``data`` is a full snapshot rather than an incremental delta."""
    return isinstance(data, dict) and all(key in data for key in _REQUIRED_STATUS_KEYS)


def get_status(printer, timeout=None, retries=2, *, require_complete=True):
    """Get printer status via MQTT with retries.

    Report-topic messages are merged into one accumulated state (later values
    win) and we keep waiting — re-issuing ``pushall`` on each retry — until every
    key in ``_REQUIRED_STATUS_KEYS`` is present, so callers never receive a
    delta dressed up as a snapshot.

    ``require_complete=False`` returns the first payload that arrives, for
    callers using the reply only as a liveness probe (``doctor``, print
    ``--dry-run``). With the default, a connection that yields nothing but
    deltas raises ``PrinterStatusIncomplete`` rather than returning a partial;
    a connection that yields nothing at all still returns ``None``.
    """
    if timeout is None:
        timeout = printer.mqtt_timeout

    if printer.simulation_mode:
        logger.info("🤖 [SIM] Fetching simulated printer status...")
        return {
            "gcode_state": "IDLE",
            "mc_percent": 0,
            "hw_ver": "P1P-SIM",
            "sw_ver": "01.XX.XX.XX",
            "bed_temper": 25,
            "bed_target_temper": 0,
            "nozzle_temper": 25,
            "nozzle_target_temper": 0,
            "cooling_fan_speed": 0,
            "wifi_signal": "-42dBm",
            "ams": {
                "tray_now": "0",
                "ams": [
                    {
                        "id": "0",
                        "humidity": "5",
                        "temp": "26.0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "F2F2F2FF", "remain": 90},
                            {"id": "1", "tray_type": "PETG", "tray_color": "0A0AC8FF", "remain": 60},
                            {"id": "2"},
                            {"id": "3", "tray_type": "TPU", "tray_color": "000000FF", "remain": 45},
                        ],
                    }
                ],
            },
        }

    session = getattr(printer, "_mqtt_session", None)
    if session is not None:
        return session.get_status(timeout, retries, require_complete=require_complete)

    merged: dict = {}
    merged_lock = threading.Lock()
    _factory = _client_factory(None)
    _sleep_fn = _sleep(None)

    for attempt in range(retries + 1):
        status_received = threading.Event()
        connect_failed = [False]
        client = _factory(printer)
        client.user_data_set({})

        def on_connect(
            client,
            userdata,
            flags,
            rc,
            properties=None,
            connect_failed=connect_failed,
            status_received=status_received,
        ):
            if rc == 0:
                client.subscribe(f"device/{printer.serial}/report")
                push = json.dumps({"pushing": {"sequence_id": get_sequence_id(), "command": "pushall"}})
                client.publish(f"device/{printer.serial}/request", push)
            else:
                logger.error(f"Connection failed: rc={rc}")
                connect_failed[0] = True
                status_received.set()

        def on_message(client, userdata, msg, status_received=status_received):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.debug(f"MQTT decode error: {e}")
                return
            if not isinstance(data, dict) or not isinstance(data.get("print"), dict):
                return
            with merged_lock:
                merged.update(data["print"])
                complete = status_is_complete(merged)
            if complete or not require_complete:
                status_received.set()

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            _connect(printer, client)
            client.loop_start()
            if status_received.wait(timeout):
                if connect_failed[0]:
                    return None
                with merged_lock:
                    snapshot = dict(merged)
                if snapshot:
                    return snapshot
            if attempt < retries:
                with merged_lock:
                    saw_partial = bool(merged)
                if saw_partial:
                    logger.warning(
                        f"Printer sent only partial status on attempt {attempt + 1}. Re-requesting full state..."
                    )
                else:
                    logger.warning(f"MQTT status timeout on attempt {attempt + 1}. Retrying...")
                _sleep_fn(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT status attempt {attempt + 1} failed: {e}. Retrying...")
                _sleep_fn(2**attempt)
            else:
                logger.error(f"MQTT status error: {e}")
        finally:
            _teardown_mqtt_client(client)

    with merged_lock:
        partial = dict(merged)
    if partial and require_complete and not status_is_complete(partial):
        missing = [key for key in _REQUIRED_STATUS_KEYS if key not in partial]
        raise PrinterStatusIncomplete(
            "Printer returned only partial status updates, never a full snapshot "
            f"(missing {', '.join(missing)}). It may be busy mid-print; retry the command.",
            detail={"missing_keys": missing, "received_keys": sorted(partial)},
            next_command="plate status",
        )
    if partial and (not require_complete or status_is_complete(partial)):
        return partial
    return None


def get_version(printer, timeout=5, retries=1):
    """Fetch printer module versions via the MQTT get_version command."""
    if printer.simulation_mode:
        return [{"name": "ota", "sw_ver": "01.00.00.00", "hw_ver": "P1P-SIM"}]

    session = getattr(printer, "_mqtt_session", None)
    if session is not None:
        return session.get_version(timeout, retries)

    _factory = _client_factory(None)
    _sleep_fn = _sleep(None)

    for attempt in range(retries + 1):
        result = {"modules": None}
        received = threading.Event()
        client = _factory(printer)
        client.user_data_set({})

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                client.subscribe(f"device/{printer.serial}/report")
                client.publish(
                    f"device/{printer.serial}/request",
                    json.dumps({"info": {"sequence_id": get_sequence_id(), "command": "get_version"}}),
                )
            else:
                logger.error(f"Connection failed: rc={rc}")
                received.set()

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            info = data.get("info")
            if isinstance(info, dict) and info.get("command") == "get_version" and "module" in info:
                result["modules"] = info["module"]
                received.set()

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            _connect(printer, client)
            client.loop_start()
            if received.wait(timeout):
                return result["modules"]
            if attempt < retries:
                _sleep_fn(2**attempt)
        except (OSError, ssl.SSLError):
            if attempt < retries:
                _sleep_fn(2**attempt)
        finally:
            _teardown_mqtt_client(client)

    return None
