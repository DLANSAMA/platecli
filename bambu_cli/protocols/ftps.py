import atexit
import ftplib
import hashlib
import os
import socket
import ssl
import tempfile
import threading
from typing import Any

from bambu_cli.utils import _resolve_ip

_SIM_FTP_FILES = {"simulated_file.3mf": 1000}

# Module-level so mypy accepts these in `except` clauses (star-unpack of
# ftplib.all_errors inline is rejected as non-exception-type).
_FTP_ERRORS: tuple[type[BaseException], ...] = ftplib.all_errors
_FTP_SSL_ERRORS: tuple[type[BaseException], ...] = ftplib.all_errors + (ssl.SSLError,)
_FTP_POOL_ERRORS: tuple[type[BaseException], ...] = ftplib.all_errors + (
    ssl.SSLError,
    OSError,
    AttributeError,
)


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
        """Pool health-check (NOOP) used by ConnectionManager.get_ftp."""
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
                peer_der = self.sock.getpeercert(binary_form=True)
                if peer_der is None:
                    raise ssl.SSLError("No peer certificate to verify fingerprint against")
                actual = hashlib.sha256(peer_der).hexdigest().lower()
                if actual != pin.lower():
                    raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {pin.lower()}, got {actual}")
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
                peer_der = conn.getpeercert(binary_form=True)
                if peer_der is None:
                    raise ssl.SSLError("No peer certificate to verify fingerprint against")
                actual = hashlib.sha256(peer_der).hexdigest().lower()
                if actual != pin.lower():
                    # Close the data socket before re-raising so a pin mismatch
                    # does not leak the FD (no try/finally around wrap otherwise).
                    try:
                        conn.close()
                    except OSError:
                        pass
                    raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {pin.lower()}, got {actual}")
            # Bambu firmware never answers the TLS close-notify on the data
            # channel, so ftplib's storbinary/retrbinary hang in
            # conn.unwrap() until the socket times out (and then treat the
            # completed transfer as failed). Skip the shutdown handshake;
            # the control-channel 226 already confirms the transfer.
            conn.unwrap = lambda: conn  # type: ignore[method-assign]
        return conn, size


def _remove_partial_file(path):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _download_partial_path(outpath):
    if not os.path.exists(outpath):
        return outpath, False
    directory = os.path.dirname(outpath) or "."
    basename = os.path.basename(outpath) or "download"
    fd, temp_path = tempfile.mkstemp(prefix=f".{basename}.", suffix=".part", dir=directory)
    os.close(fd)
    return temp_path, True


def _noncolliding_path(path):
    from bambu_cli.cli import _path_for_message

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return path
    except FileExistsError:
        pass

    directory = os.path.dirname(path)
    basename = os.path.basename(path)
    stem, ext = os.path.splitext(basename)
    stem = stem or "download"
    for index in range(1, 1000):
        candidate = os.path.join(directory, f"{stem}-{index}{ext}")
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not find an unused filename near {_path_for_message(path)}")


class PooledFTPWrapper:
    def __init__(self, ftp, manager):
        self._ftp = ftp
        self._manager = manager

    def __getattr__(self, name):
        return getattr(self._ftp, name)

    def __enter__(self):
        self._manager._ftp_usage_lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                with self._manager._lock:
                    if self._manager._ftp_client is self._ftp:
                        self._manager._ftp_client = None
                try:
                    self._ftp.close()
                except Exception:
                    pass
        finally:
            self._manager._ftp_usage_lock.release()


class ConnectionManager:
    """Manages reusable MQTT and FTPS connections to reduce socket churn."""

    def __init__(self):
        self._mqtt_client = None
        self._ftp_client = None
        self._lock = threading.Lock()
        self._ftp_usage_lock = threading.Lock()

    def get_ftp(self, printer=None, timeout=60):
        if printer is None:
            from bambu_cli.printer import get_printer

            printer = get_printer()
        with self._lock:
            client = self._ftp_client
        if client is not None:
            try:
                with self._ftp_usage_lock:
                    client.voidcmd("NOOP")
                return PooledFTPWrapper(client, self)
            except _FTP_POOL_ERRORS:
                with self._lock:
                    if self._ftp_client is client and client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass
                        self._ftp_client = None

        ftp = _create_raw_ftp(printer, timeout=timeout)
        with self._lock:
            self._ftp_client = ftp
            return PooledFTPWrapper(ftp, self)

    def close_all(self):
        self.clear()

    def clear(self):
        with self._lock:
            if self._mqtt_client is not None:
                try:
                    self._mqtt_client.disconnect()
                except Exception:
                    pass
                self._mqtt_client = None
            if self._ftp_client is not None:
                try:
                    self._ftp_client.close()
                except Exception:
                    pass
                self._ftp_client = None


connection_manager = ConnectionManager()
atexit.register(connection_manager.close_all)


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


def get_ftp(printer=None, timeout=60):
    return connection_manager.get_ftp(printer, timeout=timeout)
