"""Setup and preflight commands: guided/non-interactive config creation,
mDNS printer discovery, secure secret storage, and local readiness checks."""
import getpass
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import stat
import sys
import threading

from bambu_cli.constants import (
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_FILE_ERROR,
    EXIT_NETWORK_ERROR,
    EXIT_SUCCESS,
)
from bambu_cli.logging_utils import logger
from bambu_cli.utils import emit_json, emit_json_error, _secure_makedirs
from bambu_cli.cli import (
    _display_path,
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _setup_args_provided,
)
from bambu_cli.config import (
    CONFIG_PATH,
    MODEL_MAPPING,
    _access_code_value_problem,
    load_config,
)


def _config_path():
    """Read the config path through the bambu module so tests can patch it."""
    from bambu_cli import bambu
    return getattr(bambu, "CONFIG_PATH", CONFIG_PATH)


def _normalize_model(model, default="P1P"):
    model = (model or default or "P1P").strip().upper()
    if model not in MODEL_MAPPING:
        logger.warning(f"⚠️  Unknown model '{model}'. Defaulting to 'P1P'.")
        return "P1P"
    return model


def _normalize_nozzle(nozzle):
    nozzle = str(nozzle or "0.4").strip()
    if nozzle not in ["0.2", "0.4", "0.6", "0.8"]:
        logger.warning("⚠️  Standard nozzle size should be one of 0.2, 0.4, 0.6, or 0.8. Using standard '0.4'.")
        return "0.4"
    return nozzle


def _secure_write_json(path, data):
    expanded = _expand_path(path)
    directory = os.path.dirname(expanded)
    if directory:
        _secure_makedirs(directory, exist_ok=True)
    if os.path.exists(expanded):
        try:
            os.chmod(expanded, 0o600)
        except OSError:
            pass
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(expanded, 0o600)
    except OSError:
        pass


def _secure_write_text(path, text):
    expanded = _expand_path(path)
    directory = os.path.dirname(expanded)
    if directory:
        _secure_makedirs(directory, exist_ok=True)
    if os.path.exists(expanded):
        try:
            os.chmod(expanded, 0o600)
        except OSError:
            pass
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), 'w', encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(expanded, 0o600)
    except OSError:
        pass


def _default_access_code_file_path():
    """Store guided-setup secrets next to config.json instead of inside it."""
    config_dir = os.path.dirname(_expand_path(_config_path()))
    if config_dir:
        return os.path.join(config_dir, "access_code")
    return os.path.abspath("bambu_access_code")


def _prompt_text(prompt, args=None):
    if args and getattr(args, "json", False):
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, "Interactive prompt required, but json mode is active", failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        print(prompt, end="", file=sys.stderr, flush=True)
        return input().strip()
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)


def _prompt_secret(prompt, args=None):
    if args and getattr(args, "json", False):
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, "Interactive prompt required, but json mode is active", failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        return getpass.getpass(prompt)
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)


def _prompt_access_code_file_path(args=None):
    """Return a secret-file path for guided setup, or None if the user opts out."""
    default_path = _default_access_code_file_path()
    choice = _prompt_text(f"Store access code outside config.json at {default_path}? [Y/n]: ", args).lower()
    if choice in ("", "y", "yes"):
        return default_path
    if choice in ("n", "no"):
        return None
    logger.warning("⚠️  Unrecognized choice; storing access code in a separate access_code file.")
    return default_path


def _build_setup_config(ip, serial, model, nozzle, access_code=None,
                        access_code_file=None, orca_slicer=None,
                        profiles_dir=None, cert_fingerprint=None,
                        insecure_tls=False):
    from bambu_cli.config import _DEFAULT_ORCA, _DEFAULT_PROFILES
    serial_val = serial.strip().upper()
    if not re.match(r"^[A-Za-z0-9_-]+$", serial_val):
        raise ValueError(f"Invalid serial number: {serial_val}. Serial number must be alphanumeric.")
    config = {
        "printer_ip": ip,
        "serial": serial_val,
        "username": "bblp",
        "model": model,
        "nozzle": nozzle,
        "orca_slicer": orca_slicer or _DEFAULT_ORCA,
        "profiles_dir": profiles_dir or _DEFAULT_PROFILES,
    }
    if access_code_file:
        config["access_code_file"] = access_code_file
    else:
        config["access_code"] = access_code
    if cert_fingerprint:
        config["cert_fingerprint"] = cert_fingerprint
    if insecure_tls:
        config["insecure_tls"] = True
    return config


