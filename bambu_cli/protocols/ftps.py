import tempfile
import os
import ftplib
import socket
import ssl
import hashlib
import threading
import atexit

from bambu_cli.utils import _resolve_ip
from bambu_cli.logging_utils import mockable

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

    def quit(self):
        pass

    def close(self):
        pass


def _verify_cert_fingerprint(der_cert, host):
    """Raise ssl.SSLError if the presented cert doesn't match the pinned fingerprint."""
    from bambu_cli import bambu
    expected = bambu._expected_fingerprint()
    if not expected:
        return
    actual = bambu.fingerprint_sha256(der_cert)
    if actual is None:
        raise ssl.SSLError(f"cert_fingerprint is set but {host} presented no certificate")
    if actual.lower() != expected:
        raise ssl.SSLError(
            f"Certificate fingerprint mismatch for {host}: "
            f"expected {expected}, got {actual.lower()}")


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (Bambu printers use port 990)."""
    def connect(self, host='', port=990, timeout=-999, source_address=None):
        if host != '': self.host = host
        if port > 0: self.port = port
        if timeout != -999: self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.af = self.sock.family
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            pin = getattr(self, 'printer', None) and self.printer.cert_fingerprint
            if pin or (getattr(self, 'printer', None) and self.printer.insecure_tls):
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            else:
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
                ctx.load_default_certs()
            self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)
            if pin:
                actual = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest().lower()
                if actual != pin.lower():
                    raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {pin.lower()}, got {actual}")
            self.file = self.sock.makefile('r', encoding=self.encoding)
            self.welcome = self.getresp()
        except Exception:
            if hasattr(self, 'file') and self.file:
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
        if secure:
            if isinstance(self.sock, ssl.SSLSocket):
                session = self.sock.session
                conn = self.sock.context.wrap_socket(conn,
                                                     server_hostname=self.host,
                                                     session=session)
                pin = getattr(self, 'printer', None) and self.printer.cert_fingerprint
                if pin:
                    actual = hashlib.sha256(conn.getpeercert(binary_form=True)).hexdigest().lower()
                    if actual != pin.lower():
                        raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {pin.lower()}, got {actual}")
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
            except (*ftplib.all_errors, ssl.SSLError):
                with self._lock:
                    if self._ftp_client is client:
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

    resolved_ip = _resolve_ip(printer.ip)
    ftp = ImplicitFTPS()
    
    # Store settings for use in connect/ntransfercmd
    ftp.printer = printer
    
    ftp.connect(resolved_ip, 990, timeout=timeout)
    ftp.login("bblp", printer.access_code)
    ftp.prot_p()
    return ftp


@mockable
def get_ftp(printer=None, timeout=60):
    return connection_manager.get_ftp(printer, timeout=timeout)
