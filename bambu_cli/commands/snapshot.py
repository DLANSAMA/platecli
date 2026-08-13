"""``plate snapshot``: save a JPEG from the printer camera.

Direct P1/A1 grab lives in ``bambu_cli.protocols.camera``. The Docker/RTSP
streamer is opt-in (``camera_allow_streamer`` / ``--allow-camera-streamer``)
because it does not honour ``cert_fingerprint``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.config import load_access_code
from bambu_cli.constants import (
    DEFAULT_NETWORK_TIMEOUT,
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_FILE_ERROR,
    EXIT_NETWORK_ERROR,
)
from bambu_cli.context import RuntimeContext
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger, safe_log_error
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.protocols.camera import (
    _SNAPSHOT_SKIP_FRAMES,
    _CameraPinMismatch,
    _grab_camera_frame_direct,
)
from bambu_cli.utils import _ensure_parent_dir, emit_json, emit_json_error

_CONTAINER_PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?(/(tcp|udp|sctp))?$", re.IGNORECASE)


def _utc_stamp(now: datetime.datetime | None = None) -> str:
    """Return a compact UTC timestamp string ``YYYYMMDDTHHMMSSz``."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _is_valid_port_number(token):
    try:
        return 1 <= int(token) <= 65535
    except ValueError:
        return False


def _camera_port_is_valid(camera_port):
    """True if ``camera_port`` is a usable docker ``-p`` value."""
    if not camera_port:
        return False
    container = camera_port.split(":")[-1]
    match = _CONTAINER_PORT_RE.match(container)
    if not match:
        return False
    port_spec = container.split("/", 1)[0]
    return all(_is_valid_port_number(p) for p in port_spec.split("-"))


def _camera_bind_host(camera_port):
    """Host/IP a docker ``-p`` spec binds to; ``""`` means all interfaces."""
    parts = camera_port.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:-2]).strip("[]")
    return ""


def _bind_is_loopback(host):
    return host.startswith("127.") or host in ("localhost", "::1")


def streamer_is_allowed(settings, args=None) -> bool:
    """True only when the user opted into the unpinned Docker streamer.

    ``camera_direct_only`` still forbids the streamer even if
    ``camera_allow_streamer`` or ``--allow-camera-streamer`` is set.
    """
    if getattr(settings, "camera_direct_only", False):
        return False
    if args is not None and _namespace_get(args, "allow_camera_streamer", False):
        return True
    return bool(getattr(settings, "camera_allow_streamer", False))


def _streamer_refused_message(settings, fallback_reason: str) -> str:
    reason = f" ({fallback_reason})" if fallback_reason else ""
    if getattr(settings, "camera_direct_only", False):
        return (
            f"Direct camera grab produced no frame{reason} and camera_direct_only is set, so "
            "the Docker streamer was refused (the streamer ignores cert_fingerprint). "
            "X1-series printers require the streamer: remove camera_direct_only and set "
            "camera_allow_streamer in config (or pass --allow-camera-streamer)."
        )
    return (
        f"Direct camera grab produced no frame{reason}. The Docker streamer is opt-in "
        "because it does not honour cert_fingerprint. X1-series printers need it: set "
        "camera_allow_streamer in config.json, or pass --allow-camera-streamer."
    )


