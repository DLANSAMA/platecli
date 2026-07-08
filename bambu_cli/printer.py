import contextlib
import ftplib
import logging
import os
import random
import ssl
import time
from typing import Any, Optional

from bambu_cli.protocols import ftps as ftps_protocol
from bambu_cli.protocols import mqtt as mqtt_protocol

logger = logging.getLogger("bambu.printer")


class BambuPrinter:
    """
    A unified client for communicating with a Bambu Lab 3D printer over local network (MQTT & FTPS).
    """

    def __init__(
        self,
        ip: str,
        serial: str,
        access_code: str,
        insecure_tls: bool = False,
        cert_fingerprint: Optional[str] = None,
        simulation_mode: bool = False,
    ):
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
        self.insecure_tls = insecure_tls
        self.cert_fingerprint = cert_fingerprint
        self.simulation_mode = simulation_mode

        # Network timeouts
        self.mqtt_timeout = 5.0
        self.ftps_timeout = 15.0

    def send_command(self, payload: str, timeout: Optional[float] = None, retries: int = 2) -> bool:
        """Send a JSON command payload via MQTT."""
        return mqtt_protocol.send_command(self, payload, timeout=timeout, retries=retries)

    def status(self, timeout: Optional[float] = None, retries: int = 2) -> Optional[dict[str, Any]]:
        """Get the printer status via MQTT."""
        return mqtt_protocol.get_status(self, timeout=timeout, retries=retries)

    @contextlib.contextmanager
    def get_ftp_client(self, timeout: Optional[float] = None):
        """Context manager to get a connected FTP client."""
        if timeout is None:  # pragma: no cover -- default timeout branch
            timeout = self.ftps_timeout
        # We can implement pooling here in the future
        client = ftps_protocol._create_raw_ftp(self, timeout=timeout)
        try:
            yield client
        finally:
            try:
                client.quit()
            except (*ftplib.all_errors, ssl.SSLError):  # pragma: no cover -- quiet close
                pass
            try:
                client.close()
            except (*ftplib.all_errors, ssl.SSLError):  # pragma: no cover -- quiet close
                pass

    def _probe_remote_size(self, ftp, remote_path: str) -> Optional[int]:
        """Best-effort remote file size lookup. Returns None if unavailable."""
        try:
            size = ftp.size(remote_path)
            return int(size) if size is not None else None
        except (*ftplib.all_errors, ssl.SSLError, TypeError, ValueError):
            return None

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, in seconds."""
        base = min(5 * (2**attempt), 30)
        return base + random.uniform(0, base * 0.25)

    def _delete_remote_quiet(self, ftp, remote_path: str) -> None:
        try:
            ftp.delete(remote_path)
        except ftplib.all_errors:  # pragma: no cover -- best-effort delete
            pass

    def upload_file(  # pragma: no cover -- FTPS resume state machine; unit tests cover paths
        self,
        local_path: str,
        remote_path: str,
        timeout: Optional[float] = None,
        progress_callback=None,
        on_resume=None,
        sleep=time.sleep,
    ) -> bool:
        """Upload a file via FTPS as a verified state machine: (fresh|probe) -> (resume|restart) -> transfer -> verify."""
        filesize = os.path.getsize(local_path)
        max_retries = 3
        uploaded_bytes = 0
        attempted_transfer = False

        for attempt in range(max_retries + 1):
            try:
                with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp:
                    if attempt == 0:
                        self._delete_remote_quiet(ftp, remote_path)
                    with open(local_path, "rb") as f:
                        if uploaded_bytes > 0:
                            logger.info(f"🔄 Resuming from {uploaded_bytes // 1024}KB...")
                            if on_resume:
                                on_resume(uploaded_bytes)
                            f.seek(uploaded_bytes)
                        attempted_transfer = True
                        ftp.storbinary(
                            f"STOR {remote_path}",
                            f,
                            blocksize=1048576,
                            rest=uploaded_bytes if uploaded_bytes > 0 else None,
                            callback=progress_callback,
                        )

                    # Transfer completed without raising; verify before trusting it.
                    remote_size = self._probe_remote_size(ftp, remote_path)
                    if remote_size is None:
                        # Server doesn't support SIZE (or it failed) after a successful STOR.
                        # Don't turn flaky SIZE support into a new failure mode.
                        logger.warning(f"⚠️ Could not verify remote size for {remote_path}; assuming upload succeeded.")
                        return True
                    if remote_size == filesize:
                        return True

                    # Sizes disagree even though STOR didn't raise; treat as a failed
                    # attempt and retry rather than declaring success on garbage data.
                    logger.warning(
                        f"⚠️ Upload attempt {attempt + 1} failed: size mismatch (remote {remote_size}, expected {filesize})"
                    )
                    if remote_size < filesize:
                        uploaded_bytes = remote_size
                    else:
                        # Impossible state (remote larger than local); restart from zero.
                        self._delete_remote_quiet(ftp, remote_path)
                        uploaded_bytes = 0
                    if attempt < max_retries:
                        delay = self._backoff_delay(attempt)
                        logger.info(f"   Retrying in {delay:.1f}s...")
                        sleep(delay)
                        continue
                    else:
                        logger.error(f"Upload failed: size mismatch after {max_retries + 1} attempts")
                        return False
            except ftplib.error_perm as e:
                # Permanent errors (530 bad access code, 550 permission denied)
                # will not succeed on retry; fail fast with a clear message.
                code = str(e)[:3]
                if code == "530":
                    logger.error(f"Upload failed: printer rejected login ({e}). Check your access code.")
                else:
                    logger.error(f"Upload failed: {e}")
                return False
            except (*ftplib.all_errors, ssl.SSLError) as e:
                if attempt < max_retries:
                    logger.warning(f"⚠️ Upload attempt {attempt + 1} failed: {e}")
                    # Probe remote size to decide whether to resume, restart, or shortcut.
                    try:
                        with self.get_ftp_client(timeout=5) as ftp_check:
                            remote_size = self._probe_remote_size(ftp_check, remote_path)
                            if remote_size is not None:
                                if remote_size == filesize:
                                    if attempted_transfer:
                                        logger.info(
                                            f"✅ Uploaded {remote_path} ({filesize // 1024}KB, verified remotely)"
                                        )
                                        return True
                                    # Same-size remote file but we haven't actually
                                    # transferred anything yet this run (e.g. the
                                    # attempt-0 delete failed silently) — don't trust
                                    # a stale file; restart the transfer from zero.
                                    self._delete_remote_quiet(ftp_check, remote_path)
                                    uploaded_bytes = 0
                                elif remote_size > filesize:
                                    # Impossible state; restart from zero.
                                    self._delete_remote_quiet(ftp_check, remote_path)
                                    uploaded_bytes = 0
                                else:
                                    uploaded_bytes = remote_size
                    except (*ftplib.all_errors, ssl.SSLError):
                        pass
                    delay = self._backoff_delay(attempt)
                    logger.info(f"   Retrying in {delay:.1f}s...")
                    sleep(delay)
                else:
                    logger.error(f"Upload failed: {e}")
                    return False
        return False

    def download_file(
        self, remote_path: str, local_path: str, timeout: Optional[float] = None, progress_callback=None
    ) -> bool:
        """Download a file via FTPS into a temp sibling, then atomically replace
        ``local_path``. A dropped/failed transfer therefore never truncates or
        corrupts an existing file at ``local_path``."""
        import tempfile

        directory = os.path.dirname(os.path.abspath(local_path)) or "."
        partial_fd, partial_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(local_path) or 'download'}.", suffix=".part", dir=directory
        )
        try:
            with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp, os.fdopen(partial_fd, "wb") as f:
                partial_fd = None  # ownership transferred to the file object
                ftp.retrbinary(f"RETR {remote_path}", f.write, blocksize=1048576)
            os.replace(partial_path, local_path)
            return True
        except (*ftplib.all_errors, ssl.SSLError) as e:
            logger.error(f"Download failed: {e}")
            if partial_fd is not None:  # pragma: no cover -- fd not yet transferred
                with contextlib.suppress(OSError):
                    os.close(partial_fd)
            with contextlib.suppress(OSError):
                os.remove(partial_path)
            return False

    def delete_file(self, remote_path: str, timeout: Optional[float] = None) -> bool:
        """Delete a file from the printer via FTPS."""
        try:
            with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp:
                ftp.delete(remote_path)
            return True
        except (*ftplib.all_errors, ssl.SSLError) as e:
            logger.error(f"Delete failed: {e}")
            return False

    def list_files(self, remote_dir: str = "/model/", timeout: Optional[float] = None) -> Optional[list]:
        """List files in a remote directory via FTPS."""
        try:
            with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp:
                return ftp.nlst(remote_dir)
        except (*ftplib.all_errors, ssl.SSLError) as e:
            logger.error(f"List files failed: {e}")
            return None

    def get_version(self, timeout: Optional[float] = 5.0, retries: int = 1) -> Optional[list]:
        """Get version info via MQTT."""
        return mqtt_protocol.get_version(self, timeout=timeout, retries=retries)


def get_printer() -> BambuPrinter:
    """Factory: build a BambuPrinter from the active run's settings."""
    from bambu_cli import bambu
    from bambu_cli.context import _normalize_fingerprint, current_settings, current_simulation

    settings = current_settings()
    simulation_mode = current_simulation()
    return BambuPrinter(
        ip=settings.printer_ip,
        serial=settings.serial,
        # Simulation mode never talks to a real printer, so it must not
        # require credentials (load_access_code exits when unconfigured).
        access_code="" if simulation_mode else bambu.load_access_code(),
        insecure_tls=settings.insecure_tls,
        cert_fingerprint=_normalize_fingerprint(settings.cert_fingerprint),
        simulation_mode=simulation_mode,
    )
