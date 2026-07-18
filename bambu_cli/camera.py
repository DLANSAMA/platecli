"""Camera snapshot capture: direct P1/A1 port-6000 TLS grab with a
BambuP1Streamer Docker fallback for X1-series printers.

Collaborators (socket connect, SSL context factory, frame grabber, docker
runner, docker-which, access-code loader) are injectable so tests pass fakes
instead of patching module globals.
"""

import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.cli import (
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
)
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
from bambu_cli.logging_utils import logger
from bambu_cli.utils import _ensure_parent_dir, emit_json, emit_json_error

# The port-6000 camera stream's first frames can be stale (buffered from a
# previous connection); skip a few so the snapshot reflects the current scene.
_SNAPSHOT_SKIP_FRAMES = 5


class _CameraPinMismatch(BambuError):
    """The camera TLS cert does not match the pinned ``cert_fingerprint``.

    A pinned fingerprint is an explicit security control, so a mismatch must
    hard-abort rather than fall back to the Docker streamer path (which would
    connect to the printer without honoring the pin — a silent downgrade). This
    is distinct from a missing pin or an ordinary connection failure, both of
    which legitimately fall through to the streamer.
    """

    exit_code = EXIT_NETWORK_ERROR
    failed_step = "grab"


# Container-port token of a docker ``-p`` spec: a port (or range) plus optional
# protocol suffix, e.g. ``1984``, ``1984/tcp``, ``1984-1989/udp``. The digit
# groups are only bounded to 1-5 characters here; the actual 1-65535 range
# check happens in `_camera_port_is_valid` since a regex can't express it
# cleanly (and \d{1,5} alone lets 99999 through).
_CONTAINER_PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?(/(tcp|udp|sctp))?$", re.IGNORECASE)


def _is_valid_port_number(token):
    """True if ``token`` is a decimal integer in the valid TCP/UDP port range
    (1-65535)."""
    try:
        return 1 <= int(token) <= 65535
    except ValueError:
        return False


def _camera_port_is_valid(camera_port):
    """True if ``camera_port`` is a usable docker ``-p`` value. Only the
    container port (the last colon field) is strictly checked; the optional host
    IP/port fields are left for docker to validate (and go list-form into argv,
    so there is no injection risk). Turns a confusing docker error into a clear
    config error for the common typo cases (empty value, missing container port,
    or a port number outside 1-65535).
    """
    if not camera_port:
        return False
    container = camera_port.split(":")[-1]
    match = _CONTAINER_PORT_RE.match(container)
    if not match:
        return False
    port_spec = container.split("/", 1)[0]
    return all(_is_valid_port_number(p) for p in port_spec.split("-"))


def _camera_bind_host(camera_port):
    """Host/IP a docker ``-p`` spec binds to; ``""`` means all interfaces.

    Form is ``[HOST:]HOSTPORT:CONTAINERPORT``, so the host is everything before
    the last two colon fields (handles bracketed IPv6, which is then unbracketed).
    """
    parts = camera_port.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:-2]).strip("[]")
    return ""


def _bind_is_loopback(host):
    return host.startswith("127.") or host in ("localhost", "::1")


def _warn_if_running_bind_exposed(ctx, run):
    """Warn if an already-running streamer container publishes the camera on a
    non-loopback interface — e.g. a container created before the loopback-only
    default, whose binding only changes when the container is recreated. Purely
    best-effort: any inspect/parse failure is ignored (it is only a warning).
    """
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