def _warn_if_running_bind_exposed(ctx, run):
    """Warn if an already-running streamer publishes on a non-loopback interface."""
    try:
        out = run(
            ["docker", "inspect", "-f", "{{json .NetworkSettings.Ports}}", ctx.settings.camera_container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return
        ports = json.loads((out.stdout or "").strip() or "null")
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, TypeError):
        return
    if not isinstance(ports, dict):
        return
    exposed = set()
    for binds in ports.values():
        for bind in binds or []:
            host_ip = (bind or {}).get("HostIp", "") if isinstance(bind, dict) else ""
            if not _bind_is_loopback(host_ip.strip("[]")):
                exposed.add(host_ip or "0.0.0.0")
    if exposed:
        name = ctx.settings.camera_container_name
        logger.warning(
            f"The running '{name}' container publishes the camera on non-loopback "
            f"interface(s) {', '.join(sorted(exposed))}; anyone on the network can view it. "
            f"Run 'docker rm -f {name}' to recreate it with the loopback-only camera_port default."
        )


def _require_localhost_streamer_url(args, streamer_url, outpath):
    """Fail closed unless the configured camera streamer URL targets localhost."""
    parsed = urlparse(streamer_url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        message = "Security Error: camera_stream_url must point to localhost."
        safe_log_error(message)
        abort(
            message,
            exit_code=EXIT_CONFIG_ERROR,
            failed_step="validate",
            extra={"output": outpath},
            command="snapshot",
        )


def _write_snapshot_atomic(outpath, data):
    outdir = os.path.dirname(outpath) or "."
    fd, temp_path = tempfile.mkstemp(dir=outdir, suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(temp_path, outpath)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


def cmd_snapshot(
    args,
    ctx=None,
    *,
    grab_frame=None,
    which=None,
    subprocess_run=None,
    access_code_loader=None,
    urlopen=None,
    sleep=None,
    now=None,
):
    """Capture a snapshot from the printer camera.

    P1/A1-class printers are captured directly over the native TLS port-6000
    protocol. The Docker/RTSP streamer is used only when the user opted in.

    Collaborators are injectable so tests pass fakes instead of patching
    module globals.
    """
    _grab = (
        grab_frame
        if grab_frame is not None
        else (lambda printer: _grab_camera_frame_direct(printer, skip_frames=_SNAPSHOT_SKIP_FRAMES))
    )
    _which = which if which is not None else shutil.which
    _run = subprocess_run if subprocess_run is not None else subprocess.run
    _load_code = access_code_loader if access_code_loader is not None else load_access_code
    _urlopen = urlopen if urlopen is not None else urllib.request.urlopen
    _sleep = sleep if sleep is not None else time.sleep

    ctx = ctx or RuntimeContext.for_request(args)

    unique = bool(_namespace_get(args, "unique", False))
    user_output = args.output if hasattr(args, "output") else None
    if unique:
        stamp = _utc_stamp(now)
        if user_output:
            base, ext = os.path.splitext(user_output)
            resolved_output = f"{base}_{stamp}{ext}"
        else:
            resolved_output = f"printer_snapshot_{stamp}.jpg"
    else:
        resolved_output = user_output or "printer_snapshot.jpg"
    outpath = _expand_path(resolved_output)
    if outpath.startswith("-"):
        message = f"Invalid output path: {_path_for_message(outpath)}"
        emit_json_error(args, "snapshot", EXIT_FILE_ERROR, message, failed_step="validate", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_FILE_ERROR)
    try:
        _ensure_parent_dir(outpath)
    except BambuError as e:
        message = f"Could not prepare output path: {_path_for_message(outpath)}"
        emit_json_error(
            args,
            "snapshot",
            (getattr(e, "exit_code", None) or EXIT_FILE_ERROR),
            message,
            failed_step="validate",
            output=outpath,
        )
        raise

    _fallback_reason = ""
    try:
        printer = ctx.printer()
        _frame = _grab(printer)
    except _CameraPinMismatch as _exc:
        message = f"Camera TLS certificate does not match pinned fingerprint: {_exc}"
        emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="grab", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except ssl.SSLError as _exc:
        if not printer.insecure_tls and printer.cert_fingerprint:
            message = (
                "Camera TLS error with a cert pin configured "
                f"(refusing to fall back to the unverified Docker streamer): {_exc}"
            )
            emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="grab", output=outpath)
            safe_log_error(message)
            abort("", exit_code=EXIT_NETWORK_ERROR)
        _frame = None
        _fallback_reason = str(_exc)
        logger.debug(f"Direct camera grab unavailable ({_exc}).")
    except BambuError:
        raise
    except Exception as _exc:
        _frame = None
        _fallback_reason = str(_exc)
        logger.debug(f"Direct camera grab unavailable ({_exc}).")
    if _frame:
        try:
            _write_snapshot_atomic(outpath, _frame)
            size = os.path.getsize(outpath)
        except OSError as _exc:
            message = f"Could not write snapshot: {_path_for_message(outpath)}: {_exception_for_message(_exc)}"
            emit_json_error(args, "snapshot", EXIT_FILE_ERROR, message, failed_step="capture", output=outpath)
            safe_log_error(message)
            abort("", exit_code=EXIT_FILE_ERROR)
        captured_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sha256 = hashlib.sha256(_frame).hexdigest()
        logger.info(f"\U0001f4f8 Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Snapshot

            emit_json(
                Snapshot(
                    status="saved",
                    command="snapshot",
                    output=outpath,
                    size_bytes=size,
                    captured_at=captured_at,
                    sha256=sha256,
                    method="direct",
                )
            )
        return

    if not streamer_is_allowed(ctx.settings, args):
        message = _streamer_refused_message(ctx.settings, _fallback_reason)
        emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="grab", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_NETWORK_ERROR)

    streamer_url = ctx.settings.camera_stream_url
    camera_image = ctx.settings.camera_image

    _require_localhost_streamer_url(args, streamer_url, outpath)

    if not _which("docker"):
        message = "Docker not found in PATH. Install Docker Desktop (Windows/macOS) or docker-ce (Linux) and retry."
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_CONFIG_ERROR)

    camera_port = ctx.settings.camera_port
    if not _camera_port_is_valid(camera_port):
        message = (
            f"Invalid camera_port {camera_port!r}: expected docker port form "
            "[HOST:]HOSTPORT:CONTAINERPORT (e.g. 127.0.0.1:1985:1984)."
        )
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    config_exposed = not _bind_is_loopback(_camera_bind_host(camera_port))
    if config_exposed:
        logger.warning(
            f"camera_port {camera_port!r} publishes the printer camera on a non-loopback "
            f"interface ({_camera_bind_host(camera_port) or 'all interfaces (0.0.0.0)'}); "
            "anyone on the network can view it. Set camera_port to '127.0.0.1:1985:1984' "
            "to restrict it to this machine."
        )
    try:
        check = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", ctx.settings.camera_container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        message = f"Docker not reachable (is the daemon running?): {e}"
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        safe_log_error(message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if check.returncode != 0 or "true" not in check.stdout:
        logger.info("🔄 Starting camera streamer...")
        access_code = _load_code()
        docker_env = {**os.environ, "PRINTER_ACCESS_CODE": access_code}
        try:
            _run(["docker", "rm", "-f", ctx.settings.camera_container_name], capture_output=True, timeout=5)
            run = _run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    ctx.settings.camera_container_name,
                    "-p",
                    ctx.settings.camera_port,
                    "-e",
                    f"PRINTER_ADDRESS={ctx.settings.printer_ip}",
                    "-e",
                    "PRINTER_ACCESS_CODE",
                    camera_image,
                ],
                capture_output=True,
                timeout=10,
                env=docker_env,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            message = f"Docker not reachable (is the daemon running?): {e}"
            emit_json_error(
                args,
                "snapshot",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="docker",
                output=outpath,
                camera_image=camera_image,
            )
            safe_log_error(message)
            abort("", exit_code=EXIT_CONFIG_ERROR)
        if run.returncode != 0:
            detail = run.stderr or run.stdout or "unknown Docker error"
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            if access_code:
                detail = detail.replace(access_code, "<redacted>")
            if ctx.settings.printer_ip:
                detail = detail.replace(ctx.settings.printer_ip, "<redacted>")
            message = f"Could not start camera streamer Docker container using image {camera_image}: {detail.strip()}"
            emit_json_error(
                args,
                "snapshot",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="docker",
                output=outpath,
                camera_image=camera_image,
            )
            safe_log_error(message)
            logger.info("   Build the BambuP1Streamer image locally or set `camera_image` in config.json.")
            abort("", exit_code=EXIT_CONFIG_ERROR)

        req = urllib.request.Request(streamer_url, headers={"User-Agent": "Mozilla/5.0"})
        for _ in range(30):
            try:
                with _urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        break
            except urllib.error.URLError:
                pass
            _sleep(0.5)
    elif not config_exposed:
        _warn_if_running_bind_exposed(ctx, _run)

    logger.info("📸 Capturing snapshot...")
    try:
        req = urllib.request.Request(streamer_url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            data = resp.read()
            _write_snapshot_atomic(outpath, data)
        size = os.path.getsize(outpath)
        captured_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sha256 = hashlib.sha256(data).hexdigest()
        logger.info(f"✅ Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Snapshot

            emit_json(
                Snapshot(
                    status="saved",
                    command="snapshot",
                    output=outpath,
                    size_bytes=size,
                    captured_at=captured_at,
                    sha256=sha256,
                    camera_image=camera_image,
                    docker_container="bambu_camera",
                )
            )
    except urllib.error.URLError as e:
        message = f"Snapshot network error: {e}"
        emit_json_error(
            args,
            "snapshot",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="streamer",
            output=outpath,
            camera_image=camera_image,
        )
        safe_log_error(message)
        logger.info(f"   Make sure the {camera_image} Docker container is running and reachable.")
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except OSError as e:
        message = f"Snapshot file error: {_exception_for_message(e)}"
        emit_json_error(
            args, "snapshot", EXIT_FILE_ERROR, message, failed_step="capture", output=outpath, camera_image=camera_image
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_FILE_ERROR)
    except BambuError:
        raise
    except Exception as e:
        message = f"Snapshot failed: {_exception_for_message(e)}"
        emit_json_error(
            args,
            "snapshot",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="capture",
            output=outpath,
            camera_image=camera_image,
        )
        safe_log_error(message)
        abort("", exit_code=EXIT_COMMAND_ERROR)
