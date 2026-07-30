import json
import socket
import ssl
import sys
import threading
import time
from typing import Optional

from bambu_cli.config import get_command_timeout
from bambu_cli.errors import BambuError, PrinterStatusIncomplete, abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _resolve_ip, get_sequence_id

# Lazily import mqtt or load at module level if available
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


def _require_mqtt():
    """Ensure paho-mqtt is importable; abort with config exit if missing."""
    global mqtt
    if mqtt is not None:
        return
    # The import/abort paths only run when the optional dep is absent at import
    # time (not exercised in CI where paho-mqtt is installed).
    try:
        import paho.mqtt.client as paho_mqtt

        mqtt = paho_mqtt
    except ImportError:
        logger.error(
            "Missing dependency: paho-mqtt. Reinstall the package "
            "(e.g. `uv pip install -e .` from a source checkout, or `pip install platecli`)."
        )
        from bambu_cli.constants import EXIT_CONFIG_ERROR
        from bambu_cli.errors import abort

        abort("", exit_code=EXIT_CONFIG_ERROR)


class _SimMqttClient:
    """Small MQTT stand-in for --sim without importing test-only mocks."""

    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.on_publish = None

    def username_pw_set(self, username, password):
        pass

    def tls_set(self, *args, **kwargs):
        pass

    def tls_insecure_set(self, *args, **kwargs):
        pass

    def connect(self, host, port, keepalive=10):
        if self.on_connect:
            self.on_connect(self, None, None, 0)

    def subscribe(self, topic):
        pass

    def publish(self, topic, payload):
        if self.on_publish:
            self.on_publish(self, None, 1)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def socket(self):
        return None


# _resolve_ip is imported from bambu_cli.utils


def probe_cert_fingerprint(host, port=990, timeout=5):
    """Open a TLS connection purely to read the server cert's SHA-256 fingerprint."""
    from bambu_cli.config import fingerprint_sha256

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
        return fingerprint_sha256(tls.getpeercert(binary_form=True))