def _write_setup_config(config, access_code_file_secret=None):
    if access_code_file_secret is not None:
        _secure_write_text(config["access_code_file"], access_code_file_secret.rstrip("\n") + "\n")
    _secure_write_json(_config_path(), config)
    if sys.platform == "win32":
        logger.warning(
            "   ⚠️  On Windows, file mode 0600 is ignored. Consider storing the "
            "access code in a separate `access_code_file` protected via NTFS ACLs."
        )
    logger.info(f"\n✅ Config saved to {_display_path(_config_path())}")
    logger.info("Run 'doctor' command to verify setup.")
    return {
        "config_path": _display_path(_config_path()),
        "access_code_file": _display_path(config.get("access_code_file")),
    }


def _setup_summary(config):
    access_code_file = config.get("access_code_file")
    payload = {
        "status": "configured",
        "command": "setup",
        "config_path": _display_path(_config_path()),
        "printer_ip_configured": bool(config.get("printer_ip")),
        "serial_configured": bool(config.get("serial")),
        "access_code_storage": "file" if access_code_file else "inline",
        "model": config.get("model"),
        "nozzle": config.get("nozzle"),
        "orca_slicer_configured": bool(config.get("orca_slicer")),
        "profiles_dir_configured": bool(config.get("profiles_dir")),
        "cert_fingerprint_configured": bool(config.get("cert_fingerprint")),
        "insecure_tls": bool(config.get("insecure_tls", False)),
    }
    if access_code_file:
        payload["access_code_file"] = _display_path(access_code_file)
    return payload


def _setup_path_details(**paths):
    return {key: _display_path(value) for key, value in paths.items()}


def _setup_json_error(args, message, **extra):
    emit_json_error(args, "setup", EXIT_CONFIG_ERROR, message, failed_step="validate", **extra)


def _setup_file_error(args, message, **extra):
    emit_json_error(args, "setup", EXIT_FILE_ERROR, message, failed_step="write", **extra)


