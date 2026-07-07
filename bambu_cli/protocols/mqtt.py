import sys
import json
import socket
import ssl
from bambu_cli.utils import get_sequence_id, _resolve_ip
import logging
import threading
import time

class LoggerProxy:
    def __getattr__(self, name):
        try:
            from bambu_cli import bambu
            return getattr(getattr(bambu, "logger", None) or logging.getLogger("bambu"), name)
        except ImportError:
            return getattr(logging.getLogger("bambu"), name)

logger = LoggerProxy()
from bambu_cli.logging_utils import mockable

# Lazily import mqtt or load at module level if available
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

def _require_mqtt():
    global mqtt
    if mqtt is None:
        try:
            import paho.mqtt.client as paho_mqtt
            mqtt = paho_mqtt
        except ImportError:
            logger.error("Missing dependency: paho-mqtt. Install with: python -m pip install -r requirements.txt")
            from bambu_cli.constants import EXIT_CONFIG_ERROR
            sys.exit(EXIT_CONFIG_ERROR)


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
    with socket.create_connection((host, port), timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
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

def _mqtt_connect(printer, client):
    resolved_ip = _resolve_ip(printer.ip)
    old_timeout = socket.getdefaulttimeout()
    try:
        # timeout mutation fixed by explicit client connect keepalive logic elsewhere, but retaining for compatibility
        client.connect(resolved_ip, 8883, keepalive=10)
    finally:
        socket.setdefaulttimeout(old_timeout)

@mockable
def send_command(printer, payload, timeout=None, retries=2):
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
                time.sleep(2 ** attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT command attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2 ** attempt)
            else:
                logger.error(f"MQTT command error: {e}")

    return False


@mockable
def get_status(printer, timeout=None, retries=2):
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
            "nozzle_temper": 25
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
                data = json.loads(msg.payload.decode('utf-8'))
                if isinstance(data, dict) and "print" in data:
                    result["data"] = data["print"]
                    status_received.set()
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                from bambu_cli import bambu; bambu.logger.debug(f"MQTT decode error: {e}")

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
                time.sleep(2 ** attempt)
        except (OSError, ssl.SSLError) as e:
            if attempt < retries:
                logger.warning(f"MQTT status attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2 ** attempt)
            else:
                logger.error(f"MQTT status error: {e}")

    return None


@mockable
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
                client.publish(f"device/{printer.serial}/request",
                               json.dumps({"info": {"sequence_id": get_sequence_id(), "command": "get_version"}}))
            else:
                logger.error(f"Connection failed: rc={rc}")
                received.set()

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode('utf-8'))
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
                time.sleep(2 ** attempt)
        except (OSError, ssl.SSLError):
            if attempt < retries:
                time.sleep(2 ** attempt)

    return None


@mockable
def monitor_status(args):
    """Subscribe to the printer's report topic and log updates until a terminal state is reached."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.utils import emit_json
    from bambu_cli.printer import get_printer
    printer = get_printer()
    logger.info("📡 Starting status monitor loop. Press Ctrl+C to stop.")
    if printer.simulation_mode:
        logger.info("🤖 [SIM] Simulated status: State=PREPARE, Progress=0%")
        time.sleep(0.5)
        logger.info("🤖 [SIM] Simulated status: State=RUNNING, Progress=50%")
        time.sleep(0.5)
        logger.info("🤖 [SIM] Simulated status: State=FINISH, Progress=100%")
        logger.info("🏁 Reached terminal state: FINISH")
        if bool(_namespace_get(args, "json", False)):
            emit_json({
                "status": "ok",
                "command": "status",
                "printer": {
                    "gcode_state": "FINISH",
                    "mc_percent": 100,
                }
            })
        return

    terminal_states = {"FINISH", "FAILED", "STOP", "IDLE"}
    received_terminal = threading.Event()
    json_mode = bool(_namespace_get(args, "json", False))
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
            data = json.loads(msg.payload.decode('utf-8'))
            if isinstance(data, dict) and "print" in data:
                p = data["print"]
                state = p.get("gcode_state", "UNKNOWN")
                pct = p.get("mc_percent", 0)
                
                if state != last_state[0] or pct != last_pct[0]:
                    if 'progress' not in userdata and not show_progress_bar:
                        userdata['progress'] = None
                    if 'progress' not in userdata:
                        try:
                            from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
                            progress = Progress(
                                TextColumn("[bold blue]Print Status"),
                                BarColumn(),
                                "[progress.percentage]{task.percentage:>3.1f}%",
                                "•",
                                TextColumn("{task.description}"),
                                "•",
                                TimeElapsedColumn(),
                                transient=True
                            )
                            progress.start()
                            userdata['progress'] = progress
                            userdata['task_id'] = progress.add_task(f"State: {state}", total=100, completed=pct)
                        except ImportError:
                            userdata['progress'] = None

                    if userdata.get('progress'):
                        userdata['progress'].update(userdata['task_id'], completed=pct, description=f"State: {state}")
                    else:
                        logger.info(f"⏳ Status: State={state}, Progress={pct}%")

                    last_state[0] = state
                    last_pct[0] = pct
                
                if state in terminal_states:
                    logger.info(f"🏁 Reached terminal state: {state}")
                    if bool(_namespace_get(args, "json", False)):
                        emit_json({
                            "status": "ok",
                            "command": "status",
                            "printer": p,
                        })
                    received_terminal.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            from bambu_cli import bambu; bambu.logger.debug(f"MQTT decode error: {e}")
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
        if userdata.get('progress'):
            try:
                userdata['progress'].stop()
            except Exception:
                pass
        try:
            client.loop_stop()
            client.disconnect()
        except:
            pass

import base64

_TRUSTED_CERT_FILE = None

# probe_cert_fingerprint is defined above

def _get_and_verify_cert_pem(host, port, expected_fingerprint, timeout=5):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
            from bambu_cli.config import fingerprint_sha256
            actual = fingerprint_sha256(der)
            if actual.lower() != expected_fingerprint.lower():
                raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {expected_fingerprint}, got {actual}")
            pem = "-----BEGIN CERTIFICATE-----\n"
            b64 = base64.b64encode(der).decode('ascii')
            for i in range(0, len(b64), 64):
                pem += b64[i:i+64] + "\n"
            pem += "-----END CERTIFICATE-----\n"
            return pem




@mockable
def execute_print_command(printer, payload, basename, dry_run=False):
    """Send the print payload via MQTT and monitor for errors."""
    from bambu_cli import bambu
    from bambu_cli.utils import record_error_detail
    from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_NETWORK_ERROR, EXIT_TIMEOUT, EXIT_PRINTER_ERROR
    
    if dry_run:
        logger.info(f"🔍 Dry Run: Checking if {basename} exists on printer...")
        try:
            with printer.get_ftp_client(timeout=5) as ftp:
                files = ftp.nlst('/model/')
                if basename in files or f"/model/{basename}" in files:
                    logger.info(f"   ✅ File {basename} found on printer.")
                else:
                    message = f"File {basename} was not found on printer. Upload it first."
                    logger.error(f"   ❌ File {basename} NOT found on printer. Upload it first.")
                    record_error_detail("print", EXIT_FILE_ERROR, message, failed_step="dry_run", file=basename, printed=False)
                    sys.exit(EXIT_FILE_ERROR)
            logger.info("   ✅ Printer reachable via MQTT (status check)...")
            if printer.status(timeout=5):
                logger.info("   ✅ MQTT connection verified.")
            else:
                message = "MQTT connection failed."
                logger.error(f"   ❌ {message}")
                record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=basename, printed=False)
                sys.exit(EXIT_NETWORK_ERROR)
            return
        except Exception as e:
            message = f"Dry run failed: {e}"
            logger.error(message)
            record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=basename, printed=False)
            sys.exit(EXIT_NETWORK_ERROR)

    if printer.simulation_mode:
        from bambu_cli.protocols.ftps import _SIM_FTP_FILES
        if basename not in _SIM_FTP_FILES:
            message = f"File {basename} not found on simulated printer. Upload it first."
            logger.error(message)
            record_error_detail("print", EXIT_FILE_ERROR, message, failed_step="print", file=basename, printed=False)
            sys.exit(EXIT_FILE_ERROR)
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
            data = json.loads(msg.payload.decode('utf-8'))
            if "print" in data:
                p = data["print"]
                pe = p.get("print_error", 0)
                if pe and pe != 0:
                    print_error[0] = pe
                    command_accepted.set()
                if p.get("command") == "project_file":
                    command_accepted.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            from bambu_cli import bambu; bambu.logger.debug(f"MQTT decode error: {e}")
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
                sys.exit(EXIT_TIMEOUT)
        finally:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
    except SystemExit:
        raise
    except Exception as e:
        message = f"Error: {e}"
        logger.error(message)
        record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="print", file=basename, printed=False)
        sys.exit(EXIT_NETWORK_ERROR)

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
            sys.exit(EXIT_FILE_ERROR)
        record_error_detail(
            "print",
            EXIT_PRINTER_ERROR,
            message,
            failed_step="print",
            file=basename,
            printer_error_code=print_error[0],
            printed=False,
        )
        sys.exit(EXIT_PRINTER_ERROR)
    else:
        logger.info(f"🖨️  Print started: {basename}")
