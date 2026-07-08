"""The `preflight` command: local install/config readiness checks."""

import importlib.util
import os
import platform
import shutil
import stat
import sys

# load_config, _config_path, and _display_path are bound at module level so
# tests can patch them here (bambu_cli.setup_cmd.preflight.<name>).
from bambu_cli.cli import _display_path, _exception_for_message, _expand_path
from bambu_cli.config import _access_code_value_problem, load_config
from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_SUCCESS
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.setup_cmd.common import _config_path, _looks_like_placeholder
from bambu_cli.utils import emit_json


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


def collect_preflight_checks():  # pragma: no cover -- environment probes; core matrix unit-tested
    """Collect local install/config checks without contacting the printer."""
    from bambu_cli.context import current_config, current_settings
    from bambu_cli.protocols import mqtt as mqtt_protocol
    from bambu_cli.slicer import _slicer_executable_problem

    checks = []
    py_version = ".".join(str(part) for part in sys.version_info[:3])
    checks.append(_preflight_result("ok", "python", f"Python {py_version} is supported."))

    if mqtt_protocol.mqtt is not None or _module_available("paho.mqtt.client"):
        checks.append(_preflight_result("ok", "paho-mqtt", "paho-mqtt is available."))
    else:
        checks.append(
            _preflight_result(
                "error",
                "paho-mqtt",
                "Missing Python package: paho-mqtt. Reinstall the package "
                "(e.g. `uv pip install -e .` from a source checkout, or `pip install bambu-local-cli`).",
            )
        )

    if _module_available("zeroconf"):
        checks.append(_preflight_result("ok", "zeroconf", "zeroconf is available for network discovery."))
    else:
        checks.append(
            _preflight_result(
                "warning",
                "zeroconf",
                "zeroconf is not installed; guided setup still works with manual printer details.",
            )
        )

    cfg = load_config(exit_on_fail=False)
    if cfg:
        checks.append(_preflight_result("ok", "config", f"Config found at {_display_path(_config_path())}."))
        config_permissions = _file_permission_check(_config_path(), "config-permissions")
        if config_permissions:
            checks.append(config_permissions)
        printer_ip = cfg.get("printer_ip")
        if _looks_like_placeholder(printer_ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP"}):
            checks.append(
                _preflight_result("error", "printer-ip", "Config must contain a real printer_ip or hostname.")
            )
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
                    checks.append(
                        _preflight_result(
                            "ok",
                            "access-code",
                            "Access code file exists and contains a non-placeholder value.",
                            access_file,
                        )
                    )
                    access_permissions = _file_permission_check(expanded, "access-code-permissions")
                    if access_permissions:
                        checks.append(access_permissions)
            else:
                checks.append(
                    _preflight_result(
                        "error", "access-code", f"Access code file not found: {_display_path(access_file)}"
                    )
                )
        elif has_inline_code and _access_code_value_problem(access_code):
            checks.append(_preflight_result("error", "access-code", _access_code_value_problem(access_code)))
        elif has_inline_code:
            checks.append(
                _preflight_result(
                    "warning",
                    "access-code",
                    "config.json contains an inline access_code; move it to an access_code_file "
                    "(run: bambu setup --migrate-access-code or edit config). "
                    "Inline support will be removed in a future release.",
                )
            )
        else:
            checks.append(
                _preflight_result("error", "access-code", "Config must contain access_code or access_code_file.")
            )
    else:
        checks.append(
            _preflight_result(
                "error", "config", f"Config not found at {_display_path(_config_path())}. Run `setup` first."
            )
        )

    settings = current_settings()
    cfg_for_paths = cfg or current_config() or {}
    from bambu_cli.config import detect_orca_slicer, detect_profiles_dir

    orca_path = _expand_path(cfg_for_paths.get("orca_slicer", settings.orca_slicer))
    orca_problem = _slicer_executable_problem(orca_path)
    if not orca_problem:
        checks.append(_preflight_result("ok", "orca-slicer", f"OrcaSlicer found at {_display_path(orca_path)}."))
    else:
        detected = detect_orca_slicer()
        if detected and detected != orca_path:
            orca_problem += (
                f' Detected an OrcaSlicer at {_display_path(detected)} — set "orca_slicer" to this in config.json.'
            )
        checks.append(_preflight_result("error", "orca-slicer", orca_problem))

    profiles_dir = _expand_path(cfg_for_paths.get("profiles_dir", settings.profiles_dir))
    if os.path.isdir(profiles_dir):
        checks.append(
            _preflight_result("ok", "profiles-dir", f"OrcaSlicer profiles found at {_display_path(profiles_dir)}.")
        )
    else:
        message = f"OrcaSlicer BBL profiles not found at {_display_path(profiles_dir)}."
        detected_profiles = detect_profiles_dir()
        if detected_profiles and detected_profiles != profiles_dir:
            message += (
                f' Detected profiles at {_display_path(detected_profiles)} — set "profiles_dir" to this in config.json.'
            )
        checks.append(_preflight_result("error", "profiles-dir", message))

    if shutil.which("gmsh"):
        checks.append(_preflight_result("ok", "gmsh", "gmsh is available for STEP/STP conversion."))
    else:
        checks.append(
            _preflight_result(
                "warning", "gmsh", "gmsh not found; STEP/STP files cannot be converted. STL/3MF/G-code still work."
            )
        )

    if platform.system() == "Linux":
        if shutil.which("xvfb-run"):
            checks.append(_preflight_result("ok", "xvfb-run", "xvfb-run is available for headless Linux slicing."))
        else:
            checks.append(
                _preflight_result("warning", "xvfb-run", "xvfb-run not found; headless Linux slicing may fail.")
            )

    if shutil.which("docker"):
        checks.append(_preflight_result("ok", "docker", "Docker is available for optional camera snapshots."))
    else:
        checks.append(_preflight_result("warning", "docker", "Docker not found; camera snapshots will be unavailable."))

    return checks


def _cmd_preflight(args):  # pragma: no cover -- preflight CLI emit; collect_preflight unit-tested
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
        abort("", exit_code=exit_code)
