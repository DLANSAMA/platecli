import ftplib
import os
import socket
import ssl
from typing import Any

from bambu_cli.utils import _resolve_ip

_SIM_FTP_FILES = {"simulated_file.3mf": 1000}


class _SimFtp:
    """Small FTPS stand-in for --sim without importing test-only mocks."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def nlst(self, path=None):
        return sorted(_SIM_FTP_FILES)

    def size(self, path):
        filename = os.path.basename(path)
        if filename not in _SIM_FTP_FILES:
            raise ftplib.error_perm("550 file not found")
        return _SIM_FTP_FILES[filename]

    def storbinary(self, command, fp, blocksize=8192, rest=None, callback=None):
        _, _, remote_path = command.partition(" ")
        filename = os.path.basename(remote_path)
        current = fp.tell()
        fp.seek(0, os.SEEK_END)
        size = fp.tell()
        fp.seek(current)
        _SIM_FTP_FILES[filename] = size

        # Simulate upload progress blocks
        if callback:
            callback(b"\x00" * size)

    def delete(self, path):
        _SIM_FTP_FILES.pop(os.path.basename(path), None)

    def voidcmd(self, cmd):
        return "200 OK"

    def quit(self):
        pass

    def close(self):
        pass


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (Bambu printers use port 990).

    ``create_connection`` and ``ssl_context_cls`` are injectable so tests pass
    fakes instead of patching module globals.
    """

    # Attached after construction by _create_raw_ftp for pin / insecure_tls.
    printer: Any = None

    def connect(
        self,
        host="",
        port=990,
        timeout=-999,
        source_address=None,
        *,
        create_connection=None,
        ssl_context_cls=None,
    ):
        _connect = create_connection if create_connection is not None else socket.create_connection
        _SSLContext = ssl_context_cls if ssl_context_cls is not None else ssl.SSLContext
        if host != "":
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = _connect((self.host, self.port), self.timeout)
        self.af = self.sock.family
        try:
            ctx = _SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            printer = self.printer
            pin = printer.cert_fingerprint if printer is not None else None
            if pin or (printer is not None and printer.insecure_tls):
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            else:
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
                ctx.load_default_certs()
            self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)
            if pin:
                from bambu_cli.tlspin import verify_cert_fingerprint

                peer_der = self.sock.getpeercert(binary_form=True)
                verify_cert_fingerprint(peer_der, pin)
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
        except Exception:
            if hasattr(self, "file") and self.file:
                try:
                    self.file.close()
                except Exception:
                    pass
            try:
                self.sock.close()
            except Exception:
                pass
            raise
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        secure = getattr(self, "_secure_data", False) or getattr(self, "_prot_p", False)
        if secure and isinstance(self.sock, ssl.SSLSocket):
            session = self.sock.session
            conn = self.sock.context.wrap_socket(conn, server_hostname=self.host, session=session)
            printer = self.printer
            pin = printer.cert_fingerprint if printer is not None else None
            if pin:
                from bambu_cli.tlspin import verify_cert_fingerprint

                peer_der = conn.getpeercert(binary_form=True)
                try:
                    verify_cert_fingerprint(peer_der, pin)
                except ssl.SSLError:
                    # Close the data socket before re-raising so a pin failure
                    # does not leak the FD (no try/finally around wrap otherwise).
                    try:
                        conn.close()
                    except OSError:
                        pass
                    raise
            # Bambu firmware never answers the TLS close-notify on the data
            # channel, so ftplib's storbinary/retrbinary hang in
            # conn.unwrap() until the socket times out (and then treat the
            # completed transfer as failed). Skip the shutdown handshake;
            # the control-channel 226 already confirms the transfer.
            conn.unwrap = lambda: conn  # type: ignore[method-assign]
        return conn, size


def _create_raw_ftp(printer, timeout=60):
    """Connect to printer's FTPS server."""
    if printer.simulation_mode:
        from bambu_cli.logging_utils import logger

        logger.info("🤖 [SIM] Connecting to simulated FTPS server...")
        return _SimFtp()

    # Real FTPS handshake is covered by TLS pin unit tests via ImplicitFTPS mocks.
    resolved_ip = _resolve_ip(printer.ip)  # pragma: no cover -- live FTPS connect
    ftp = ImplicitFTPS()
    ftp.printer = printer
    ftp.connect(resolved_ip, 990, timeout=timeout)
    ftp.login("bblp", printer.access_code)
    ftp.prot_p()
    return ftp
