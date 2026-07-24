"""Doctor health-check and optional cert-fingerprint pinning."""

import json
import os
import sys
import tempfile

from bambu_cli.cli import (
    _display_path,
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
)
from bambu_cli.config import CONFIG_PATH, MODEL_MAPPING, _expected_fingerprint, get_network_timeout, load_config
from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _ensure_parent_dir, _redacted_serial


def _offer_pin_fingerprint(
    fp, config_path, json_mode, interactive=None
):  # pragma: no cover -- interactive TTY pin prompt
    """Offer to pin an unpinned printer cert fingerprint into config.json.

    Returns True only if the fingerprint was written. Silently declines (returns
    False) in ``--json`` mode or when the session is not interactive, so agent
    and non-TTY runs are never blocked on a prompt. Writes are atomic and 0600.
    """
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


def cmd_doctor(args, ctx=None):
    """Health-check: auto-discover printer capabilities and verify configuration."""
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

    cap_path = _namespace_get(args, "output")
    if not cap_path:
        fd, cap_path = tempfile.mkstemp(prefix="printer_capabilities_", suffix=".json")
        os.close(fd)
    cap_path = _expand_path(cap_path)
    if cap_path.startswith("-"):
        message = f"Invalid output path: {_path_for_message(cap_path)}"
        logger.error(message)
        emit_doctor_failure("validate", EXIT_FILE_ERROR, message)
        abort("", exit_code=EXIT_FILE_ERROR)
    try:
        _ensure_parent_dir(cap_path)
    except BambuError as exc:
        emit_doctor_failure(
            "validate",
            getattr(exc, "exit_code", None) or EXIT_FILE_ERROR,
            f"Could not prepare output path: {_path_for_message(cap_path)}",
        )
        raise

    logger.info("🩺 Running Bambu printer health check...")

    logger.info(f"   [1/3] Checking config at {_display_path(CONFIG_PATH)}...")
    try:
        load_config()
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
        if fp and not _expected_fingerprint() and not ctx.settings.insecure_tls:
            logger.info("      The printer uses a self-signed certificate. Pin it by adding to config.json:")
            logger.info(f'        "cert_fingerprint": "{fp}"')
            logger.info("      then re-run doctor.")

    logger.info(f"   [2/3] Verifying MQTT connectivity to {ctx.settings.printer_ip}:{ctx.settings.mqtt_port}...")
    printer = ctx.printer()
    net_timeout = get_network_timeout(args)
    status = printer.status(timeout=net_timeout, retries=0)
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
        with get_ftp(timeout=net_timeout):
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
        if _expected_fingerprint() == fp:
            logger.info("      ✅ Matches the pinned cert_fingerprint in your config.")
        elif _expected_fingerprint():
            logger.warning("      ⚠️  Does NOT match the cert_fingerprint in your config!")
        elif ctx.settings.insecure_tls or not _offer_pin_fingerprint(fp, CONFIG_PATH, json_mode):
            logger.info('      Add "cert_fingerprint": "<above>" to config.json to pin this connection.')

    model_info = MODEL_MAPPING.get(ctx.settings.printer_model, MODEL_MAPPING["P1P"])
    firmware = status.get("sw_ver")
    modules = printer.get_version(timeout=net_timeout)
    if modules:
        ota = next((m for m in modules if m.get("name") == "ota"), None) or modules[0]
        firmware = ota.get("sw_ver") or firmware

    capabilities = {
        "model": status.get("hw_ver") or model_info["full_name"],
        "firmware": firmware or "Unknown",
        "serial": _redacted_serial(),
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
