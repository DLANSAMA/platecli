import json
import os
from typing import NoReturn

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
        from bambu_cli.paths import exception_for_message as _exception_for_message
        from bambu_cli.paths import path_for_message as _path_for_message

        logger.error(f"Could not create output directory {_path_for_message(path)}: {_exception_for_message(e)}")
        abort("", exit_code=EXIT_FILE_ERROR)


def _ensure_parent_dir(path):
    """Create the parent directory for an output file when one was supplied."""
    from bambu_cli.paths import expand_path as _expand_path

    parent = os.path.dirname(_expand_path(path))
    if parent:
        _ensure_output_dir(parent)


_JSON_PATH_KEYS = {
    "access_code_file",
    "config_path",
    "downloaded_path",
    "extracted_path",
    "file",
    "local_path",
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
    """Strip URL userinfo. Delegates to ``jsonio.redact_url_credentials``."""
    from bambu_cli.jsonio import redact_url_credentials

    return redact_url_credentials(url)


_HOME_DIR = os.path.expanduser("~")


def _json_path(path):
    """Normalize separators for JSON path fields. Delegates to ``paths.json_path``."""
    from bambu_cli.paths import json_path

    return json_path(path)


def _display_path(path):
    if not path:
        return path
    # Require a separator boundary after the home prefix so a sibling directory
    # whose name merely starts with the home dir name (e.g. /home/user2 vs
    # /home/user) is not mangled into "~2/…" — that path would not exist and
    # could not be expanded back by a JSON consumer.
    if path == _HOME_DIR:
        return "~"
    for sep in (os.sep, os.altsep):
        if sep and path.startswith(_HOME_DIR + sep):
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
                # A URL keeps its own separators; only local paths are normalized.
                result[key] = redacted if redacted != item else _json_path(_display_path(item))
            else:
                result[key] = _json_display_paths(item)
        return result
    if isinstance(value, list):
        return [_json_display_paths(item) for item in value]
    if isinstance(value, str):
        return _redact_url_credentials(value)
    return value


def _as_payload(data):
    """Accept either a raw dict or a contract from ``bambu_cli.contracts``.

    Contracts are the typed description of each command's ``--json`` output and
    generate ``docs/schemas``. They render to a plain dict here rather than
    serializing themselves, so every payload still goes through the redaction
    pass below — that is the whole reason these are dataclasses and not pydantic
    models (see bambu_cli/contracts/base.py).
    """
    from bambu_cli.contracts import Contract

    return data.to_payload() if isinstance(data, Contract) else data


def emit_json(data):
    global _JSON_EMITTED
    _JSON_EMITTED = True
    print(json.dumps(_json_display_paths(_as_payload(data)), indent=2))


def emit_json_line(data):
    """Emit one compact JSON object on its own line (NDJSON).

    Used for streaming output (e.g. ``status --monitor --json``) where an agent
    consumes one event per line as they arrive, rather than a single pretty
    document at the end.
    """
    global _JSON_EMITTED
    _JSON_EMITTED = True
    print(json.dumps(_json_display_paths(_as_payload(data)), separators=(",", ":")), flush=True)


def _namespace_get(args, key, default=None):
    return getattr(args, key, default)


def write_error_envelope(args, command, exit_code, error, failed_step=None, **extra):
    """Write the JSON error envelope. Does not raise — ``cli.main`` owns process exit."""
    global _JSON_EMITTED
    _JSON_EMITTED = True
    global _LAST_ERROR_PAYLOAD
    from bambu_cli.contracts import ErrorEnvelope

    envelope = ErrorEnvelope(
        status="error",
        command=command,
        exit_code=exit_code,
        error=error,
        failed_step=failed_step,
        printer_error_code=extra.pop("printer_error_code", None),
        printer_error_code_hex=extra.pop("printer_error_code_hex", None),
        next_command=extra.pop("next_command", None),
        detail=extra.pop("detail", None),
    )
    payload = envelope.to_payload(**extra)
    _LAST_ERROR_PAYLOAD = payload
    if not bool(_namespace_get(args, "json", False)):
        return
    emit_json(payload)


def emit_json_error(args, command, exit_code, error, failed_step=None, **extra) -> NoReturn:
    """Domain failure: log, record extras, then raise. ``cli.main`` emits JSON.

    Kept as a thin wrapper so remaining call sites become a single raise
    instead of emit-then-abort. New code should call ``abort`` directly.
    """
    from bambu_cli.errors import abort
    from bambu_cli.logging_utils import safe_log_error

    extra = dict(extra)
    record_error_detail(
        command, exit_code, error or f"Command failed (exit {exit_code})", failed_step=failed_step, **extra
    )
    if error:
        safe_log_error(error)
    # The ERROR line was just written; tell cli.main so it does not print it again.
    abort(error, exit_code=exit_code, failed_step=failed_step, extra=extra, command=command, logged=bool(error))


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
    from bambu_cli.contracts import Download

    if isinstance(payload, dict):
        import dataclasses

        known = {field.name for field in dataclasses.fields(Download)}
        kwargs = {key: payload[key] for key in known if key in payload}
        extras = {key: value for key, value in payload.items() if key not in known}
        payload = Download(**kwargs).to_payload(**extras)
    _LAST_DOWNLOAD_PAYLOAD = payload
    if bool(_namespace_get(args, "json", False)):
        emit_json(payload)


import ipaddress
import socket
import threading

_RESOLVE_IP_CACHE: dict[str, str] = {}
_RESOLVE_IP_LOCK = threading.Lock()
_RESOLVE_IP_CACHE_MAX = 1024
# host -> {"done": Event, "ip": str|None} for resolutions currently in flight.
_RESOLVE_IP_INFLIGHT: dict[str, dict] = {}


def _try_resolve_ip(host, timeout=5.0):
    """Bounded hostname resolution. Returns the IP string, or None on failure.

    This is the honest-signal variant of :func:`_resolve_ip`: callers that need
    to distinguish "resolved" from "gave up" must use this one. ``_resolve_ip``
    cannot express failure -- it returns the hostname unchanged -- so a caller
    that wants to *validate* a hostname and follows it with its own
    ``socket.getaddrinfo`` reintroduces the very unbounded lookup the timeout
    exists to prevent.

    ``getaddrinfo`` is not interruptible, so the timeout is implemented by
    joining a daemon worker and walking away. In-flight resolutions are shared
    per host, so repeatedly asking about a blackholed DNS server parks one
    worker thread, not one per call.
    """
    if not host or host == "0.0.0.0":
        return None

    # Fast path: already an IP literal (IPv4 or IPv6) — no thread, no lookup.
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    with _RESOLVE_IP_LOCK:
        if host in _RESOLVE_IP_CACHE:
            return _RESOLVE_IP_CACHE[host]
        pending = _RESOLVE_IP_INFLIGHT.get(host)
        if pending is None:
            pending = {"done": threading.Event(), "ip": None}
            _RESOLVE_IP_INFLIGHT[host] = pending
            owner = True
        else:
            owner = False

    def _resolve():
        try:
            addr_info = socket.getaddrinfo(host, None)
            if addr_info:
                pending["ip"] = addr_info[0][4][0]
        except Exception:
            pass
        finally:
            pending["done"].set()
            with _RESOLVE_IP_LOCK:
                # Drop the shared slot only once the worker is actually done, so
                # a later call after a timeout starts a fresh attempt rather than
                # attaching to a thread that has already been abandoned.
                if _RESOLVE_IP_INFLIGHT.get(host) is pending:
                    del _RESOLVE_IP_INFLIGHT[host]

    if owner:
        threading.Thread(target=_resolve, daemon=True).start()

    if not pending["done"].wait(timeout):
        return None  # still hung in getaddrinfo; the daemon thread is abandoned
    ip = pending["ip"]

    # Only cache a genuine success. A DNS failure or a join timeout must NOT be
    # cached permanently — otherwise a transient hiccup on the first resolve
    # would skip pre-resolution for the whole process lifetime.
    if ip is not None:
        with _RESOLVE_IP_LOCK:
            if len(_RESOLVE_IP_CACHE) >= _RESOLVE_IP_CACHE_MAX:
                _RESOLVE_IP_CACHE.clear()
            _RESOLVE_IP_CACHE[host] = ip
    return ip


def _resolve_ip(host, timeout=5.0):
    """Resolve a hostname to an IP, or return ``host`` unchanged if that fails.

    The forgiving variant, for transport call sites (paho, ftplib) that will
    re-resolve the name themselves anyway and where TLS pinning still applies.
    Use :func:`_try_resolve_ip` when you need to know whether it worked.
    """
    if not host or host == "0.0.0.0":
        return host
    return _try_resolve_ip(host, timeout=timeout) or host


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
