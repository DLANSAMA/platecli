import contextlib
import ftplib
import logging
import os
import ssl
import time
from typing import Optional, Dict, Any

from bambu_cli.protocols import mqtt as mqtt_protocol
from bambu_cli.protocols import ftps as ftps_protocol

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

    def status(self, timeout: Optional[float] = None, retries: int = 2) -> Optional[Dict[str, Any]]:
        """Get the printer status via MQTT."""
        return mqtt_protocol.get_status(self, timeout=timeout, retries=retries)

    @contextlib.contextmanager
    def get_ftp_client(self, timeout: Optional[float] = None):
        """Context manager to get a connected FTP client."""
        if timeout is None:
            timeout = self.ftps_timeout
        # We can implement pooling here in the future
        client = ftps_protocol._create_raw_ftp(self, timeout=timeout)
        try:
            yield client
        finally:
            try:
                client.quit()
            except (*ftplib.all_errors, ssl.SSLError):
                pass
            try:
                client.close()
            except (*ftplib.all_errors, ssl.SSLError):
                pass

    def upload_file(self, local_path: str, remote_path: str, timeout: Optional[float] = None, progress_callback=None, on_resume=None) -> bool:
        """Upload a file via FTPS."""
        filesize = os.path.getsize(local_path)
        max_retries = 3
        uploaded_bytes = 0

        for attempt in range(max_retries + 1):
            try:
                with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp:
                    if attempt == 0:
                        try:
                            ftp.delete(remote_path)
                        except ftplib.all_errors:
                            pass
                    with open(local_path, 'rb') as f:
                        if uploaded_bytes > 0:
                            logger.info(f"🔄 Resuming from {uploaded_bytes // 1024}KB...")
                            if on_resume:
                                on_resume(uploaded_bytes)
                            f.seek(uploaded_bytes)
                        ftp.storbinary(f'STOR {remote_path}', f, blocksize=1048576, rest=uploaded_bytes if uploaded_bytes > 0 else None, callback=progress_callback)
                    return True
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
                    # Attempt to get remote size for resume
                    try:
                        with self.get_ftp_client(timeout=5) as ftp_check:
                            size = ftp_check.size(remote_path)
                            remote_size = int(size) if size is not None else 0
                            if remote_size == filesize:
                                logger.info(f"✅ Uploaded {remote_path} ({filesize // 1024}KB, verified remotely)")
                                return True
                            uploaded_bytes = remote_size
                    except (*ftplib.all_errors, ssl.SSLError):
                        pass
                    logger.info("   Retrying in 5s...")
                    time.sleep(5)
                else:
                    logger.error(f"Upload failed: {e}")
                    return False
        return False

    def download_file(self, remote_path: str, local_path: str, timeout: Optional[float] = None, progress_callback=None) -> bool:
        """Download a file via FTPS."""
        # Simple download implementation without resume for now
        try:
            with self.get_ftp_client(timeout=timeout or self.ftps_timeout) as ftp:
                with open(local_path, 'wb') as f:
                    ftp.retrbinary(f'RETR {remote_path}', f.write, blocksize=1048576)
                return True
        except (*ftplib.all_errors, ssl.SSLError) as e:
            logger.error(f"Download failed: {e}")
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

    def list_files(self, remote_dir: str = '/model/', timeout: Optional[float] = None) -> Optional[list]:
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
    """Factory method to get a BambuPrinter instance based on current global config."""
    from bambu_cli import bambu
    simulation_mode = bambu.SIMULATION_MODE
    return BambuPrinter(
        ip=bambu.PRINTER_IP,
        serial=bambu.SERIAL,
        # Simulation mode never talks to a real printer, so it must not
        # require credentials (load_access_code exits when unconfigured).
        access_code="" if simulation_mode else bambu.load_access_code(),
        insecure_tls=bambu.INSECURE_TLS,
        cert_fingerprint=bambu._expected_fingerprint(),
        simulation_mode=simulation_mode,
    )
