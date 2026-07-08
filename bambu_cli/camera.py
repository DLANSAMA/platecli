"""Camera snapshot capture: direct P1/A1 port-6000 TLS grab with a
BambuP1Streamer Docker fallback for X1-series printers."""

import os
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


def _grab_camera_frame_direct(printer, timeout=12):  # pragma: no cover -- native TLS frame protocol; pin mismatch unit-tested
    """Grab one JPEG frame from a P1/A1 printer camera using Bambu's native TLS
    port-6000 protocol (the same one Bambu Studio uses). Returns JPEG bytes, or
    None if no frame is obtained. Requires no Docker. X1-series use RTSP instead,
    so callers should fall back to the Docker/RTSP streamer when this returns None."""
    if not printer.ip or not printer.access_code:
        return None
    auth = bytearray()
    auth += struct.pack("<I", 0x40)
    auth += struct.pack("<I", 0x3000)
    auth += struct.pack("<I", 0x0)
    auth += struct.pack("<I", 0x0)
    auth += "bblp".encode("ascii").ljust(32, b"\x00")
    auth += printer.access_code.encode("ascii").ljust(32, b"\x00")
    ctx = ssl.create_default_context()
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

    sock = socket.create_connection((printer.ip, 6000), timeout=timeout)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=printer.ip)
        tls.settimeout(timeout)

        # TLS Verification
        if not printer.insecure_tls and printer.cert_fingerprint:
            der = tls.getpeercert(binary_form=True)
            from bambu_cli.config import fingerprint_sha256

            actual = fingerprint_sha256(der)
            if actual.lower() != printer.cert_fingerprint.lower():
                raise ssl.SSLError(
                    f"Certificate fingerprint mismatch: expected {printer.cert_fingerprint}, got {actual}"
                )
        elif not printer.insecure_tls and not printer.cert_fingerprint:
            raise ssl.SSLError(
                "No cert_fingerprint pinned for camera connection; run 'bambu-cli setup' to pin one, or set insecure_tls to bypass (not recommended)"
            )

        tls.sendall(bytes(auth))
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
                return bytes(data)
        return None
    finally:
        try:
            sock.close()
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


def _cmd_snapshot(args, ctx=None):  # pragma: no cover -- docker+direct hybrid; pin paths unit-tested
    """Capture a snapshot from the printer camera via BambuP1Streamer."""
    from bambu_cli import bambu

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
        _frame = bambu._grab_camera_frame_direct(printer)
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
    if not shutil.which("docker"):
        message = "Docker not found in PATH. Install Docker Desktop (Windows/macOS) or docker-ce (Linux) and retry."
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        check = subprocess.run(
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
        access_code = bambu.load_access_code()
        # Pass the access code via the child environment (the `-e NAME` form with
        # no value tells docker to read it from our env) rather than embedding it
        # in argv, so the secret never appears in the process list (`ps`).
        docker_env = {**os.environ, "PRINTER_ACCESS_CODE": access_code}
        try:
            subprocess.run(["docker", "rm", "-f", ctx.settings.camera_container_name], capture_output=True, timeout=5)
            run = subprocess.run(
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
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        break
            except urllib.error.URLError:
                pass
            time.sleep(0.5)

    logger.info("📸 Capturing snapshot...")
    try:
        # streamer_url was already validated as localhost-only before polling.
        req = urllib.request.Request(streamer_url, headers={"User-Agent": "Mozilla/5.0"})
        # Use standard urlopen for localhost streamer to bypass SSRF protections
        with urllib.request.urlopen(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
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
