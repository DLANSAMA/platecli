"""The `setup` command: guided/non-interactive config creation and mDNS discovery."""

import os
import re
import socket
import sys
import threading

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.argutils import setup_args_provided as _setup_args_provided
from bambu_cli.config import MODEL_MAPPING, _access_code_value_problem
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR, EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.paths import display_path as _display_path
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.setup_cmd.common import (
    _build_setup_config,
    _config_path,
    _looks_like_placeholder,
    _normalize_model,
    _normalize_nozzle,
    _prompt_access_code_file_path,
    _prompt_secret,
    _prompt_text,
    _setup_file_error,
    _setup_json_error,
    _setup_path_details,
    _setup_summary,
    _validate_setup_access_code_file,
    _write_setup_config,
)
from bambu_cli.utils import emit_json


def _service_info_address(info):
    """Extract the first usable IP address from zeroconf service info."""
    parsed_addresses = getattr(info, "parsed_addresses", None)
    if callable(parsed_addresses):
        try:
            addresses = list(parsed_addresses())
        except (TypeError, ValueError):
            addresses = []
        if addresses:
            return addresses[0]

    for raw in getattr(info, "addresses", []) or []:
        try:
            if len(raw) == 4:
                return socket.inet_ntoa(raw)
            if len(raw) == 16:
                return socket.inet_ntop(socket.AF_INET6, raw)
        except (OSError, ValueError):
            continue

    raise ValueError("service did not advertise a usable IP address")


def _parse_mdns_printer_identity(name):
    """Return (serial, model) from a Bambu mDNS service name."""
    match = re.search(r"BBLP-([^._]+)", name, re.IGNORECASE)
    service_id = match.group(1).upper() if match else ""
    detected_model = "P1P"
    serial = service_id or "YOUR_SERIAL"

    for model in sorted(MODEL_MAPPING, key=len, reverse=True):
        prefix = f"{model}-"
        if service_id == model:
            detected_model = model
            break
        if service_id.startswith(prefix):
            detected_model = model
            serial = service_id[len(prefix) :] or "YOUR_SERIAL"
            break

    return serial, detected_model