def create_mqtt_client(printer, client_id=""):
    global _TRUSTED_CERT_FILE
    if printer.simulation_mode:
        return _SimMqttClient()

    _require_mqtt()
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
    except AttributeError:
        client = mqtt.Client(client_id)
    client.username_pw_set("bblp", printer.access_code)

    if printer.insecure_tls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    elif printer.cert_fingerprint:
        expected_fp = printer.cert_fingerprint
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        orig_wrap = ctx.wrap_socket

        def wrap_socket_with_pinning(*args, **kwargs):
            tls_sock = orig_wrap(*args, **kwargs)
            from bambu_cli.tlspin import verify_cert_fingerprint

            def _verify_pin():
                der = tls_sock.getpeercert(binary_form=True)
                verify_cert_fingerprint(der, expected_fp)

            # paho wraps with do_handshake_on_connect=False, so the peer cert
            # is not available yet; defer verification to handshake completion.
            try:
                tls_sock.getpeercert(binary_form=True)
                handshake_done = True
            except ValueError:
                handshake_done = False
            if handshake_done:
                _verify_pin()
            else:
                orig_handshake = tls_sock.do_handshake

                def do_handshake_with_pinning(*hs_args, **hs_kwargs):
                    orig_handshake(*hs_args, **hs_kwargs)
                    _verify_pin()

                tls_sock.do_handshake = do_handshake_with_pinning  # type: ignore[method-assign]
            return tls_sock

        ctx.wrap_socket = wrap_socket_with_pinning  # type: ignore[method-assign]
        client.tls_set_context(ctx)
        client.tls_insecure_set(True)
    else:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def _mqtt_connect(printer, client):
    resolved_ip = _resolve_ip(printer.ip)
    old_timeout = socket.getdefaulttimeout()
    try:
        # Bound the connect phase by the printer's configured timeout. The socket
        # default covers blocking name/socket ops, while paho caps its own TCP/TLS
        # connect via client._connect_timeout (default 5s) independent of the
        # socket default — so set both to actually honor the configured value.
        socket.setdefaulttimeout(printer.mqtt_timeout)
        if hasattr(client, "_connect_timeout"):
            client._connect_timeout = printer.mqtt_timeout
        client.connect(resolved_ip, 8883, keepalive=10)
    finally:
        socket.setdefaulttimeout(old_timeout)


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
    _client_factory = client_factory if client_factory is not None else create_mqtt_client
    _sleep = sleep if sleep is not None else time.sleep
    if timeout is None:
        timeout = printer.mqtt_timeout

    if printer.simulation_mode:
        logger.info(f"🤖 [SIM] Sending command: {payload}")
        return True

    for attempt in range(retries + 1):
        client = _client_factory(printer)
        client.user_data_set({})
        publish_done = threading.Event()
        success = [False]
        # Once-flag so paho's auto-reconnect (loop_start -> loop_forever with
        # reconnect_on_failure=True) can never fire on_connect a second time and
        # re-publish this state-changing command (pause/stop/gcode_line) to the
        # printer. Bound as a default arg so a stale callback from a previous
        # attempt's client cannot re-publish either.
        published = [False]

        def on_connect(client, userdata, flags, rc, properties=None, published=published):
            if rc == 0:
                if not published[0]:
                    published[0] = True
                    # QoS 1: pairs on_publish with a broker PUBACK rather than a
                    # bare local socket write. The Bambu broker is the printer,
                    # so a PUBACK is real receipt, not "left our OS buffer".
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
            _mqtt_connect(printer, client)
            client.loop_start()
            try:
                if publish_done.wait(timeout):
                    return success[0]
            finally:
                try:
                    client.loop_stop()
                except Exception:
                    pass
                try:
                    client.disconnect()
                except Exception:
                    pass

            if attempt < retries:
                logger.warning(f"MQTT command timeout on attempt {attempt + 1}. Retrying...")
                _sleep(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT command attempt {attempt + 1} failed: {e}. Retrying...")
                _sleep(2**attempt)
            else:
                logger.error(f"MQTT command error: {e}")

    return False


# Keys a full state snapshot always carries. The printer publishes incremental
# deltas on the report topic and only answers `pushall` with the complete state,
# so "a message arrived" is not the same as "we have the state" — mid-print the
# first thing to land is often a lone nozzle_temper reading. These four are what
# both docs/schemas/status.json and the human renderer treat as always-present,
# so they are the gate for telling a snapshot from a delta.
_REQUIRED_STATUS_KEYS = ("gcode_state", "mc_percent", "bed_temper", "nozzle_temper")


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
            # A representative AMS so agents can exercise `status --json` AMS
            # parsing (and --ams-mapping decisions) without hardware.
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

    # Accumulated across attempts: a retry re-issues pushall, and keys collected
    # before the timeout are still the freshest we have.
    merged: dict = {}
    merged_lock = threading.Lock()

    for attempt in range(retries + 1):
        status_received = threading.Event()
        connect_failed = [False]
        client = create_mqtt_client(printer)
        client.user_data_set({})

        # status_received / connect_failed are bound per attempt so a late
        # callback from a previous attempt's client cannot wake this one.
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
            _mqtt_connect(printer, client)
            client.loop_start()
            try:
                if status_received.wait(timeout):
                    if connect_failed[0]:
                        return None
                    with merged_lock:
                        snapshot = dict(merged)
                    # Never hand back a falsy-but-not-None {}: callers treat the
                    # return value as "reachable or not".
                    if snapshot:
                        return snapshot
            finally:
                try:
                    client.loop_stop()
                except Exception:
                    pass
                try:
                    client.disconnect()
                except Exception:
                    pass
            if attempt < retries:
                with merged_lock:
                    saw_partial = bool(merged)
                if saw_partial:
                    logger.warning(
                        f"Printer sent only partial status on attempt {attempt + 1}. Re-requesting full state..."
                    )
                else:
                    logger.warning(f"MQTT status timeout on attempt {attempt + 1}. Retrying...")
                time.sleep(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT status attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2**attempt)
            else:
                logger.error(f"MQTT status error: {e}")

    with merged_lock:
        partial = dict(merged)
    if partial and require_complete:
        # We reached the printer, it just never answered pushall with a whole
        # state. Emitting `partial` here is what hands agents a KeyError later.
        missing = [key for key in _REQUIRED_STATUS_KEYS if key not in partial]
        raise PrinterStatusIncomplete(
            "Printer returned only partial status updates, never a full snapshot "
            f"(missing {', '.join(missing)}). It may be busy mid-print; retry the command.",
            detail={"missing_keys": missing, "received_keys": sorted(partial)},
            next_command="plate status",
        )
    return None


def get_version(printer, timeout=5, retries=1):
    """Fetch printer module versions via the MQTT get_version command."""
    if printer.simulation_mode:
        return [{"name": "ota", "sw_ver": "01.00.00.00", "hw_ver": "P1P-SIM"}]

    for attempt in range(retries + 1):
        result = {"modules": None}
        received = threading.Event()
        client = create_mqtt_client(printer)
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
            _mqtt_connect(printer, client)
            client.loop_start()
            try:
                if received.wait(timeout):
                    return result["modules"]
            finally:
                try:
                    client.loop_stop()
                except Exception:
                    pass
                try:
                    client.disconnect()
                except Exception:
                    pass
            if attempt < retries:
                time.sleep(2**attempt)
        except (OSError, ssl.SSLError):
            if attempt < retries:
                time.sleep(2**attempt)

    return None


def _status_event(p, event):
    """Build a compact, agent-friendly status event from a raw MQTT print payload.

    ``event`` is ``"update"`` for an in-progress change or ``"terminal"`` for the
    final state. Only the fields agents care about for print progress are kept,
    so a streamed line stays small.
    """

    def _int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return {
        "event": event,
        "command": "status",
        "gcode_state": p.get("gcode_state", "UNKNOWN"),
        "mc_percent": _int(p.get("mc_percent", 0)),
        "layer_num": _int(p.get("layer_num", 0)),
        "total_layer_num": _int(p.get("total_layer_num", 0)),
        "mc_remaining_time": _int(p.get("mc_remaining_time", 0)),
        "nozzle_temper": p.get("nozzle_temper"),
        "nozzle_target_temper": p.get("nozzle_target_temper"),
        "bed_temper": p.get("bed_temper"),
        "bed_target_temper": p.get("bed_target_temper"),
        "gcode_file": p.get("gcode_file", ""),
    }


def monitor_status(args):
    """Subscribe to the printer's report topic and stream updates until a terminal state.

    In ``--json`` mode each change is emitted as one compact NDJSON line (an
    ``event: "update"`` object, then a final ``event: "terminal"``) so an agent
    can follow a print in real time. Otherwise a live human-readable progress
    bar is shown.
    """
    from bambu_cli.argutils import namespace_get as _namespace_get
    from bambu_cli.printer import get_printer
    from bambu_cli.utils import emit_json_line

    printer = get_printer()
    json_mode = bool(_namespace_get(args, "json", False))
    logger.info("📡 Starting status monitor loop. Press Ctrl+C to stop.")
    if printer.simulation_mode:
        # Stream the same shape of events a real print would, so agents can
        # exercise the --monitor --json contract without hardware.
        for state, pct, event in (("PREPARE", 0, "update"), ("RUNNING", 50, "update"), ("FINISH", 100, "terminal")):
            if json_mode:
                emit_json_line(_status_event({"gcode_state": state, "mc_percent": pct}, event))
            else:
                logger.info(f"🤖 [SIM] Simulated status: State={state}, Progress={pct}%")
            if event != "terminal":
                time.sleep(0.5)
        if not json_mode:
            logger.info("🏁 Reached terminal state: FINISH")
        return

    terminal_states = {"FINISH", "FAILED", "STOP", "IDLE"}
    received_terminal = threading.Event()
    show_progress_bar = not json_mode and sys.stdout.isatty()
    client = create_mqtt_client(printer)
    userdata: dict = {}
    client.user_data_set(userdata)

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"device/{printer.serial}/report")
            push = json.dumps({"pushing": {"sequence_id": get_sequence_id(), "command": "pushall"}})
            client.publish(f"device/{printer.serial}/request", push)
        else:
            logger.error(f"Connection failed: rc={rc}")
            received_terminal.set()

    last_state = [None]
    last_pct = [None]
    # Same accumulation as get_status: report-topic messages are deltas, so a
    # lone temperature update must not read as gcode_state=UNKNOWN at 0%.
    merged: dict = {}

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("print"), dict):
                merged.update(data["print"])
                p = merged
                state = p.get("gcode_state", "UNKNOWN")
                pct = p.get("mc_percent", 0)

                if state != last_state[0] or pct != last_pct[0]:
                    if "progress" not in userdata and not show_progress_bar:
                        userdata["progress"] = None
                    if "progress" not in userdata:
                        try:  # pragma: no cover -- rich TTY progress UI
                            from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

                            progress = Progress(
                                TextColumn("[bold blue]Print Status"),
                                BarColumn(),
                                "[progress.percentage]{task.percentage:>3.1f}%",
                                "•",
                                TextColumn("{task.description}"),
                                "•",
                                TimeElapsedColumn(),
                                transient=True,
                            )
                            progress.start()
                            userdata["progress"] = progress
                            userdata["task_id"] = progress.add_task(f"State: {state}", total=100, completed=pct)
                        except ImportError:
                            userdata["progress"] = None

                    if userdata.get("progress"):
                        userdata["progress"].update(userdata["task_id"], completed=pct, description=f"State: {state}")
                    elif json_mode:
                        emit_json_line(_status_event(p, "update"))
                    else:
                        logger.info(f"⏳ Status: State={state}, Progress={pct}%")

                    last_state[0] = state
                    last_pct[0] = pct

                if state in terminal_states:
                    if json_mode:
                        emit_json_line(_status_event(p, "terminal"))
                    else:
                        logger.info(f"🏁 Reached terminal state: {state}")
                    received_terminal.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug(f"MQTT decode error: {e}")
        except Exception as e:
            logger.warning(f"MQTT message handling error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        _mqtt_connect(printer, client)
        client.loop_start()
        while not received_terminal.is_set():
            received_terminal.wait(1.0)
    except KeyboardInterrupt:
        logger.info("\n🛑 Monitor loop stopped by user.")
    finally:
        if userdata.get("progress"):
            try:
                userdata["progress"].stop()
            except Exception:
                pass
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


import base64

_TRUSTED_CERT_FILE = None

# probe_cert_fingerprint is defined above


def _get_and_verify_cert_pem(host, port, expected_fingerprint, timeout=5):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True)
        from bambu_cli.tlspin import verify_cert_fingerprint

        verify_cert_fingerprint(der, expected_fingerprint)
        assert der is not None  # verify_cert_fingerprint raises on a missing cert
        pem = "-----BEGIN CERTIFICATE-----\n"
        b64 = base64.b64encode(der).decode("ascii")
        for i in range(0, len(b64), 64):
            pem += b64[i : i + 64] + "\n"
        pem += "-----END CERTIFICATE-----\n"
        return pem