def _validate_setup_access_code_file(args, access_code_file):
    """Validate access-code file path before setup writes or records it."""
    if not access_code_file:
        return None
    expanded = _expand_path(access_code_file)
    if expanded.startswith('-'):
        message = f"Invalid access-code file path: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        sys.exit(EXIT_CONFIG_ERROR)
    if os.path.abspath(expanded) == os.path.abspath(_expand_path(_config_path())):
        message = "access_code_file must be separate from config.json."
        logger.error(message)
        _setup_json_error(
            args,
            message,
            **_setup_path_details(access_code_file=expanded, config_path=_config_path()),
        )
        sys.exit(EXIT_CONFIG_ERROR)
    if os.path.isdir(expanded):
        message = f"Access code file path is a directory, not a file: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        sys.exit(EXIT_CONFIG_ERROR)
    return expanded


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
    match = re.search(r'BBLP-([^._]+)', name, re.IGNORECASE)
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
            serial = service_id[len(prefix):] or "YOUR_SERIAL"
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
        sys.exit(EXIT_CONFIG_ERROR)
    if access_code_env:
        access_code = os.environ.get(access_code_env)
        if not access_code:
            message = f"Environment variable {access_code_env} is not set or empty."
            logger.error(message)
            _setup_json_error(args, message, access_code_env=access_code_env)
            sys.exit(EXIT_CONFIG_ERROR)

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
        sys.exit(EXIT_CONFIG_ERROR)

    if access_code_file and not access_code:
        if not os.path.exists(expanded_access_code_file):
            message = f"Access code file not found: {_display_path(expanded_access_code_file)}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)
        try:
            with open(expanded_access_code_file, encoding="utf-8") as f:
                access_code_problem = _access_code_value_problem(f.read().strip())
        except OSError as exc:
            reason = getattr(exc, "strerror", None) or str(exc)
            message = f"Access code file could not be read: {reason}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)
        if access_code_problem:
            logger.error(access_code_problem)
            _setup_json_error(args, access_code_problem, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)

    placeholder_errors = []
    if _looks_like_placeholder(ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP", "USER_PROVIDED_IP"}):
        placeholder_errors.append("--printer-ip")
    if _looks_like_placeholder(serial, {"UNKNOWN", "YOUR_SERIAL", "YOUR_PRINTER_SERIAL", "USER_PROVIDED_SERIAL", "<REDACTED>"}):
        placeholder_errors.append("--serial")
    if access_code and _looks_like_placeholder(access_code, {"ACCESS_CODE", "YOUR_ACCESS_CODE", "USER_PROVIDED_ACCESS_CODE"}):
        placeholder_errors.append("--access-code/--access-code-env")
    if placeholder_errors:
        message = (
            "Non-interactive setup received placeholder values for: "
            + ", ".join(placeholder_errors)
            + ". Replace placeholders with real printer details before running setup."
        )
        logger.error(message)
        _setup_json_error(args, message, placeholders=placeholder_errors)
        sys.exit(EXIT_CONFIG_ERROR)

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
        sys.exit(EXIT_CONFIG_ERROR)
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
        sys.exit(EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _cmd_setup(args):
    """Guided setup to discover printer and generate config."""
    from bambu_cli.config import _DEFAULT_ORCA, _DEFAULT_PROFILES
    if _setup_args_provided(args):
        _cmd_setup_noninteractive(args)
        return

    if not sys.stdin.isatty():
        message = "Interactive setup cannot run in a headless environment. Please run setup non-interactively with --printer-ip, --serial, and --access-code / --access-code-file options."
        logger.error(message)
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, message, failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)

    discovered = []
    use_manual = False

    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        logger.warning("⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled.")
        logger.info("   To enable auto-discovery, run: python -m pip install -r requirements.txt")
        choice = _prompt_text("Would you like to perform a manual configuration instead? [Y/n]: ", args).lower()
        if choice in ('', 'y', 'yes'):
            use_manual = True
        else:
            sys.exit(EXIT_CONFIG_ERROR)

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
            def update_service(self, zc, type_, name): pass
            def remove_service(self, zc, type_, name): pass

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
            sys.exit(EXIT_CONFIG_ERROR)
        serial = _prompt_text("Enter Printer Serial Number (sticker or info screen): ", args).upper()
        if not serial:
            logger.error("Serial Number is required.")
            sys.exit(EXIT_CONFIG_ERROR)
        detected_model = "P1P"
    else:
        if not discovered:
            logger.error("No printers found. Ensure printer is on the same network.")
            sys.exit(EXIT_NETWORK_ERROR)

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
                    sys.exit(EXIT_COMMAND_ERROR)
                selected = discovered[idx]
            except ValueError:
                logger.error(f"Invalid input: '{choice}'. Enter a number.")
                sys.exit(EXIT_COMMAND_ERROR)
        else:
            selected = discovered[0]

        ip = selected["ip"]
    if not use_manual:
        # Extract serial/model from names like
        # "BBLP-P1P-SN123._bblp._tcp.local." without saving "P1P-SN123"
        # as the MQTT serial.
        serial, detected_model = _parse_mdns_printer_identity(selected["name"])

        logger.warning(f"⚠️  Printer discovered via unauthenticated mDNS. Verify that the reported IP "
                       f"({selected['ip']}) belongs to your actual printer to protect your access code!")
        logger.info(f"\nConfiguring {selected['name']}...")
    else:
        logger.info("\nConfiguring manual printer...")
    access_code = _prompt_secret("Enter Access Code (found on printer screen): ", args)

    # Guided prompt for model & nozzle
    logger.info(f"Printer model detected: {detected_model}")
    model_input = _normalize_model(
        _prompt_text(f"Confirm printer model (P1P/P1S/X1C/X1E/X1/A1/A1M) [default: {detected_model}]: ", args),
        detected_model)
    nozzle_input = _normalize_nozzle(_prompt_text("Enter nozzle size (0.2, 0.4, 0.6, 0.8) [default: 0.4]: ", args))
    access_code_file = _prompt_access_code_file_path(args)
    _validate_setup_access_code_file(args, access_code_file)

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
        sys.exit(EXIT_CONFIG_ERROR)
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
        sys.exit(EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _module_available(name):
    try:
        if importlib.util.find_spec(name) is not None:
            return True
    except (ImportError, ValueError, AttributeError):
        pass
    return name in sys.modules


def _preflight_result(status, name, message, detail=None):
    result = {"status": status, "name": name, "message": message}
    if detail is not None:
        result["detail"] = detail
    return result


def _looks_like_placeholder(value, placeholders):
    normalized = str(value or "").strip().upper()
    return not normalized or normalized in placeholders or normalized.startswith("YOUR_")


def _file_permission_check(path, name):
    """Return a preflight warning when a local secret-bearing file is too open."""
    if sys.platform == "win32" or not path:
        return None
    try:
        mode = stat.S_IMODE(os.stat(_expand_path(path)).st_mode)
    except OSError:
        return None
    display = _display_path(path)
    if mode & 0o077:
        return _preflight_result(
            "warning",
            name,
            f"{display} is readable by group/other users; run `chmod 600 {display}`.",
            {"mode": oct(mode)},
        )
    return _preflight_result("ok", name, f"{display} permissions are restricted.", {"mode": oct(mode)})


def collect_preflight_checks():
    """Collect local install/config checks without contacting the printer."""
    from bambu_cli import bambu
    from bambu_cli.protocols import mqtt as mqtt_protocol
    from bambu_cli.slicer import _slicer_executable_problem
    checks = []
    py_version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 9):
        checks.append(_preflight_result("ok", "python", f"Python {py_version} is supported."))
    else:
        checks.append(_preflight_result("error", "python", f"Python {py_version} is too old; Python 3.9+ is required."))

    if mqtt_protocol.mqtt is not None or _module_available("paho.mqtt.client"):
        checks.append(_preflight_result("ok", "paho-mqtt", "paho-mqtt is available."))
    else:
        checks.append(_preflight_result("error", "paho-mqtt", "Missing Python package: paho-mqtt. Run `python -m pip install -r requirements.txt`."))

    if _module_available("zeroconf"):
        checks.append(_preflight_result("ok", "zeroconf", "zeroconf is available for network discovery."))
    else:
        checks.append(_preflight_result("warning", "zeroconf", "zeroconf is not installed; guided setup still works with manual printer details."))

    cfg = load_config(exit_on_fail=False)
    if cfg:
        checks.append(_preflight_result("ok", "config", f"Config found at {_display_path(_config_path())}."))
        config_permissions = _file_permission_check(_config_path(), "config-permissions")
        if config_permissions:
            checks.append(config_permissions)
        printer_ip = cfg.get("printer_ip")
        if _looks_like_placeholder(printer_ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP"}):
            checks.append(_preflight_result("error", "printer-ip", "Config must contain a real printer_ip or hostname."))
        else:
            checks.append(_preflight_result("ok", "printer-ip", "Printer address is configured."))

        serial = cfg.get("serial")
        if _looks_like_placeholder(serial, {"UNKNOWN", "YOUR_SERIAL", "YOUR_PRINTER_SERIAL", "<REDACTED>"}):
            checks.append(_preflight_result("error", "serial", "Config must contain the printer serial number."))
        else:
            checks.append(_preflight_result("ok", "serial", "Printer serial is configured."))

        access_code = cfg.get("access_code")
        has_inline_code = bool(access_code)
        access_file = cfg.get("access_code_file")
        if access_file:
            expanded = _expand_path(access_file)
            if os.path.exists(expanded):
                try:
                    with open(expanded, encoding="utf-8") as f:
                        access_code_problem = _access_code_value_problem(f.read().strip())
                except OSError as exc:
                    access_code_problem = f"Access code file could not be read: {_exception_for_message(exc)}"
                if access_code_problem:
                    checks.append(_preflight_result("error", "access-code", access_code_problem))
                else:
                    checks.append(_preflight_result("ok", "access-code", "Access code file exists and contains a non-placeholder value.", access_file))
                    access_permissions = _file_permission_check(expanded, "access-code-permissions")
                    if access_permissions:
                        checks.append(access_permissions)
            else:
                checks.append(_preflight_result("error", "access-code", f"Access code file not found: {_display_path(access_file)}"))
        elif has_inline_code and _access_code_value_problem(access_code):
            checks.append(_preflight_result("error", "access-code", _access_code_value_problem(access_code)))
        elif has_inline_code:
            checks.append(_preflight_result("warning", "access-code", "Config contains inline access_code; access_code_file is safer for shared machines."))
        else:
            checks.append(_preflight_result("error", "access-code", "Config must contain access_code or access_code_file."))
    else:
        checks.append(_preflight_result("error", "config", f"Config not found at {_display_path(_config_path())}. Run `setup` first."))

    cfg_for_paths = cfg or bambu._cfg or {}
    orca_path = _expand_path(cfg_for_paths.get("orca_slicer", bambu.ORCA_SLICER))
    orca_problem = _slicer_executable_problem(orca_path)
    if not orca_problem:
        checks.append(_preflight_result("ok", "orca-slicer", f"OrcaSlicer found at {_display_path(orca_path)}."))
    else:
        checks.append(_preflight_result("error", "orca-slicer", orca_problem))

    profiles_dir = _expand_path(cfg_for_paths.get("profiles_dir", bambu.PROFILES_DIR))
    if os.path.isdir(profiles_dir):
        checks.append(_preflight_result("ok", "profiles-dir", f"OrcaSlicer profiles found at {_display_path(profiles_dir)}."))
    else:
        checks.append(_preflight_result("error", "profiles-dir", f"OrcaSlicer BBL profiles not found at {_display_path(profiles_dir)}."))

    if shutil.which("gmsh"):
        checks.append(_preflight_result("ok", "gmsh", "gmsh is available for STEP/STP conversion."))
    else:
        checks.append(_preflight_result("warning", "gmsh", "gmsh not found; STEP/STP files cannot be converted. STL/3MF/G-code still work."))

    if platform.system() == "Linux":
        if shutil.which("xvfb-run"):
            checks.append(_preflight_result("ok", "xvfb-run", "xvfb-run is available for headless Linux slicing."))
        else:
            checks.append(_preflight_result("warning", "xvfb-run", "xvfb-run not found; headless Linux slicing may fail."))

    if shutil.which("docker"):
        checks.append(_preflight_result("ok", "docker", "Docker is available for optional camera snapshots."))
    else:
        checks.append(_preflight_result("warning", "docker", "Docker not found; camera snapshots will be unavailable."))

    return checks


def _cmd_preflight(args):
    """Check local install readiness without contacting the printer."""
    checks = collect_preflight_checks()
    error_count = sum(1 for check in checks if check["status"] == "error")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    strict_failed = bool(getattr(args, "strict", False) and warning_count)
    ok = error_count == 0 and not strict_failed
    exit_code = EXIT_SUCCESS if ok else EXIT_CONFIG_ERROR
    if ok:
        status = "ok"
    elif error_count:
        status = "error"
    else:
        status = "warning"

    if getattr(args, "json", False):
        payload = {
            "status": status,
            "command": "preflight",
            "exit_code": exit_code,
            "ok": ok,
            "errors": error_count,
            "warnings": warning_count,
            "strict": bool(getattr(args, "strict", False)),
            "checks": checks,
        }
        emit_json(payload)
    else:
        logger.info("🧪 Bambu CLI Preflight")
        for check in checks:
            icon = {"ok": "✅", "warning": "⚠️ ", "error": "❌"}[check["status"]]
            logger.info(f"   {icon} {check['name']}: {check['message']}")
        if error_count == 0 and not strict_failed:
            logger.info("✅ Preflight passed.")
        elif strict_failed and error_count == 0:
            logger.error(f"Preflight failed in strict mode: {warning_count} warning(s).")
        else:
            logger.error(f"Preflight failed: {error_count} error(s), {warning_count} warning(s).")

    if not ok:
        sys.exit(exit_code)
