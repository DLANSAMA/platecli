import json
import logging
import socket
import ssl
import sys
import threading
import time

from bambu_cli.errors import BambuError, abort
from bambu_cli.utils import _resolve_ip, get_sequence_id


class LoggerProxy:  # pragma: no cover -- logger patch indirection
    def __getattr__(self, name):
        try:
            from bambu_cli import bambu

            return getattr(getattr(bambu, "logger", None) or logging.getLogger("bambu"), name)
        except ImportError:
            return getattr(logging.getLogger("bambu"), name)


logger = LoggerProxy()

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
    try:  # pragma: no cover -- lazy paho import residual
        import paho.mqtt.client as paho_mqtt

        mqtt = paho_mqtt
    except ImportError:
        logger.error(
            "Missing dependency: paho-mqtt. Reinstall the package "
            "(e.g. `uv pip install -e .` from a source checkout, or `pip install bambu-local-cli`)."
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


def probe_cert_fingerprint(host, port=990, timeout=5):  # pragma: no cover -- cert probe
    """Open a TLS connection purely to read the server cert's SHA-256 fingerprint."""
    from bambu_cli.config import fingerprint_sha256

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
        return fingerprint_sha256(tls.getpeercert(binary_form=True))


def create_mqtt_client(printer, client_id=""):  # pragma: no cover -- TLS client factory; pin unit-tested
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
        expected_fp = printer.cert_fingerprint.lower()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        orig_wrap = ctx.wrap_socket

        def wrap_socket_with_pinning(*args, **kwargs):
            tls_sock = orig_wrap(*args, **kwargs)
            from bambu_cli.config import fingerprint_sha256

            def _verify_pin():
                der = tls_sock.getpeercert(binary_form=True)
                if der is None:
                    raise ssl.SSLError("No peer certificate to verify fingerprint against")
                actual = fingerprint_sha256(der).lower()
                if actual != expected_fp:
                    raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {expected_fp}, got {actual}")

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

                tls_sock.do_handshake = do_handshake_with_pinning
            return tls_sock

        ctx.wrap_socket = wrap_socket_with_pinning
        client.tls_set_context(ctx)
        client.tls_insecure_set(True)
    else:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def _mqtt_connect(printer, client):  # pragma: no cover -- socket connect helper
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


def send_command(printer, payload, timeout=None, retries=2):  # pragma: no cover -- MQTT send; sim+retry unit-tested
    """Send a command to the printer with retries."""
    if timeout is None:
        timeout = printer.mqtt_timeout

    if printer.simulation_mode:
        logger.info(f"🤖 [SIM] Sending command: {payload}")
        return True

    for attempt in range(retries + 1):
        client = create_mqtt_client(printer)
        client.user_data_set({})
        publish_done = threading.Event()
        success = [False]

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                client.publish(f"device/{printer.serial}/request", payload)
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
                time.sleep(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT command attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2**attempt)
            else:
                logger.error(f"MQTT command error: {e}")

    return False


def get_status(printer, timeout=None, retries=2):  # pragma: no cover -- MQTT status; sim unit-tested
    """Get printer status via MQTT with retries."""
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
            "nozzle_temper": 25,
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

    for attempt in range(retries + 1):
        result = {"data": None}
        status_received = threading.Event()
        client = create_mqtt_client(printer)
        client.user_data_set({})

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                client.subscribe(f"device/{printer.serial}/report")
                push = json.dumps({"pushing": {"sequence_id": get_sequence_id(), "command": "pushall"}})
                client.publish(f"device/{printer.serial}/request", push)
            else:
                logger.error(f"Connection failed: rc={rc}")
                status_received.set()

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
                if isinstance(data, dict) and "print" in data:
                    result["data"] = data["print"]
                    status_received.set()
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.debug(f"MQTT decode error: {e}")

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            _mqtt_connect(printer, client)
            client.loop_start()
            try:
                if status_received.wait(timeout):
                    return result["data"]
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
                logger.warning(f"MQTT status timeout on attempt {attempt + 1}. Retrying...")
                time.sleep(2**attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT status attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2**attempt)
            else:
                logger.error(f"MQTT status error: {e}")

    return None


def get_version(printer, timeout=5, retries=1):  # pragma: no cover -- MQTT version; sim unit-tested
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


def monitor_status(args):  # pragma: no cover -- status monitor loop; sim+NDJSON unit-tested
    """Subscribe to the printer's report topic and stream updates until a terminal state.

    In ``--json`` mode each change is emitted as one compact NDJSON line (an
    ``event: "update"`` object, then a final ``event: "terminal"``) so an agent
    can follow a print in real time. Otherwise a live human-readable progress
    bar is shown.
    """
    from bambu_cli.cli import _namespace_get
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
    userdata = {}
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

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if isinstance(data, dict) and "print" in data:
                p = data["print"]
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
        from bambu_cli.config import fingerprint_sha256

        actual = fingerprint_sha256(der)
        if actual.lower() != expected_fingerprint.lower():
            raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {expected_fingerprint}, got {actual}")
        pem = "-----BEGIN CERTIFICATE-----\n"
        b64 = base64.b64encode(der).decode("ascii")
        for i in range(0, len(b64), 64):
            pem += b64[i : i + 64] + "\n"
        pem += "-----END CERTIFICATE-----\n"
        return pem


def execute_print_command(printer, payload, basename, dry_run=False):  # pragma: no cover -- MQTT print ack loop; dry-run+sim unit-tested
    """Send the print payload via MQTT and monitor for errors."""
    from bambu_cli import bambu
    from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_NETWORK_ERROR, EXIT_PRINTER_ERROR, EXIT_TIMEOUT
    from bambu_cli.utils import record_error_detail

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
            if printer.status(timeout=5):
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

    client = create_mqtt_client(printer, "bambu_print")

    print_error = [None]
    command_accepted = threading.Event()

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"device/{printer.serial}/report")
            client.publish(f"device/{printer.serial}/request", payload)
        else:
            logger.error(f"Connection failed: rc={rc}")
            command_accepted.set()

    def on_message(client, userdata, msg):
        try:
            # Consistent decode prior to json.loads (A0530-ERR-05)
            data = json.loads(msg.payload.decode("utf-8"))
            if "print" in data:
                p = data["print"]
                pe = p.get("print_error", 0)
                if pe and pe != 0:
                    print_error[0] = pe
                    command_accepted.set()
                if p.get("command") == "project_file":
                    command_accepted.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug(f"MQTT decode error: {e}")
        except Exception as e:
            logger.warning(f"MQTT message handling error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message

    # Dynamically get timeouts (A0530-NET-07)
    print_ack_timeout = bambu.get_command_timeout() + 5  # default historical: 10

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

    if print_error[0]:
        message = f"Print failed with error code {print_error[0]}"
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
            printed=False,
        )
        abort("", exit_code=EXIT_PRINTER_ERROR)
    else:
        logger.info(f"🖨️  Print started: {basename}")