def _grab_camera_frame_direct(
    printer,
    timeout=12,
    *,
    create_connection=None,
    ssl_context_factory=None,
    skip_frames=0,
):
    """Grab one JPEG frame from a P1/A1 printer camera using Bambu's native TLS
    port-6000 protocol (the same one Bambu Studio uses). Returns JPEG bytes, or
    None if no frame is obtained. Requires no Docker. X1-series use RTSP instead,
    so callers should fall back to the Docker/RTSP streamer when this returns None.

    ``create_connection`` and ``ssl_context_factory`` default to the real
    ``socket.create_connection`` / ``ssl.create_default_context``; tests inject
    fakes instead of patching module globals.
    """
    _connect = create_connection if create_connection is not None else socket.create_connection
    _ssl_factory = ssl_context_factory if ssl_context_factory is not None else ssl.create_default_context
    if not printer.ip or not printer.access_code:
        return None
    auth = bytearray()
    auth += struct.pack("<I", 0x40)
    auth += struct.pack("<I", 0x3000)
    auth += struct.pack("<I", 0x0)
    auth += struct.pack("<I", 0x0)
    auth += "bblp".encode("ascii").ljust(32, b"\x00")
    auth += printer.access_code.encode("ascii").ljust(32, b"\x00")
    ctx = _ssl_factory()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def _recv_exact(sock_, n):
        buf = b""
        while len(buf) < n:
            c = sock_.recv(n - len(buf))
            if not c:
                raise EOFError("camera socket closed")
            buf += c
        return buf

    sock = _connect((printer.ip, 6000), timeout=timeout)
    tls = None
    try:
        tls = ctx.wrap_socket(sock, server_hostname=printer.ip)
        tls.settimeout(timeout)

        # TLS Verification
        if not printer.insecure_tls and printer.cert_fingerprint:
            der = tls.getpeercert(binary_form=True)
            from bambu_cli.config import fingerprint_sha256

            actual = fingerprint_sha256(der)
            if actual.lower() != printer.cert_fingerprint.lower():
                raise _CameraPinMismatch(
                    f"Certificate fingerprint mismatch: expected {printer.cert_fingerprint}, got {actual}"
                )
        elif not printer.insecure_tls and not printer.cert_fingerprint:
            raise ssl.SSLError(
                "No cert_fingerprint pinned for camera connection; run 'bambu-cli setup' to pin one, or set insecure_tls to bypass (not recommended)"
            )

        tls.sendall(bytes(auth))
        valid_frames_count = 0
        last_frame = None
        for _ in range(30):
            hdr = _recv_exact(tls, 16)
            size = int.from_bytes(hdr[0:4], "little")
            if size <= 0:
                # Empty/keepalive frame: nothing to drain, just read the next header.
                continue
            if size > 12_000_000:
                # Implausible frame length means the stream is desynced — the body
                # we'd skip would be misread as the next header, so every later
                # iteration reads garbage. Abandon the direct grab and let the
                # caller fall back to the Docker streamer instead.
                break
            data = _recv_exact(tls, size)
            if data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9":
                last_frame = bytes(data)
                valid_frames_count += 1
                if valid_frames_count > skip_frames:
                    return last_frame
        return last_frame
    finally:
        # wrap_socket() detaches the underlying fd into the SSLSocket, so on the
        # success path closing `sock` is a no-op and the real fd would leak (a
        # ResourceWarning under GC, an fd leak in a long-lived process). Close
        # whichever object still owns the fd: `tls` once wrapped, else `sock`
        # (wrap_socket raised before detaching).
        closer = tls if tls is not None else sock
        try:
            closer.close()
        except Exception:
            pass


def _require_localhost_streamer_url(args, streamer_url, outpath):
    """Fail closed unless the configured camera streamer URL targets localhost.

    Called before any request is issued to the URL (readiness polling included)
    so a misconfigured non-local ``camera_stream_url`` can never trigger
    outbound requests.
    """
    parsed = urlparse(streamer_url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        message = "Security Error: camera_stream_url must point to localhost."
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="validate", output=outpath)
        abort("", exit_code=EXIT_CONFIG_ERROR)


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


