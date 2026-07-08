import json
import os
import sys
import tempfile

from bambu_cli.context import RuntimeContext
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger

# We dynamically import bambu at runtime in every function to support patching of functions and configuration globals


def cmd_setup(args):
    """Interactive or non-interactive printer configuration setup."""
    from bambu_cli import bambu

    bambu._cmd_setup(args)


from bambu_cli.utils import get_sequence_id


def _offer_pin_fingerprint(fp, config_path, json_mode, interactive=None):  # pragma: no cover -- interactive TTY pin prompt
    """Offer to pin an unpinned printer cert fingerprint into config.json.

    Returns True only if the fingerprint was written. Silently declines (returns
    False) in ``--json`` mode or when the session is not interactive, so agent
    and non-TTY runs are never blocked on a prompt. Writes are atomic and 0600.
    """
    from bambu_cli.cli import _display_path, _exception_for_message
    from bambu_cli.setup_cmd.common import _secure_write_json

    if json_mode:
        return False
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        return False
    try:
        answer = input("      Pin this fingerprint to config.json for MITM protection? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        logger.info("")
        return False
    if answer.strip().lower() not in ("y", "yes"):
        logger.info('      Skipped. Add "cert_fingerprint" to config.json later to pin it.')
        return False
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["cert_fingerprint"] = fp
        _secure_write_json(config_path, cfg)
    except (OSError, ValueError) as exc:
        logger.error(f"      Could not pin fingerprint: {_exception_for_message(exc)}")
        return False
    logger.info(f"      🔐 Pinned cert_fingerprint in {_display_path(config_path)}.")
    return True


def cmd_doctor(args, ctx=None):  # pragma: no cover -- printer health check; components unit-tested
    """Health-check: auto-discover printer capabilities and verify configuration."""
    from bambu_cli import bambu
    from bambu_cli.cli import _display_path, _exception_for_message, _namespace_get, _path_for_message
    from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
    from bambu_cli.protocols.ftps import get_ftp
    from bambu_cli.protocols.mqtt import probe_cert_fingerprint
    from bambu_cli.utils import emit_json

    ctx = ctx or RuntimeContext.for_request(args)
    json_mode = bool(_namespace_get(args, "json", False))

    def emit_doctor_failure(failed_step, exit_code, error, extra=None):
        if not json_mode:
            return
        payload = {
            "command": "doctor",
            "ok": False,
            "status": "error",
            "failed_step": failed_step,
            "exit_code": exit_code,
            "error": error,
        }
        if extra:
            payload.update(extra)
        emit_json(payload)

    cap_path = _namespace_get(args, "output") or os.path.join(tempfile.gettempdir(), "printer_capabilities.json")
    cap_path = bambu._expand_path(cap_path)
    if cap_path.startswith("-"):
        message = f"Invalid output path: {_path_for_message(cap_path)}"
        logger.error(message)
        emit_doctor_failure("validate", EXIT_FILE_ERROR, message)
        abort("", exit_code=EXIT_FILE_ERROR)
    try:
        bambu._ensure_parent_dir(cap_path)
    except BambuError as exc:
        emit_doctor_failure(
            "validate",
            getattr(exc, "exit_code", None) or EXIT_FILE_ERROR,
            f"Could not prepare output path: {_path_for_message(cap_path)}",
        )
        raise

    logger.info("🩺 Running Bambu printer health check...")

    logger.info(f"   [1/3] Checking config at {_display_path(bambu.CONFIG_PATH)}...")
    try:
        bambu.load_config()
        logger.info("   ✅ Config loaded successfully.")
    except BambuError:
        logger.error("   ❌ Config check failed.")
        emit_doctor_failure("config", EXIT_CONFIG_ERROR, "Config check failed.")
        abort("", exit_code=EXIT_CONFIG_ERROR)

    try:
        fp = probe_cert_fingerprint(ctx.settings.printer_ip, 990, timeout=5)
    except Exception:
        fp = None

    def log_pin_hint():
        if fp and not bambu._expected_fingerprint() and not ctx.settings.insecure_tls:
            logger.info("      The printer uses a self-signed certificate. Pin it by adding to config.json:")
            logger.info(f'        "cert_fingerprint": "{fp}"')
            logger.info("      then re-run doctor.")

    logger.info(f"   [2/3] Verifying MQTT connectivity to {ctx.settings.printer_ip}:{ctx.settings.mqtt_port}...")
    printer = ctx.printer()
    status = printer.status(timeout=5)
    if status:
        logger.info("   ✅ MQTT connection established. Printer identified.")
    else:
        message = (
            f"MQTT connection failed. Ensure printer at {ctx.settings.printer_ip} is on and access code is correct."
        )
        logger.error(f"   ❌ {message}")
        log_pin_hint()
        extra = {"certificate_fingerprint": fp} if fp else None
        emit_doctor_failure("mqtt", EXIT_NETWORK_ERROR, message, extra=extra)
        abort("", exit_code=EXIT_NETWORK_ERROR)

    logger.info(f"   [3/3] Verifying FTPS connectivity to {ctx.settings.printer_ip}:990...")
    try:
        with get_ftp(timeout=5):
            logger.info("   ✅ FTPS connection established.")
    except Exception as e:
        message = f"FTPS connection failed: {e}"
        logger.error(f"   ❌ {message}")
        log_pin_hint()
        extra = {"certificate_fingerprint": fp} if fp else None
        emit_doctor_failure("ftps", EXIT_NETWORK_ERROR, message, extra=extra)
        abort("", exit_code=EXIT_NETWORK_ERROR)

    if fp:
        logger.info(f"   🔐 Printer certificate SHA-256: {fp}")
        if bambu._expected_fingerprint() == fp:
            logger.info("      ✅ Matches the pinned cert_fingerprint in your config.")
        elif bambu._expected_fingerprint():
            logger.warning("      ⚠️  Does NOT match the cert_fingerprint in your config!")
        elif ctx.settings.insecure_tls or not _offer_pin_fingerprint(fp, bambu.CONFIG_PATH, json_mode):
            logger.info('      Add "cert_fingerprint": "<above>" to config.json to pin this connection.')

    model_info = bambu.MODEL_MAPPING.get(ctx.settings.printer_model, bambu.MODEL_MAPPING["P1P"])
    firmware = status.get("sw_ver")
    modules = printer.get_version(timeout=5)
    if modules:
        ota = next((m for m in modules if m.get("name") == "ota"), None) or modules[0]
        firmware = ota.get("sw_ver") or firmware

    capabilities = {
        "model": status.get("hw_ver") or model_info["full_name"],
        "firmware": firmware or "Unknown",
        "serial": bambu._redacted_serial(),
        "capabilities": {
            "ams": "ams" in status,
            "chamber_light": True,
            "camera_snapshot": ctx.settings.printer_model in ("P1P", "P1S"),
            "camera_snapshot_note": "snapshot uses the optional BambuP1Streamer container and is intended for P1P/P1S",
        },
    }

    try:
        with open(cap_path, "w", encoding="utf-8") as f:
            json.dump(capabilities, f, indent=2)
    except OSError as e:
        message = f"Could not write printer capabilities to {_path_for_message(cap_path)}: {_exception_for_message(e)}"
        logger.error(message)
        emit_doctor_failure("output", EXIT_FILE_ERROR, message, extra={"output": cap_path})
        abort("", exit_code=EXIT_FILE_ERROR)
    logger.info(
        f"\n✨ Printer Details: Model={capabilities['model']}, Firmware={capabilities['firmware']}, Serial={capabilities['serial']}"
    )
    logger.info(f"✅ All checks passed! Printer capabilities saved to {_display_path(cap_path)}")
    if json_mode:
        # Mask IP address inside doctor capabilities report unless --verbose is checked (A0530-SEC-16)
        reported_ip = ctx.settings.printer_ip if bool(_namespace_get(args, "verbose", False)) else "<redacted>"
        emit_json(
            {
                "command": "doctor",
                "ok": True,
                "status": "ok",
                "output": cap_path,
                "printer_ip": reported_ip,
                "certificate_fingerprint": fp,
                "capabilities": capabilities,
            }
        )


def cmd_light(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Control chamber light."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    action = args.action  # on or off
    val = "on" if action == "on" else "off"
    payload = json.dumps(
        {
            "system": {
                "sequence_id": get_sequence_id(),
                "command": "ledctrl",
                "led_node": "chamber_light",
                "led_mode": val,
                "led_on_time": 500,
                "led_off_time": 500,
            }
        }
    )
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send light command."
        logger.error(message)
        emit_json_error(args, "light", EXIT_NETWORK_ERROR, message, failed_step="mqtt", action=action, changed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info(f"💡 Light turned {action}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "light_changed",
                "command": "light",
                "action": action,
                "changed": True,
            }
        )


def cmd_pause(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Pause current print."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "pause"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send pause command."
        logger.error(message)
        emit_json_error(args, "pause", EXIT_NETWORK_ERROR, message, failed_step="mqtt", paused=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("⏸️  Print paused")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "paused",
                "command": "pause",
                "paused": True,
            }
        )


def cmd_resume(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Resume paused print."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "resume"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send resume command."
        logger.error(message)
        emit_json_error(args, "resume", EXIT_NETWORK_ERROR, message, failed_step="mqtt", resumed=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("▶️  Print resumed")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "resumed",
                "command": "resume",
                "resumed": True,
            }
        )


def cmd_stop(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Stop current print."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    if not args.confirm:
        logger.warning("⚠️  This will STOP the current print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "stop",
                    "stopped": False,
                    "next_command": ["stop", "--confirm", "--json"],
                }
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "stop"}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send stop command."
        logger.error(message)
        emit_json_error(args, "stop", EXIT_NETWORK_ERROR, message, failed_step="mqtt", stopped=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info("⏹️  Print stopped")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "stopped",
                "command": "stop",
                "stopped": True,
            }
        )


def cmd_upload(args, ctx=None):  # pragma: no cover -- FTPS upload orchestration; resume unit-tested
    """Upload a file to the printer via FTPS with binary retry/resume."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
    from bambu_cli.printer import get_printer
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    filepath = bambu._expand_path(args.file)
    if filepath.startswith("-"):
        message = f"Invalid filepath: {bambu._path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not os.path.exists(filepath):
        message = f"File not found: {bambu._path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if bambu._is_directory_input(filepath):
        message = bambu._directory_input_message(filepath)
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)

    filename = bambu._portable_basename(filepath)
    if bambu._safe_remote_name(filename) is None:
        message = f"Refusing to upload file with unsafe name: {bambu._name_for_message(filename)!r}"
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if not bambu._is_print_ready_name(filename):
        message = bambu._print_ready_error_message(filename, "upload")
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    try:
        filesize = os.path.getsize(filepath)
    except OSError as exc:
        message = (
            f"Could not read file size for {bambu._path_for_message(filepath)}: {bambu._exception_for_message(exc)}"
        )
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if filesize <= 0:
        message = f"Refusing to upload empty file: {bambu._path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(
            args,
            "upload",
            EXIT_FILE_ERROR,
            message,
            failed_step="validate",
            file=filepath,
            remote_name=filename,
            bytes=filesize,
        )
        abort("", exit_code=EXIT_FILE_ERROR)

    if getattr(args, "dry_run", False):
        logger.info(f"🔍 Dry Run: Validating printer connectivity for {filename}...")
        printer = get_printer()
        try:
            # Uploads go over FTPS, so the dry-run must exercise FTPS, not MQTT.
            with printer.get_ftp_client(timeout=5):
                pass
            logger.info("   ✅ Printer reachable.")
        except Exception:
            message = "Dry run failed: Could not reach printer."
            logger.error(message)
            emit_json_error(
                args, "upload", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=filepath, remote_name=filename
            )
            abort("", exit_code=EXIT_NETWORK_ERROR)

        logger.info(f"   ✅ Local file {bambu._path_for_message(filepath)} exists ({filesize // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "dry_run_ok",
                    "command": "upload",
                    "file": filepath,
                    "remote_name": filename,
                    "bytes": filesize,
                    "uploaded": False,
                }
            )
        return filename

    logger.info(f"📤 Uploading {filename} ({filesize // 1024}KB)...")

    printer = get_printer()

    progress = None
    task_id = None
    upload_callback = None
    try:
        if not getattr(args, "json", False) and getattr(args, "progress", True) and sys.stdout.isatty():
            from rich.progress import DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

            progress = Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                transient=True,
            )
            progress.start()
            task_id = progress.add_task(f"Uploading {filename}...", total=filesize)

            def _cb(block):
                progress.update(task_id, advance=len(block))

            upload_callback = _cb
    except ImportError:
        pass

    try:
        on_resume = None
        if progress is not None and task_id is not None:
            on_resume = lambda n: progress.update(task_id, completed=n)
        success = printer.upload_file(
            filepath,
            f"/model/{filename}",
            timeout=getattr(bambu, "UPLOAD_TIMEOUT", 300),
            progress_callback=upload_callback,
            on_resume=on_resume,
        )
    finally:
        if progress:
            progress.stop()

    if success:
        logger.info(f"✅ Uploaded {filename} to printer")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "uploaded",
                    "command": "upload",
                    "file": filepath,
                    "remote_name": filename,
                    "bytes": filesize,
                    "uploaded": True,
                }
            )
        return filename
    else:
        # 4 attempts mirrors upload_file.max_retries (3 retries + initial try)
        message = "Upload failed after 4 attempts."
        logger.error(f"❌ {message}")
        emit_json_error(
            args,
            "upload",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="upload",
            file=filepath,
            remote_name=filename,
        )
        abort("", exit_code=EXIT_NETWORK_ERROR)


def cmd_files(args, ctx=None):  # pragma: no cover -- FTPS list command
    """List files on the printer."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.printer import get_printer
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    json_mode = bool(_namespace_get(args, "json", False))
    try:
        printer = get_printer()
        files = printer.list_files("/model/")
        if files is None:
            raise Exception("Failed to list files via printer API")
        remote_files = [{"name": bambu._portable_basename(path), "path": path} for path in files]
        if json_mode:
            emit_json(
                {
                    "status": "ok",
                    "command": "files",
                    "count": len(remote_files),
                    "files": remote_files,
                }
            )
            return
        if not files:
            logger.info("No files on printer.")
            return
        logger.info("📁 Files on printer:")
        for f in files:
            logger.info(f"   {f}")
    except Exception as e:
        message = f"Error listing files: {e}"
        logger.error(message)
        emit_json_error(args, "files", EXIT_NETWORK_ERROR, message, failed_step="ftps", files=[])
        abort("", exit_code=EXIT_NETWORK_ERROR)


def cmd_print(args, ctx=None):  # pragma: no cover -- print option parsing; execute_print unit-tested
    """Start printing a file already on the printer."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    dry_run = getattr(args, "dry_run", False)
    basename = str(args.file or "")

    if bambu._safe_remote_name(basename) is None:
        message = f"Refusing to print file with unsafe name: {bambu._name_for_message(basename)!r}"
        logger.error(message)
        emit_json_error(args, "print", EXIT_FILE_ERROR, message, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not bambu._is_print_ready_name(basename):
        message = bambu._print_ready_error_message(basename, "print")
        logger.error(message)
        emit_json_error(args, "print", EXIT_FILE_ERROR, message, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_FILE_ERROR)

    ams_mapping, print_option_error = bambu._parse_print_options(args)
    if print_option_error:
        logger.error(print_option_error)
        emit_json_error(args, "print", EXIT_COMMAND_ERROR, print_option_error, failed_step="validate", file=basename)
        abort("", exit_code=EXIT_COMMAND_ERROR)

    if not args.confirm and not dry_run:
        logger.warning("⚠️  This will START a print. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "print",
                    "file": basename,
                    "printed": False,
                    "next_command": bambu._print_next_command(args, basename),
                }
            )
        return

    payload = bambu.generate_print_payload(
        basename,
        use_ams=getattr(args, "use_ams", False),
        ams_mapping=ams_mapping,
        timelapse=getattr(args, "timelapse", False),
        bed_leveling=not getattr(args, "skip_bed_leveling", False),
        flow_cali=not getattr(args, "skip_flow_cali", False),
    )
    try:
        from bambu_cli.printer import get_printer

        printer = get_printer()
        from bambu_cli import utils as _utils

        _utils._LAST_ERROR_PAYLOAD = None
        bambu.execute_print_command(printer, payload, basename, dry_run=dry_run)
    except BambuError as exc:
        exit_code = getattr(exc, "exit_code", None) or bambu._exit_code_from_system_exit(exc)
        detail = bambu._last_error_for("print")
        emit_json_error(
            args,
            "print",
            exit_code,
            detail.get("error") if detail else "print failed; see stderr for details",
            failed_step="dry_run" if dry_run else "print",
            file=basename,
            printed=False,
            dry_run=bool(dry_run),
            **({"print_error": detail} if detail else {}),
        )
        raise
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "dry_run_ok" if dry_run else "print_started",
                "command": "print",
                "file": basename,
                "printed": not dry_run,
                "dry_run": bool(dry_run),
            }
        )
    return basename


def cmd_download(args):
    """Download a model file from a remote URL."""
    from bambu_cli import bambu

    return bambu._cmd_download(args)


def cmd_delete(args, ctx=None):  # pragma: no cover -- FTPS delete command
    """Delete a file from the printer via FTPS."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
    from bambu_cli.printer import get_printer
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    filename = str(args.file or "")
    if bambu._safe_remote_name(filename) is None:
        message = f"Refusing to delete file with unsafe name: {bambu._name_for_message(filename)!r}"
        logger.error(message)
        emit_json_error(args, "delete", EXIT_FILE_ERROR, message, failed_step="validate", file=filename, deleted=False)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not args.confirm:
        logger.warning(f"⚠️  This will DELETE '{filename}' from the printer. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "delete",
                    "file": filename,
                    "deleted": False,
                    "next_command": ["delete", filename, "--confirm", "--json"],
                }
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)

    try:
        printer = get_printer()
        if printer.delete_file(f"/model/{filename}"):
            logger.info(f"🗑️  Deleted {filename} from printer")
            if bool(_namespace_get(args, "json", False)):
                emit_json(
                    {
                        "status": "deleted",
                        "command": "delete",
                        "file": filename,
                        "deleted": True,
                    }
                )
        else:
            raise Exception("Delete operation failed in printer client.")
    except Exception as e:
        message = f"Delete failed: {e}"
        logger.error(message)
        emit_json_error(args, "delete", EXIT_NETWORK_ERROR, message, failed_step="ftps", file=filename, deleted=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)