def _printer_error_hex(code: object) -> Optional[str]:
    """Render a printer error code as the hex form Bambu documents (e.g. 0x0500C010).

    Returns None when the code is not an integer we can render.
    """
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return f"0x{code & 0xFFFFFFFF:08X}"


def execute_print_command(
    printer,
    payload,
    basename,
    dry_run=False,
    *,
    command_timeout=None,
    client_factory=None,
):
    """Send the print payload via MQTT and monitor for errors.

    ``command_timeout`` and ``client_factory`` are injectable; defaults are
    get_command_timeout() / create_mqtt_client.
    """
    from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_NETWORK_ERROR, EXIT_PRINTER_ERROR, EXIT_TIMEOUT
    from bambu_cli.utils import record_error_detail

    _client_factory = client_factory if client_factory is not None else create_mqtt_client

    if dry_run:
        logger.info(f"🔍 Dry Run: Checking if {basename} exists on printer...")
        try:
            with printer.get_ftp_client(timeout=5) as ftp:
                files = ftp.nlst("/model/")
                if basename in files or f"/model/{basename}" in files:
                    logger.info(f"   ✅ File {basename} found on printer.")
                else:
                    message = f"File {basename} was not found on printer. Upload it first."
                    logger.error(f"   ❌ File {basename} NOT found on printer. Upload it first.")
                    record_error_detail(
                        "print", EXIT_FILE_ERROR, message, failed_step="dry_run", file=basename, printed=False
                    )
                    abort("", exit_code=EXIT_FILE_ERROR)
            logger.info("   ✅ Printer reachable via MQTT (status check)...")
            if printer.status(timeout=5, require_complete=False):
                logger.info("   ✅ MQTT connection verified.")
            else:
                message = "MQTT connection failed."
                logger.error(f"   ❌ {message}")
                record_error_detail(
                    "print", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=basename, printed=False
                )
                abort("", exit_code=EXIT_NETWORK_ERROR)
            return
        except BambuError:
            raise
        except Exception as e:
            message = f"Dry run failed: {e}"
            logger.error(message)
            record_error_detail(
                "print", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=basename, printed=False
            )
            abort("", exit_code=EXIT_NETWORK_ERROR)

    if printer.simulation_mode:
        from bambu_cli.protocols.ftps import _SIM_FTP_FILES

        if basename not in _SIM_FTP_FILES:
            message = f"File {basename} not found on simulated printer. Upload it first."
            logger.error(message)
            record_error_detail("print", EXIT_FILE_ERROR, message, failed_step="print", file=basename, printed=False)
            abort("", exit_code=EXIT_FILE_ERROR)
        logger.info(f"🤖 [SIM] Print started: {basename}")
        return

    client = _client_factory(printer, "bambu_print")

    print_error = [None]
    reject_reason: list[Optional[str]] = [None]
    command_accepted = threading.Event()
    # rc != 0 (bad CONNACK, e.g. wrong/rotated LAN access code) is delivered
    # asynchronously via on_connect after loop_start, so _mqtt_connect cannot
    # raise on it. Track it like get_status does and fail closed after the wait
    # instead of setting command_accepted and falling through to "Print started".
    connect_failed = [False]
    # Once-flag so paho auto-reconnect can never re-fire on_connect and re-issue
    # the print-start command to a printer that may already be running it.
    published = [False]
    # A stale, latched print_error from a *prior* job rides in on the printer's
    # first periodic *full-state snapshot* (P1-series push the complete state on
    # the report topic). Before our project_file ack lands, such a snapshot must
    # not be blamed on this print. A genuine error for our command arrives either
    # with/after the ack, or as a lone print_error delta (not a full snapshot).
    ack_seen = [False]

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            if not published[0]:
                published[0] = True
                client.subscribe(f"device/{printer.serial}/report")
                client.publish(f"device/{printer.serial}/request", payload)
        else:
            logger.error(f"Connection failed: rc={rc}")
            connect_failed[0] = True
            command_accepted.set()

    def on_message(client, userdata, msg):
        try:
            # Consistent decode prior to json.loads (A0530-ERR-05)
            data = json.loads(msg.payload.decode("utf-8"))
            if "print" in data:
                p = data["print"]
                is_ack = p.get("command") == "project_file"
                if is_ack:
                    ack_seen[0] = True
                pe = p.get("print_error", 0)
                # Blame the error on this print unless it is a pre-existing value
                # latched into a full-state snapshot that arrived before our ack
                # (the classic stale-latch vector). Errors in/after the ack, or in
                # a lone delta, are ours.
                stale_snapshot = status_is_complete(p) and not ack_seen[0] and not is_ack
                if pe and pe != 0 and not stale_snapshot:
                    print_error[0] = pe
                    command_accepted.set()
                if is_ack:
                    # A firmware rejection carries result=fail (+ optional
                    # reason); do not report success for a rejected job.
                    result = p.get("result")
                    if isinstance(result, str) and result.strip().lower() not in ("", "success"):
                        reject_reason[0] = str(p.get("reason") or result)
                    command_accepted.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug(f"MQTT decode error: {e}")
        except Exception as e:
            logger.warning(f"MQTT message handling error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message

    # Dynamically get timeouts (A0530-NET-07)
    base_timeout = command_timeout if command_timeout is not None else get_command_timeout()
    print_ack_timeout = base_timeout + 5  # default historical: 10

    try:
        _mqtt_connect(printer, client)
        client.loop_start()
        try:
            accepted = command_accepted.wait(print_ack_timeout)
            if not accepted:
                message = f"Timed out waiting for printer to acknowledge print start for {basename}"
                logger.error(message)
                record_error_detail("print", EXIT_TIMEOUT, message, failed_step="print", file=basename, printed=False)
                abort("", exit_code=EXIT_TIMEOUT)
        finally:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
    except BambuError:
        raise
    except Exception as e:
        message = f"Error: {e}"
        logger.error(message)
        record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="print", file=basename, printed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)

    if connect_failed[0]:
        message = f"Failed to connect to printer to start print for {basename} (check LAN access code)"
        logger.error(message)
        record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="print", file=basename, printed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)

    if reject_reason[0]:
        message = f"Printer rejected print of {basename}: {reject_reason[0]}"
        logger.error(message)
        record_error_detail("print", EXIT_PRINTER_ERROR, message, failed_step="print", file=basename, printed=False)
        abort("", exit_code=EXIT_PRINTER_ERROR)

    if print_error[0]:
        error_hex = _printer_error_hex(print_error[0])
        message = f"Print failed with error code {print_error[0]}"
        if error_hex:
            message += f" (hex {error_hex})"
        logger.error(message)
        if print_error[0] == 83935248:
            logger.info("   File not found on printer SD card. Check filename with 'files' command.")
            record_error_detail(
                "print",
                EXIT_FILE_ERROR,
                "File not found on printer SD card. Check filename with 'files' command.",
                failed_step="print",
                file=basename,
                printer_error_code=print_error[0],
                printer_error_code_hex=error_hex,
                printed=False,
            )
            abort("", exit_code=EXIT_FILE_ERROR)
        record_error_detail(
            "print",
            EXIT_PRINTER_ERROR,
            message,
            failed_step="print",
            file=basename,
            printer_error_code=print_error[0],
            printer_error_code_hex=error_hex,
            printed=False,
        )
        abort("", exit_code=EXIT_PRINTER_ERROR)
    else:
        logger.info(f"🖨️  Print started: {basename}")