def _cmd_setup_noninteractive(args):
    from bambu_cli.config import _DEFAULT_ORCA, _DEFAULT_PROFILES

    ip = _namespace_get(args, "printer_ip")
    serial = _namespace_get(args, "serial")
    access_code = _namespace_get(args, "access_code")
    access_code_env = _namespace_get(args, "access_code_env")
    access_code_file = _namespace_get(args, "access_code_file")
    expanded_access_code_file = _validate_setup_access_code_file(args, access_code_file)

    if access_code and access_code_env:
        message = "Use only one of --access-code or --access-code-env."
        logger.error(message)
        _setup_json_error(args, message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if access_code_env:
        access_code = os.environ.get(access_code_env)
        if not access_code:
            message = f"Environment variable {access_code_env} is not set or empty."
            logger.error(message)
            _setup_json_error(args, message, access_code_env=access_code_env)
            abort("", exit_code=EXIT_CONFIG_ERROR)

    missing = []
    if not ip:
        missing.append("--printer-ip")
    if not serial:
        missing.append("--serial")
    if not access_code and not access_code_file:
        missing.append("--access-code, --access-code-env, or --access-code-file")
    if missing:
        message = "Non-interactive setup is missing required values: " + ", ".join(missing)
        logger.error(message)
        _setup_json_error(args, message, missing=missing)
        abort("", exit_code=EXIT_CONFIG_ERROR)

    if access_code_file and not access_code:
        if not os.path.exists(expanded_access_code_file):
            message = f"Access code file not found: {_display_path(expanded_access_code_file)}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            abort("", exit_code=EXIT_CONFIG_ERROR)
        try:
            with open(expanded_access_code_file, encoding="utf-8-sig") as f:
                access_code_problem = _access_code_value_problem(f.read().strip())
        except OSError as exc:
            reason = getattr(exc, "strerror", None) or str(exc)
            message = f"Access code file could not be read: {reason}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            abort("", exit_code=EXIT_CONFIG_ERROR)
        if access_code_problem:
            logger.error(access_code_problem)
            _setup_json_error(
                args, access_code_problem, **_setup_path_details(access_code_file=expanded_access_code_file)
            )
            abort("", exit_code=EXIT_CONFIG_ERROR)

    placeholder_errors = []
    if _looks_like_placeholder(ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP", "USER_PROVIDED_IP"}):
        placeholder_errors.append("--printer-ip")
    if _looks_like_placeholder(
        serial, {"UNKNOWN", "YOUR_SERIAL", "YOUR_PRINTER_SERIAL", "USER_PROVIDED_SERIAL", "<REDACTED>"}
    ):
        placeholder_errors.append("--serial")
    if access_code and _looks_like_placeholder(
        access_code, {"ACCESS_CODE", "YOUR_ACCESS_CODE", "USER_PROVIDED_ACCESS_CODE"}
    ):
        placeholder_errors.append("--access-code/--access-code-env")
    if placeholder_errors:
        message = (
            "Non-interactive setup received placeholder values for: "
            + ", ".join(placeholder_errors)
            + ". Replace placeholders with real printer details before running setup."
        )
        logger.error(message)
        _setup_json_error(args, message, placeholders=placeholder_errors)
        abort("", exit_code=EXIT_CONFIG_ERROR)

    try:
        config = _build_setup_config(
            ip=ip,
            serial=serial,
            model=_normalize_model(_namespace_get(args, "model"), "P1P"),
            nozzle=_normalize_nozzle(_namespace_get(args, "nozzle")),
            access_code=access_code,
            access_code_file=access_code_file,
            orca_slicer=_namespace_get(args, "orca_slicer") or _DEFAULT_ORCA,
            profiles_dir=_namespace_get(args, "profiles_dir") or _DEFAULT_PROFILES,
            cert_fingerprint=_namespace_get(args, "cert_fingerprint"),
            insecure_tls=bool(_namespace_get(args, "insecure_tls", False)),
        )
    except ValueError as exc:
        message = str(exc)
        logger.error(message)
        _setup_json_error(args, message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    # Refuse to silently clobber an existing secret file when both --access-code
    # and --access-code-file are given (the existence check above is skipped in
    # that case). Fails closed on an unreadable existing file too.
    if access_code and access_code_file and not _namespace_get(args, "force", False):
        conflict = _access_code_file_overwrite_conflict(expanded_access_code_file, access_code)
        if conflict:
            message = f"{conflict} (pass --force to overwrite, or point --access-code-file at a new path)."
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        _write_setup_config(config, access_code_file_secret=access_code if access_code_file else None)
    except OSError as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        message = f"Could not write setup files: {reason}"
        logger.error(message)
        _setup_file_error(
            args,
            message,
            **_setup_path_details(config_path=_config_path(), access_code_file=expanded_access_code_file),
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _access_code_file_overwrite_conflict(expanded_access_code_file, new_code):
    """Return a reason string if writing ``new_code`` would clobber an existing
    secret file, else None.

    Secret-file writes are backup=False, so an existing credential (possibly
    shared with another printer profile or script) would be unrecoverable.
    Fails **closed**: an existing file whose contents cannot be read is treated
    as a conflict too — we must not overwrite what we cannot confirm is identical.
    """
    if not os.path.exists(expanded_access_code_file):
        return None
    try:
        with open(expanded_access_code_file, encoding="utf-8-sig") as f:
            existing_secret = f.read().strip()
    except OSError:
        return (
            f"Access code file already exists and could not be read: "
            f"{_display_path(expanded_access_code_file)}. Refusing to overwrite it."
        )
    if existing_secret != (new_code or "").strip():
        return (
            f"Access code file already exists with different contents: "
            f"{_display_path(expanded_access_code_file)}. Refusing to overwrite it."
        )
    return None


def _confirm_interactive_access_code_file_overwrite(args, expanded_access_code_file, new_code):
    """Interactive guard: refuse to silently clobber a differing/unreadable
    existing secret file. Prompts for explicit confirmation, defaulting to NO.

    The interactive wizard's default target is a fixed path, so re-running it can
    land on a secret file shared with another printer profile. Mirrors the
    non-interactive --force gate with a confirmation prompt instead of a flag.
    """
    conflict = _access_code_file_overwrite_conflict(expanded_access_code_file, new_code)
    if not conflict:
        return
    logger.warning(f"⚠️  {conflict}")
    choice = _prompt_text("Overwrite the existing access code file? [y/N]: ", args).lower()
    if choice not in ("y", "yes"):
        logger.error("Aborted to avoid overwriting the existing access code file.")
        abort("", exit_code=EXIT_CONFIG_ERROR)


def _prompt_interactive_access_code(args, max_attempts=3):
    """Prompt for the access code, rejecting empty/placeholder input.

    The interactive path previously fed _prompt_secret straight into the config
    with no validation, so pressing Enter wrote an immediately-broken config
    ("Config must contain a real access_code...") and, on a re-run, replaced a
    working credential with the empty one (access_code is _WIZARD_OWNED). Mirror
    the non-interactive validation (_access_code_value_problem) and re-prompt.
    """
    for _ in range(max_attempts):
        access_code = _prompt_secret("Enter Access Code (found on printer screen): ", args).strip()
        problem = _access_code_value_problem(access_code)
        if not problem:
            return access_code
        logger.error(problem)
    logger.error("No valid access code provided after multiple attempts.")
    abort("", exit_code=EXIT_CONFIG_ERROR)


def _cmd_setup_interactive(args):
    """TTY-only guided setup (mDNS + prompts). Unit-tested via headless rejection + noninteractive."""
    from bambu_cli.config import _DEFAULT_ORCA, _DEFAULT_PROFILES

    discovered = []
    use_manual = False

    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        logger.warning("⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled.")
        logger.info(
            "   To enable auto-discovery, reinstall the package "
            "(e.g. `uv pip install -e .` from a source checkout, or `pip install platecli`)."
        )
        choice = _prompt_text("Would you like to perform a manual configuration instead? [Y/n]: ", args).lower()
        if choice in ("", "y", "yes"):
            use_manual = True
        else:
            abort("", exit_code=EXIT_CONFIG_ERROR)

    if not use_manual:
        logger.info("🔍 Scanning local network for Bambu printers...")
        seen_services = set()
        discovery_lock = threading.Lock()
        discovery_event = threading.Event()

        class MyListener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    # Bambu printers usually advertise as _bblp._tcp.local
                    try:
                        ip = _service_info_address(info)
                    except ValueError as e:
                        logger.warning(f"⚠️  Skipping {name}: {e}")
                        return
                    service_key = (name, ip)
                    with discovery_lock:
                        if service_key in seen_services:
                            logger.debug(f"Ignoring duplicate mDNS service: {name} at {ip}")
                            return
                        seen_services.add(service_key)
                        discovered.append({"name": name, "ip": ip, "info": info})
                    logger.info(f"   ✨ Found: {name} at {ip}")

            def update_service(self, zc, type_, name):
                pass

            def remove_service(self, zc, type_, name):
                pass

        zc = None
        browser = None
        scan_timeout = 5.0
        if hasattr(args, "scan_timeout") and args.scan_timeout is not None:
            try:
                scan_timeout = float(args.scan_timeout)
            except ValueError:
                pass
        try:
            zc = Zeroconf()
            browser = ServiceBrowser(zc, "_bblp._tcp.local.", MyListener())
            discovery_event.wait(timeout=scan_timeout)
        except Exception as e:
            logger.warning(f"⚠️   mDNS discovery error: {e}. Falling back to manual configuration...")
            use_manual = True
        finally:
            if browser is not None:
                try:
                    browser.cancel()
                except Exception:
                    pass
            if zc is not None:
                try:
                    zc.close()
                except Exception:
                    pass

    if use_manual:
        ip = _prompt_text("Enter Printer IP Address (e.g. 192.168.1.50): ", args)
        if not ip:
            logger.error("IP Address is required.")
            abort("", exit_code=EXIT_CONFIG_ERROR)
        serial = _prompt_text("Enter Printer Serial Number (sticker or info screen): ", args).upper()
        if not serial:
            logger.error("Serial Number is required.")
            abort("", exit_code=EXIT_CONFIG_ERROR)
        detected_model = "P1P"
    else:
        if not discovered:
            logger.error("No printers found. Ensure printer is on the same network.")
            abort("", exit_code=EXIT_NETWORK_ERROR)

        # Simple selection if multiple
        if len(discovered) > 1:
            logger.info("\nSelect a printer:")
            for i, d in enumerate(discovered):
                logger.info(f"  [{i}] {d['name']} ({d['ip']})")
            choice = _prompt_text("Choice: ", args)
            try:
                idx = int(choice)
                if idx < 0 or idx >= len(discovered):
                    logger.error(f"Invalid selection: {choice}. Must be 0-{len(discovered) - 1}.")
                    abort("", exit_code=EXIT_COMMAND_ERROR)
                selected = discovered[idx]
            except ValueError:
                logger.error(f"Invalid input: '{choice}'. Enter a number.")
                abort("", exit_code=EXIT_COMMAND_ERROR)
        else:
            selected = discovered[0]

        ip = selected["ip"]
    if not use_manual:
        # Extract serial/model from names like
        # "BBLP-P1P-SN123._bblp._tcp.local." without saving "P1P-SN123"
        # as the MQTT serial.
        serial, detected_model = _parse_mdns_printer_identity(selected["name"])

        logger.warning(
            f"⚠️  Printer discovered via unauthenticated mDNS. Verify that the reported IP "
            f"({selected['ip']}) belongs to your actual printer to protect your access code!"
        )
        logger.info(f"\nConfiguring {selected['name']}...")
    else:
        logger.info("\nConfiguring manual printer...")
    access_code = _prompt_interactive_access_code(args)

    # Guided prompt for model & nozzle
    logger.info(f"Printer model detected: {detected_model}")
    model_input = _normalize_model(
        _prompt_text(f"Confirm printer model (P1P/P1S/X1C/X1E/X1/A1/A1M) [default: {detected_model}]: ", args),
        detected_model,
    )
    nozzle_input = _normalize_nozzle(_prompt_text("Enter nozzle size (0.2, 0.4, 0.6, 0.8) [default: 0.4]: ", args))
    access_code_file = _prompt_access_code_file_path(args)
    _validate_setup_access_code_file(args, access_code_file)
    if access_code_file:
        _confirm_interactive_access_code_file_overwrite(args, _expand_path(access_code_file), access_code)

    try:
        from bambu_cli.protocols.mqtt import probe_cert_fingerprint

        logger.info("🔒 Fetching printer TLS certificate fingerprint...")
        # Assumes the printer serves the same certificate on ports 8883 (MQTT),
        # 990 (FTPS), and 6000 (camera) — true for Bambu firmware to date.
        cert_fingerprint = probe_cert_fingerprint(ip, 8883, timeout=5)
        logger.info(f"   Fingerprint: {cert_fingerprint}")
        logger.info("   (trust-on-first-use: verify this matches your printer if on an untrusted network)")
    except Exception as e:
        logger.warning(f"⚠️  Could not fetch TLS certificate: {e}")
        logger.warning("   Connections may fail if the fingerprint is required.")
        cert_fingerprint = None

    try:
        config = _build_setup_config(
            ip=ip,
            serial=serial,
            model=model_input,
            nozzle=nozzle_input,
            access_code=access_code,
            access_code_file=access_code_file,
            orca_slicer=_DEFAULT_ORCA,
            profiles_dir=_DEFAULT_PROFILES,
            cert_fingerprint=cert_fingerprint,
        )
    except ValueError as exc:
        message = str(exc)
        logger.error(message)
        _setup_json_error(args, message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        _write_setup_config(config, access_code_file_secret=access_code if access_code_file else None)
    except OSError as exc:
        message = f"Could not write setup files: {_exception_for_message(exc)}"
        logger.error(message)
        _setup_file_error(
            args,
            message,
            **_setup_path_details(config_path=_config_path(), access_code_file=access_code_file),
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _cmd_setup(args):
    """Guided setup to discover printer and generate config."""
    from bambu_cli.setup_cmd.migrate import _cmd_migrate_access_code

    if _namespace_get(args, "migrate_access_code", False):
        _cmd_migrate_access_code(args)
        return
    if _setup_args_provided(args):
        _cmd_setup_noninteractive(args)
        return

    if not sys.stdin.isatty():
        message = (
            "Interactive setup cannot run in a headless environment. "
            "Run setup non-interactively with --printer-ip, --serial, and --access-code-file, for example:\n"
            "  plate setup --printer-ip 192.168.1.42 --serial 01S00A000000000 "
            "--access-code-file ~/.bambu_access"
        )
        abort(
            message,
            exit_code=EXIT_CONFIG_ERROR,
            failed_step="validate",
        )

    _cmd_setup_interactive(args)
