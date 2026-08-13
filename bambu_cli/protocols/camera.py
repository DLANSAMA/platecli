"""Direct P1/A1 port-6000 TLS camera grab.

This module is the transport: open a pinned (or explicitly insecure) TLS
socket, authenticate, and return JPEG bytes. Docker, JSON envelopes, and the
``snapshot`` command live in ``bambu_cli.commands.snapshot``.
"""

from __future__ import annotations

import socket
import ssl
import struct

from bambu_cli.constants import EXIT_NETWORK_ERROR
from bambu_cli.errors import BambuError

# The port-6000 camera stream's first frames can be stale (buffered from a
# previous connection); skip a few so the snapshot reflects the current scene.
_SNAPSHOT_SKIP_FRAMES = 5


class _CameraPinMismatch(BambuError):
    """The camera TLS cert does not match the pinned ``cert_fingerprint``.

    A pinned fingerprint is an explicit security control, so a mismatch must
    hard-abort rather than fall back to the Docker streamer (which would
    connect to the printer without honoring the pin).
    """

    exit_code = EXIT_NETWORK_ERROR
    failed_step = "grab"


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
    None if no frame is obtained. Requires no Docker. X1-series use RTSP instead.

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

        if not printer.insecure_tls and printer.cert_fingerprint:
            from bambu_cli.tlspin import verify_cert_fingerprint

            der = tls.getpeercert(binary_form=True)
            verify_cert_fingerprint(der, printer.cert_fingerprint, exc_factory=_CameraPinMismatch)
        elif not printer.insecure_tls and not printer.cert_fingerprint:
            raise ssl.SSLError(
                "No cert_fingerprint pinned for camera connection; run 'plate setup' to pin one, or set insecure_tls to bypass (not recommended)"
            )

        tls.sendall(bytes(auth))
        valid_frames_count = 0
        last_frame = None
        for _ in range(30):
            hdr = _recv_exact(tls, 16)
            size = int.from_bytes(hdr[0:4], "little")
            if size <= 0:
                continue
            if size > 12_000_000:
                break
            data = _recv_exact(tls, size)
            if data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9":
                last_frame = bytes(data)
                valid_frames_count += 1
                if valid_frames_count > skip_frames:
                    return last_frame
        return last_frame
    finally:
        closer = tls if tls is not None else sock
        try:
            closer.close()
        except Exception:
            pass