def _cmd_snapshot(
    args,
    ctx=None,
    *,
    grab_frame=None,
    which=None,
    subprocess_run=None,
    access_code_loader=None,
    urlopen=None,
    sleep=None,
):
    """Capture a snapshot from the printer camera via BambuP1Streamer.

    Collaborators are injectable so tests pass fakes instead of patching
    module globals. Defaults are the real production implementations.
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
    outpath = _expand_path(args.output or "printer_snapshot.jpg")
    if outpath.startswith("-"):
        message = f"Invalid output path: {_path_for_message(outpath)}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_FILE_ERROR, message, failed_step="validate", output=outpath)
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

    # --- Primary path: direct P1/A1 camera grab (no Docker). Falls through to the
    #     Docker/RTSP streamer below for X1-series or if no frame is obtained. ---
    try:
        printer = ctx.printer()
        _frame = _grab(printer)
    except _CameraPinMismatch as _exc:
        # A pinned fingerprint that does not match is a security failure, not a
        # "this printer needs Docker" signal: fail closed instead of silently
        # falling back to the streamer (which would ignore the pin).
        message = f"Camera TLS certificate does not match pinned fingerprint: {_exc}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="grab", output=outpath)
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except ssl.SSLError as _exc:
        # A TLS handshake failure (e.g. from wrap_socket()) is a normal signal to
        # fall back to Docker when no pin is configured -- but when a pin *is*
        # configured, an attacker able to interfere with the port-6000 handshake
        # could otherwise defeat the pin simply by breaking TLS instead of
        # presenting a mismatched cert. Treat that case the same as a pin
        # mismatch: fail closed, no Docker fallthrough. This deliberately covers
        # post-handshake SSLErrors too (a truncation attack is indistinguishable
        # from a flaky read, and the streamer would be unpinned).
        if not printer.insecure_tls and printer.cert_fingerprint:
            message = f"Camera TLS error with a cert pin configured (refusing to fall back to the unverified Docker streamer): {_exc}"
            logger.error(message)
            emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="grab", output=outpath)
            abort("", exit_code=EXIT_NETWORK_ERROR)
        _frame = None
        logger.debug(f"Direct camera grab unavailable ({_exc}); trying Docker streamer.")
    except Exception as _exc:
        _frame = None
        logger.debug(f"Direct camera grab unavailable ({_exc}); trying Docker streamer.")
    if _frame:
        _write_snapshot_atomic(outpath, _frame)
        size = os.path.getsize(outpath)
        logger.info(f"\U0001f4f8 Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "saved",
                    "command": "snapshot",
                    "output": outpath,
                    "size_bytes": size,
                    "method": "direct",
                }
            )
        return

    streamer_url = ctx.settings.camera_stream_url
    camera_image = ctx.settings.camera_image

    # Fail closed before ANY request is issued to the streamer URL — including
    # the readiness-polling loop below — so a misconfigured non-local
    # camera_stream_url can never trigger outbound (SSRF-shaped) requests.
    _require_localhost_streamer_url(args, streamer_url, outpath)

    # Check if streamer container is running, start if needed
    if not _which("docker"):
        message = "Docker not found in PATH. Install Docker Desktop (Windows/macOS) or docker-ce (Linux) and retry."
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        abort("", exit_code=EXIT_CONFIG_ERROR)

    camera_port = ctx.settings.camera_port
    if not _camera_port_is_valid(camera_port):
        message = (
            f"Invalid camera_port {camera_port!r}: expected docker port form "
            "[HOST:]HOSTPORT:CONTAINERPORT (e.g. 127.0.0.1:1985:1984)."
        )
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
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
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if check.returncode != 0 or "true" not in check.stdout:
        logger.info("🔄 Starting camera streamer...")
        access_code = _load_code()
        # Pass the access code via the child environment (the `-e NAME` form with
        # no value tells docker to read it from our env) rather than embedding it
        # in argv, so the secret never appears in the process list (`ps`).
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
            logger.error(message)
            emit_json_error(
                args,
                "snapshot",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="docker",
                output=outpath,
                camera_image=camera_image,
            )
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
            logger.error(message)
            logger.info("   Build the BambuP1Streamer image locally or set `camera_image` in config.json.")
            emit_json_error(
                args,
                "snapshot",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="docker",
                output=outpath,
                camera_image=camera_image,
            )
            abort("", exit_code=EXIT_CONFIG_ERROR)

        # Polling to wait for stream to connect (up to 15 seconds)
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
        # Container already running: its published port was fixed at creation
        # time, so the loopback-only default only takes effect on recreation.
        # Warn if a pre-existing container is still exposed. Skipped when the
        # configured value already warned above (avoid duplicate noise).
        _warn_if_running_bind_exposed(ctx, _run)

    logger.info("📸 Capturing snapshot...")
    try:
        # streamer_url was already validated as localhost-only before polling.
        req = urllib.request.Request(streamer_url, headers={"User-Agent": "Mozilla/5.0"})
        # Use standard urlopen for localhost streamer to bypass SSRF protections
        with _urlopen(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            data = resp.read()
            _write_snapshot_atomic(outpath, data)
        size = os.path.getsize(outpath)
        logger.info(f"✅ Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "saved",
                    "command": "snapshot",
                    "output": outpath,
                    "size_bytes": size,
                    "camera_image": camera_image,
                    "docker_container": "bambu_camera",
                }
            )
    except urllib.error.URLError as e:
        message = f"Snapshot network error: {e}"
        logger.error(message)
        logger.info(f"   Make sure the {camera_image} Docker container is running and reachable.")
        emit_json_error(
            args,
            "snapshot",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="streamer",
            output=outpath,
            camera_image=camera_image,
        )
        abort("", exit_code=EXIT_NETWORK_ERROR)
    except OSError as e:
        message = f"Snapshot file error: {_exception_for_message(e)}"
        logger.error(message)
        emit_json_error(
            args, "snapshot", EXIT_FILE_ERROR, message, failed_step="capture", output=outpath, camera_image=camera_image
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    except BambuError:
        raise
    except Exception as e:
        message = f"Snapshot failed: {_exception_for_message(e)}"
        logger.error(message)
        emit_json_error(
            args,
            "snapshot",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="capture",
            output=outpath,
            camera_image=camera_image,
        )
        abort("", exit_code=EXIT_COMMAND_ERROR)