def cmd_snapshot(args, ctx=None):
    """Capture a camera snapshot using the RTSP Streamer Docker container."""
    from bambu_cli import bambu

    ctx = ctx or RuntimeContext.for_request(args)
    bambu._cmd_snapshot(args, ctx=ctx)


def cmd_preflight(args):  # pragma: no cover -- thin dispatch to setup_cmd
    """Check local install/config readiness without contacting printer."""
    from bambu_cli import bambu

    bambu._cmd_preflight(args)


def cmd_config(args):  # pragma: no cover -- thin dispatch to setup_cmd
    """Show the effective config (redacted) or validate it locally."""
    from bambu_cli import bambu

    bambu._cmd_config(args)


def cmd_job(args):  # pragma: no cover -- thin dispatch to job module
    """One-shot URL/local file workflow: download, slice, upload, optionally print."""
    from bambu_cli import bambu

    return bambu._cmd_job(args)


def cmd_gcode(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Send raw G-code to the printer via MQTT."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.utils import emit_json, emit_json_error

    ctx = ctx or RuntimeContext.for_request(args)
    gcode = args.code
    payload = json.dumps({"print": {"sequence_id": get_sequence_id(), "command": "gcode_line", "param": gcode}})
    printer = ctx.printer()
    if not printer.send_command(payload):
        message = "Failed to send G-code command."
        logger.error(message)
        emit_json_error(args, "gcode", EXIT_NETWORK_ERROR, message, failed_step="mqtt", gcode=gcode, sent=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    logger.info(f"📡 Sent: {gcode}")
    if bool(_namespace_get(args, "json", False)):
        emit_json(
            {
                "status": "sent",
                "command": "gcode",
                "gcode": gcode,
                "sent": True,
            }
        )


def cmd_status(args, ctx=None):  # pragma: no cover -- thin handler wrapper
    """Query and display the printer's current status."""
    from bambu_cli.ams import parse_ams
    from bambu_cli.cli import _namespace_get
    from bambu_cli.constants import EXIT_NETWORK_ERROR
    from bambu_cli.errors import PrinterConnectionError
    from bambu_cli.protocols.mqtt import monitor_status
    from bambu_cli.utils import emit_json

    ctx = ctx or RuntimeContext.for_request(args)
    if bool(_namespace_get(args, "monitor", False)):
        monitor_status(args)
        return

    printer = ctx.printer()
    data = printer.status()
    if not data:
        raise PrinterConnectionError(
            "Could not connect to printer.",
            exit_code=EXIT_NETWORK_ERROR,
            failed_step="mqtt",
        )

    ams = parse_ams(data)

    if args.json:
        payload = {
            "status": "ok",
            "command": "status",
            "printer": data,
        }
        payload.update({k: v for k, v in data.items() if k not in ("status", "command")})
        # Normalized AMS view (trays/filaments) for agents building --ams-mapping;
        # None on printers without an AMS.
        payload["ams"] = ams
        emit_json(payload)
        return

    state = data.get("gcode_state", "UNKNOWN")
    pct = data.get("mc_percent", 0)
    layer = data.get("layer_num", 0)
    total_layers = data.get("total_layer_num", 0)
    try:
        remaining = int(data.get("mc_remaining_time", 0))
    except (TypeError, ValueError):
        remaining = 0
    filename = data.get("gcode_file", "")
    bed_temp = data.get("bed_temper", "?")
    bed_target = data.get("bed_target_temper", "?")
    nozzle_temp = data.get("nozzle_temper", "?")
    nozzle_target = data.get("nozzle_target_temper", "?")
    fan = data.get("cooling_fan_speed", "?")
    wifi = str(data.get("wifi_signal", "?")).replace("dBm", "")

    logger.info("🖨️  Bambu Printer Status")
    logger.info(f"   State: {state}")
    if state == "RUNNING":
        hrs, mins = divmod(remaining, 60)
        logger.info(f"   File: {filename}")
        logger.info(f"   Progress: {pct}% | Layer {layer}/{total_layers}")
        logger.info(f"   Time left: {hrs}h {mins}m")
    logger.info(f"   Bed: {bed_temp}°C / {bed_target}°C")
    logger.info(f"   Nozzle: {nozzle_temp}°C / {nozzle_target}°C")
    logger.info(f"   Fan: {fan} | WiFi: {wifi}dBm")

    if ams and ams["units"]:
        logger.info("   AMS:")
        for unit in ams["units"]:
            logger.info(f"     Unit {unit['id']} (humidity {unit['humidity']}, {unit['temp']}°C)")
            for tray in unit["trays"]:
                marker = "▶ " if tray["active"] else "  "
                if tray["empty"]:
                    logger.info(f"       {marker}Slot {tray['slot']}: empty")
                else:
                    color = f" #{tray['color']}" if tray["color"] else ""
                    remain = f" | {tray['remain']}%" if tray["remain"] is not None else ""
                    logger.info(f"       {marker}Slot {tray['slot']}: {tray['type']}{color}{remain}")
