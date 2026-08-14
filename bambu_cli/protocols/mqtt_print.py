"""MQTT print-start: publish project_file and wait for ack / error."""

from __future__ import annotations

import json
import threading

from bambu_cli.config import get_command_timeout
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.protocols.mqtt_cmd import _client_factory, _connect, status_is_complete


def _printer_error_hex(code: object) -> str | None:
    """Render a printer error code as the hex form Bambu documents (e.g. 0x0500C010)."""
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

    _factory = _client_factory(client_factory)

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

    client = _factory(printer, "bambu_print")

    print_error = [None]
    reject_reason: list[str | None] = [None]
    command_accepted = threading.Event()
    connect_failed = [False]
    published = [False]
    ack_seen = [False]

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"device/{printer.serial}/report")
            if not published[0]:
                published[0] = True
                client.publish(f"device/{printer.serial}/request", payload)
        else:
            logger.error(f"Connection failed: rc={rc}")
            connect_failed[0] = True
            command_accepted.set()

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if "print" in data:
                p = data["print"]
                is_ack = p.get("command") == "project_file"
                if is_ack:
                    ack_seen[0] = True
                pe = p.get("print_error", 0)
                stale_snapshot = status_is_complete(p) and not ack_seen[0] and not is_ack
                if pe and pe != 0 and not stale_snapshot:
                    print_error[0] = pe
                    command_accepted.set()
                if is_ack:
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

    base_timeout = command_timeout if command_timeout is not None else get_command_timeout()
    print_ack_timeout = base_timeout + 5

    try:
        _connect(printer, client)
        client.loop_start()
        accepted = command_accepted.wait(print_ack_timeout)
        if not accepted:
            message = f"Timed out waiting for printer to acknowledge print start for {basename}"
            logger.error(message)
            record_error_detail("print", EXIT_TIMEOUT, message, failed_step="print", file=basename, printed=False)
            abort("", exit_code=EXIT_TIMEOUT)
    except BambuError:
        raise
    except Exception as e:
        message = f"Error: {e}"
        logger.error(message)
        record_error_detail("print", EXIT_NETWORK_ERROR, message, failed_step="print", file=basename, printed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

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
