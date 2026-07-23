import json
import os

from bambu_cli.errors import abort

from .constants import (
    EXIT_FILE_ERROR,
)


def _secure_makedirs(path, exist_ok=True):
    os.makedirs(path, mode=0o700, exist_ok=exist_ok)


def _ensure_output_dir(path):
    """Create an output directory before expensive work starts."""

    from bambu_cli.logging_utils import logger

    try:
        _secure_makedirs(path, exist_ok=True)
    except OSError as e:
        from bambu_cli.cli import _exception_for_message, _path_for_message

        logger.error(f"Could not create output directory {_path_for_message(path)}: {_exception_for_message(e)}")
        abort("", exit_code=EXIT_FILE_ERROR)


def _ensure_parent_dir(path):
    """Create the parent directory for an output file when one was supplied."""
    from bambu_cli.cli import _expand_path

    parent = os.path.dirname(_expand_path(path))
    if parent:
        _ensure_output_dir(parent)


_JSON_PATH_KEYS = {
    "access_code_file",
    "config_path",
    "downloaded_path",
    "extracted_path",
    "file",
    "output",
    "path",
    "printable_path",
    "normalized_source",
    "orca_slicer",
    "source",
    "profiles_dir",
    "workdir",
    "detail",
    "details",
}

_JSON_EMITTED = False
_LAST_ERROR_PAYLOAD = None
_LAST_DOWNLOAD_PAYLOAD = None


def _redact_url_credentials(url):
    # Performance optimization: Fast-path for non-strings and strings that clearly
    # cannot contain credentials (missing '@'). This avoids the overhead of
    # lazy importing urllib and running the relatively expensive urlparse
    # on every string value in large JSON responses.
    if not isinstance(url, str) or "@" not in url:
        return url

    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)
            return urlunparse(parsed)
    except Exception:
        pass
    return url


_HOME_DIR = os.path.expanduser("~")


def _display_path(path):
    if not path:
        return path
    if path.startswith(_HOME_DIR):
        return "~" + path[len(_HOME_DIR) :]
    return path


def _compact_all_strings(val):
    if isinstance(val, dict):
        return {k: _compact_all_strings(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_compact_all_strings(v) for v in val]
    if isinstance(val, str):
        redacted = _redact_url_credentials(val)
        return redacted if redacted != val else _display_path(val)
    return val


def _json_display_paths(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ("detail", "details"):
                result[key] = _compact_all_strings(item)
            elif key in _JSON_PATH_KEYS and (isinstance(item, str) or item is None):
                redacted = _redact_url_credentials(item)
                result[key] = redacted if redacted != item else _display_path(item)
            else:
                result[key] = _json_display_paths(item)
        return result
    if isinstance(value, list):
        return [_json_display_paths(item) for item in value]
    if isinstance(value, str):
        return _redact_url_credentials(value)
    return value


def emit_json(data):
    global _JSON_EMITTED
    _JSON_EMITTED = True
    print(json.dumps(_json_display_paths(data), indent=2))


def emit_json_line(data):
    """Emit one compact JSON object on its own line (NDJSON).

    Used for streaming output (e.g. ``status --monitor --json``) where an agent
    consumes one event per line as they arrive, rather than a single pretty
    document at the end.
    """
    global _JSON_EMITTED
    _JSON_EMITTED = True
    print(json.dumps(_json_display_paths(data), separators=(",", ":")), flush=True)


def _namespace_get(args, key, default=None):
    return getattr(args, key, default)


def emit_json_error(args, command, exit_code, error, failed_step=None, **extra):
    global _JSON_EMITTED
    _JSON_EMITTED = True
    global _LAST_ERROR_PAYLOAD
    payload = {
        "status": "error",
        "command": command,
        "exit_code": exit_code,
        "error": error,
    }
    if failed_step:
        payload["failed_step"] = failed_step
    payload.update(extra)
    _LAST_ERROR_PAYLOAD = payload
    if not bool(_namespace_get(args, "json", False)):
        return
    emit_json(payload)


def record_error_detail(command, exit_code, error, failed_step=None, **extra):
    global _LAST_ERROR_PAYLOAD
    payload = {
        "status": "error",
        "command": command,
        "exit_code": exit_code,
        "error": error,
    }
    if failed_step:
        payload["failed_step"] = failed_step
    payload.update(extra)
    _LAST_ERROR_PAYLOAD = payload


def _record_download_success(args, payload):
    global _LAST_DOWNLOAD_PAYLOAD
    _LAST_DOWNLOAD_PAYLOAD = payload
    if bool(_namespace_get(args, "json", False)):
        emit_json(payload)


import socket
import threading

_RESOLVE_IP_CACHE: dict[str, str] = {}


def _resolve_ip(host, timeout=5.0):
    """Resolve a hostname to an IP address (supporting IPv4 and IPv6) exactly once.
    Includes a timeout to prevent DNS resolution deadlocks.
    """
    if not host or host == "0.0.0.0":
        return host

    if host in _RESOLVE_IP_CACHE:
        return _RESOLVE_IP_CACHE[host]

    result = [host]

    def _resolve():
        try:
            addr_info = socket.getaddrinfo(host, None)
            if addr_info:
                result[0] = addr_info[0][4][0]
        except Exception:
            pass

    t = threading.Thread(target=_resolve)
    t.daemon = True
    t.start()
    t.join(timeout)

    _RESOLVE_IP_CACHE[host] = result[0]
    return result[0]


_sequence_counter = 0


def get_sequence_id():
    global _sequence_counter
    _sequence_counter += 1
    return str(_sequence_counter)


def _redacted_serial():
    """Return a non-identifying serial placeholder for reports written to disk."""
    from bambu_cli.context import current_settings

    serial = current_settings().serial
    return "UNKNOWN" if not serial or serial == "UNKNOWN" else "<redacted>"
