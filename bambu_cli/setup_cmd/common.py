"""Shared setup helpers: config path, secure writes, prompts, config building."""

import getpass
import json
import os
import re
import sys

from bambu_cli.cli import _display_path, _expand_path
from bambu_cli.config import CONFIG_PATH, MODEL_MAPPING
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _secure_makedirs, emit_json_error


def _config_path():
    """Return the active config path (patch ``CONFIG_PATH`` or this helper in tests)."""
    return CONFIG_PATH


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
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
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
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
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


def _prompt_text(prompt, args=None):  # pragma: no cover -- interactive prompt
    if args and getattr(args, "json", False):
        emit_json_error(
            args,
            "setup",
            EXIT_CONFIG_ERROR,
            "Interactive prompt required, but json mode is active",
            failed_step="validate",
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        print(prompt, end="", file=sys.stderr, flush=True)
        return input().strip()
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR)


def _prompt_secret(prompt, args=None):  # pragma: no cover -- interactive secret
    if args and getattr(args, "json", False):
        emit_json_error(
            args,
            "setup",
            EXIT_CONFIG_ERROR,
            "Interactive prompt required, but json mode is active",
            failed_step="validate",
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        return getpass.getpass(prompt)
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR)


def _prompt_access_code_file_path(args=None):  # pragma: no cover -- interactive path
    """Return a secret-file path for guided setup, or None if the user opts out."""
    default_path = _default_access_code_file_path()
    choice = _prompt_text(f"Store access code outside config.json at {default_path}? [Y/n]: ", args).lower()
    if choice in ("", "y", "yes"):
        return default_path
    if choice in ("n", "no"):
        return None
    logger.warning("⚠️  Unrecognized choice; storing access code in a separate access_code file.")
    return default_path


def _build_setup_config(
    ip,
    serial,
    model,
    nozzle,
    access_code=None,
    access_code_file=None,
    orca_slicer=None,
    profiles_dir=None,
    cert_fingerprint=None,
    insecure_tls=False,
):
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
    if expanded.startswith("-"):
        message = f"Invalid access-code file path: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if os.path.abspath(expanded) == os.path.abspath(_expand_path(_config_path())):
        message = "access_code_file must be separate from config.json."
        logger.error(message)
        _setup_json_error(
            args,
            message,
            **_setup_path_details(access_code_file=expanded, config_path=_config_path()),
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if os.path.isdir(expanded):
        message = f"Access code file path is a directory, not a file: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        abort("", exit_code=EXIT_CONFIG_ERROR)
    return expanded


def _looks_like_placeholder(value, placeholders):
    normalized = str(value or "").strip().upper()
    return not normalized or normalized in placeholders or normalized.startswith("YOUR_")
