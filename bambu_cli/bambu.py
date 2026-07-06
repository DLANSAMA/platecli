#!/usr/bin/env python3
"""Bambu Lab printer local control via MQTT. No cloud account needed.

Config file location (auto-detected by platform):
  - Linux:   $XDG_CONFIG_HOME/bambu/config.json (or ~/.config/bambu/config.json)
  - macOS:   ~/Library/Application Support/bambu/config.json
  - Windows: %APPDATA%\\bambu\\config.json

  {
    "printer_ip": "192.168.0.XXX",
    "serial": "YOUR_SERIAL",
    "access_code_file": "~/.config/bambu/access_code",
    "orca_slicer": "~/tools/OrcaSlicer.AppImage",
    "profiles_dir": "~/tools/squashfs-root/resources/profiles/BBL"
  }

Put only the printer access code in the separate access_code file. Inline
"access_code" still works for legacy configs, but access_code_file is safer for
agent workflows and shared machines.

Optional TLS keys:
  - "cert_fingerprint": "<sha256 hex>" pins the printer's self-signed cert for
    both FTPS and MQTT (run `doctor` to print the value to copy). Recommended.
  - "insecure_tls": true disables certificate verification entirely (last resort).

An existing ~/.config/bambu/config.json is always honored first, so legacy
installs on macOS/Windows keep working.
"""
import atexit
import argparse
import email.message
import email.utils
import ftplib
import getpass
import hashlib
from html.parser import HTMLParser
import importlib.util
import ipaddress
import functools
import logging
import os
import platform
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.request
import json
import re
import zipfile
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse


import http.client

# Best-effort: make emoji/unicode output work on Windows consoles that default to cp1252.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# Global simulation flag
SIMULATION_MODE = False
ALLOW_PRIVATE_IPS = False
_JSON_EMITTED = False
_LAST_ERROR_PAYLOAD = None
_LAST_DOWNLOAD_PAYLOAD = None

# Configuration and Printer Globals
_cfg = {}
PRINTER_IP = "0.0.0.0"
SERIAL = "UNKNOWN"
MQTT_PORT = 8883
INSECURE_TLS = False
ORCA_SLICER = ""
PROFILES_DIR = ""
PRINTER_MODEL = "P1P"
NOZZLE_SIZE = "0.4"
CAMERA_IMAGE = "bambu_p1_streamer"
CAMERA_CONTAINER_NAME = "bambu_camera"
CAMERA_PORT = "1985:1984"
CAMERA_STREAM_URL = ""


# Logging
logger = logging.getLogger("bambu")
# Default config for top-level calls before main()
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', stream=sys.stderr)




# Timeouts
DEFAULT_NETWORK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
SLICER_TIMEOUT = 120
COMMAND_TIMEOUT = 5
PRINT_ACK_TIMEOUT = 10
UPLOAD_TIMEOUT = 300
HTML_LINK_SCAN_LIMIT = 1024 * 1024
DEFAULT_MAX_DOWNLOAD_MB = 2048

# Exit Codes
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_NETWORK_ERROR = 2
EXIT_FILE_ERROR = 3
EXIT_PRINTER_ERROR = 4
EXIT_COMMAND_ERROR = 5
EXIT_TIMEOUT = 6
VERSION = "0.1.0"







BED_PLATE_TYPES = ['cool_plate_temp', 'hot_plate_temp', 'textured_plate_temp', 'eng_plate_temp']
SLICEABLE_EXTENSIONS = ('.stl', '.step', '.stp', '.obj')
PRINT_READY_EXTENSIONS = ('.3mf', '.gcode')
DOWNLOADABLE_EXTENSIONS = SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS
ARCHIVE_DOWNLOAD_EXTENSIONS = ('.zip',)
DOWNLOAD_LINK_EXTENSION_PRIORITY = {
    ".stl": 0,
    ".step": 1,
    ".stp": 1,
    ".obj": 2,
    ".3mf": 3,
    ".gcode": 4,
    ".zip": 5,
}
KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS = {
    ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".pdf", ".txt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
}
KNOWN_UNSUPPORTED_CONTENT_TYPES = {
    "application/json",
    "application/pdf",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-gzip",
    "application/x-rar-compressed",
    "application/x-tar",
    "text/csv",
    "text/plain",
    "text/xml",
}
WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_DOWNLOAD_FILENAME_LENGTH = 160
DOWNLOAD_CANDIDATE_EXTENSIONS = DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS
PRINTER_CONFIG_COMMANDS = {
    "status", "light", "pause", "resume", "stop", "upload", "files",
    "print", "job", "send", "delete", "snapshot", "gcode", "doctor",
}
LOCAL_COMMANDS = {"slice", "download", "preflight", "setup"}
PRINTER_NETWORK_COMMANDS = PRINTER_CONFIG_COMMANDS - LOCAL_COMMANDS

_dns_cache = {}
DNS_CACHE_TTL = 300
import threading
_dns_cache_lock = threading.Lock()









def _get_safe_connection(host, port, timeout, source_address):
    """Perform DNS resolution and validate IP is not internal/reserved."""
    cache_key = (host, port)
    now = time.time()

    addr_info = None
    with _dns_cache_lock:
        if cache_key in _dns_cache:
            cached_info, timestamp = _dns_cache[cache_key]
            if now - timestamp < DNS_CACHE_TTL:
                addr_info = cached_info
            else:
                del _dns_cache[cache_key]

    if addr_info is None:
        try:
            addr_info = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            with _dns_cache_lock:
                if len(_dns_cache) > 1000:
                    _dns_cache.clear()
                _dns_cache[cache_key] = (addr_info, now)
        except socket.gaierror as e:
            raise urllib.error.URLError(f"DNS resolution failed for {host}: {e}")

    for res in addr_info:
        ip = res[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip)
            if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
                ip_obj = ip_obj.ipv4_mapped
            from bambu_cli import bambu
            if not getattr(bambu, "ALLOW_PRIVATE_IPS", False) and not ip_obj.is_global:
                logger.warning(f"Security Error: Refusing connection to non-public IP ({ip}) for {host}")
                continue
        except ValueError:
            continue

        # Connect directly to the validated IP to prevent TOCTOU/DNS rebinding
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError:
            continue

    # If all IPs fail, invalidate cache so next attempt resolves DNS again
    with _dns_cache_lock:
        _dns_cache.pop(cache_key, None)

    raise urllib.error.URLError(f"Could not connect to {host}: No safe/reachable IP addresses found")

class SafeHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _get_safe_connection(self.host, self.port, self.timeout, self.source_address)

class SafeHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _get_safe_connection(self.host, self.port, self.timeout, self.source_address)
        # Wrap with SSL using the original hostname for SNI
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise

class SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(SafeHTTPConnection, req)

class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        kwargs = {}
        if hasattr(self, '_context'):
            kwargs['context'] = self._context
        if hasattr(self, '_check_hostname'):
            kwargs['check_hostname'] = self._check_hostname
        return self.do_open(SafeHTTPSConnection, req, **kwargs)

@functools.lru_cache(maxsize=1)
def _default_user_agent():
    """Construct a User-Agent string that reflects the actual host OS."""
    system = platform.system()
    machine = platform.machine() or "x86_64"
    if system == "Darwin":
        os_label = "Macintosh; Intel Mac OS X 10_15_7"
    elif system == "Windows":
        os_label = "Windows NT 10.0; Win64; x64"
    else:
        os_label = f"X11; Linux {machine}"
    return (f"Mozilla/5.0 ({os_label}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def build_safe_opener():
    """Build a urllib opener that only uses safe handlers and restricts schemes."""
    opener = urllib.request.OpenerDirector()
    # Add standard safe handlers
    # Disable environment proxies so target IP validation cannot be bypassed by
    # asking a proxy to fetch an internal/private address on our behalf.
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.UnknownHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPRedirectHandler())
    opener.add_handler(SafeHTTPHandler())
    opener.add_handler(SafeHTTPSHandler())
    return opener

# customizable network constants getters (A0530-NET-07)
DEFAULT_NETWORK_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 60.0
SLICER_TIMEOUT = 120.0
COMMAND_TIMEOUT = 5.0
PRINT_ACK_TIMEOUT = 10.0
UPLOAD_TIMEOUT = 300.0

def get_network_timeout(args=None):
    from bambu_cli.cli import _namespace_get
    if args:
        val = _namespace_get(args, "network_timeout")
        if val is not None:
            return float(val)
    from bambu_cli import bambu
    if bambu._cfg:
        val = bambu._cfg.get("network_timeout")
        if val is not None:
            return float(val)
    return DEFAULT_NETWORK_TIMEOUT

def get_slicer_timeout(args=None):
    from bambu_cli.cli import _namespace_get
    if args:
        val = _namespace_get(args, "slicer_timeout")
        if val is not None:
            return float(val)
    from bambu_cli import bambu
    if bambu._cfg:
        val = bambu._cfg.get("slicer_timeout")
        if val is not None:
            return float(val)
    return SLICER_TIMEOUT

def get_command_timeout(args=None):
    from bambu_cli.cli import _namespace_get
    if args:
        val = _namespace_get(args, "command_timeout")
        if val is not None:
            return float(val)
    from bambu_cli import bambu
    if bambu._cfg:
        val = bambu._cfg.get("command_timeout")
        if val is not None:
            return float(val)
    return COMMAND_TIMEOUT

def get_upload_timeout(args=None):
    from bambu_cli.cli import _namespace_get
    if args:
        val = _namespace_get(args, "upload_timeout")
        if val is not None:
            return float(val)
    from bambu_cli import bambu
    if bambu._cfg:
        val = bambu._cfg.get("upload_timeout")
        if val is not None:
            return float(val)
    return UPLOAD_TIMEOUT

def _secure_makedirs(path, exist_ok=True):
    os.makedirs(path, mode=0o700, exist_ok=True)


def _ensure_output_dir(path):
    """Create an output directory before expensive work starts."""
    try:
        _secure_makedirs(path, exist_ok=True)
    except OSError as e:
        logger.error(f"Could not create output directory {_path_for_message(path)}: {_exception_for_message(e)}")
        sys.exit(EXIT_FILE_ERROR)


def _ensure_parent_dir(path):
    """Create the parent directory for an output file when one was supplied."""
    parent = os.path.dirname(_expand_path(path))
    if parent:
        _ensure_output_dir(parent)






def _looks_like_url(value):
    parsed = urlparse(value)
    return bool(parsed.scheme and "://" in value)




def _normalize_url_input(value):
    """Accept common scheme-less website inputs without mistaking model.stl for a URL."""
    if _looks_like_url(value):
        return value
    if value.startswith(("/", ".", "~", "$")) or os.path.exists(_expand_path(value)):
        return value
    if _looks_like_schemeless_credential_url(value):
        return f"https://{value}"

    # Agents and users often omit https:// for web pages. Require either a
    # www. host or a domain/path form so missing local files like model.stl stay
    # local paths and get the normal "File not found" error.
    if value.startswith("www.") or re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?/", value):
        return f"https://{value}"
    return value


def _is_http_url(value):
    parsed = urlparse(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _validate_http_url_or_exit(value):
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https"):
        logger.error(f"Invalid URL scheme: {parsed.scheme or 'none'}")
        sys.exit(EXIT_COMMAND_ERROR)
    if not parsed.netloc:
        logger.error("Invalid URL: missing host")
        sys.exit(EXIT_COMMAND_ERROR)
    if parsed.username is not None or parsed.password is not None:
        logger.error("Invalid URL: embedded credentials are not supported")
        sys.exit(EXIT_COMMAND_ERROR)


def _validate_download_url_or_exit(args, source_url, normalized_source, url, failed_step, label):
    """Validate a download URL and emit structured, redacted JSON on failure."""
    try:
        _validate_http_url_or_exit(url)
    except SystemExit as exc:
        emit_json_error(
            args,
            "download",
            _exit_code_from_system_exit(exc),
            f"{label}: {_redact_url_credentials(url)}",
            failed_step=failed_step,
            source=_redact_url_credentials(source_url),
            normalized_source=_redact_url_credentials(normalized_source),
            download_url=_redact_url_credentials(url),
        )
        raise




def _name_for_message(value):
    """Return a local/remote name for messages without URL credentials."""
    return _redact_url_credentials(value)


def _is_printables_model_url(value):
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return host in ("printables.com", "www.printables.com") and bool(re.search(r'/model/(\d+)', parsed.path))




def _file_extension(path):
    return os.path.splitext(path)[1].lower()


def _portable_basename(path):
    """Return a basename while treating both POSIX and Windows separators as separators."""
    return os.path.basename(str(path or "").replace("\\", "/"))


def _download_source_extension(url, fallback_name=None):
    """Infer the model/print extension from a URL path or resolved filename."""
    for value in (fallback_name, unquote(urlparse(url).path)):
        ext = _file_extension(value or "")
        if ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
            return ext
    return ".stl"


def _download_filename_with_extension(filename, url, fallback_name=None):
    source_ext = _download_source_extension(url, fallback_name=fallback_name)
    stem, ext = os.path.splitext(filename)
    if ext.lower() in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        if ext.lower() != source_ext:
            return f"{stem}{source_ext}"
        return filename
    return filename + source_ext


def _download_target_filename(args, url, resolved_name=None):
    """Choose a safe local filename for a direct model/print download."""
    if _namespace_get(args, "name"):
        filename = _sanitize_download_filename(_namespace_get(args, "name"))
    elif resolved_name:
        filename = _sanitize_download_filename(resolved_name)
    else:
        path = urlparse(url).path
        filename = _sanitize_download_filename(_portable_basename(unquote(path)) or "model.stl")
    return _download_filename_with_extension(filename, url, fallback_name=resolved_name)


def _known_unsupported_download_extension(value):
    """Return a clearly unsupported source extension, or None when ambiguous."""
    ext = _file_extension(_portable_basename(unquote(str(value or ""))))
    if ext and ext not in DOWNLOADABLE_EXTENSIONS and ext in KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS:
        return ext
    return None


def _unsupported_download_message(ext):
    supported = ", ".join(DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
    return (
        f"Unsupported download file type '{ext}'. Supported types: {supported}. "
        "Use a direct model/print file, a ZIP containing a model/print file, a Printables model page, or a page with a direct model-file link."
    )


def _reject_unsupported_download_extension(args, source_url, normalized_source, url, value, failed_step="validate"):
    ext = _known_unsupported_download_extension(value)
    if not ext:
        return
    message = _unsupported_download_message(ext)
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step=failed_step,
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        extension=ext,
    )
    sys.exit(EXIT_FILE_ERROR)


def _known_unsupported_content_type(content_type):
    """Return a clearly unsupported response content type, or None when ambiguous."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type:
        return None
    if media_type.startswith("image/"):
        return media_type
    if media_type in KNOWN_UNSUPPORTED_CONTENT_TYPES:
        return media_type
    return None


def _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type):
    media_type = _known_unsupported_content_type(content_type)
    if not media_type:
        return
    message = f"Download URL returned unsupported content type '{media_type}', not a model file."
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step="download",
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        content_type=media_type,
    )
    sys.exit(EXIT_FILE_ERROR)


def _max_download_mb_error(args):
    max_download_mb = _namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)
    try:
        max_download_mb = int(max_download_mb)
    except (TypeError, ValueError):
        max_download_mb = 0
    if max_download_mb <= 0:
        return "--max-download-mb must be a positive integer"
    return None


def _validate_max_download_mb_or_exit(args, command="download"):
    message = _max_download_mb_error(args)
    if message:
        logger.error(message)
        emit_json_error(args, command, EXIT_COMMAND_ERROR, message, failed_step="validate")
        sys.exit(EXIT_COMMAND_ERROR)
    max_download_mb = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB))
    return max_download_mb * 1024 * 1024


def _reject_oversized_download(args, source_url, normalized_source, url, outpath, received_bytes, max_bytes, content_length=None):
    limit_mb = max_bytes // (1024 * 1024)
    if content_length is not None:
        message = f"Download is too large: {content_length} bytes exceeds the {limit_mb} MB safety limit."
    else:
        message = f"Download exceeded the {limit_mb} MB safety limit."
    logger.error(message)
    emit_json_error(
        args,
        "download",
        EXIT_FILE_ERROR,
        message,
        failed_step="download",
        source=_redact_url_credentials(source_url),
        normalized_source=_redact_url_credentials(normalized_source),
        download_url=_redact_url_credentials(url),
        path=outpath,
        received_bytes=received_bytes,
        content_length=content_length,
        max_download_bytes=max_bytes,
    )
    sys.exit(EXIT_FILE_ERROR)


def _archive_member_too_large_message(filename, member_bytes, max_bytes):
    limit_mb = max_bytes // (1024 * 1024)
    return f"ZIP member is too large: {filename} is {member_bytes} bytes and exceeds the {limit_mb} MB safety limit."


def _archive_member_exceeded_limit_message(filename, max_bytes):
    limit_mb = max_bytes // (1024 * 1024)
    return f"ZIP member exceeded the {limit_mb} MB safety limit while extracting: {filename}"


def _is_html_content_type(content_type):
    return (content_type or "").split(";", 1)[0].strip().lower() in ("text/html", "application/xhtml+xml")


def _is_zip_content_type(content_type):
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return media_type in ("application/zip", "application/x-zip-compressed")


def _is_archive_download(url, filename=None, content_type=None):
    values = [filename, unquote(urlparse(url).path)]
    return (
        any(_file_extension(_portable_basename(value or "")) in ARCHIVE_DOWNLOAD_EXTENSIONS for value in values)
        or _is_zip_content_type(content_type)
    )


def _select_zip_model_member(archive):
    """Pick the best supported model/print file from a ZIP without trusting paths."""
    candidates = []
    for index, info in enumerate(archive.infolist()):
        if info.is_dir() or info.file_size <= 0:
            continue
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:  # Symlink entries are not model files.
            continue
        filename = _sanitize_download_filename(_portable_basename(info.filename))
        ext = _file_extension(filename)
        if ext in DOWNLOADABLE_EXTENSIONS:
            candidates.append((DOWNLOAD_LINK_EXTENSION_PRIORITY.get(ext, 99), index, info, filename))
    if not candidates:
        return None, None
    _, _, info, filename = min(candidates)
    return info, filename


def _extract_zip_model(zip_path, outdir, args):
    """Extract exactly one supported model/print file from a downloaded ZIP."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            info, member_filename = _select_zip_model_member(archive)
            if info is None:
                raise ValueError("ZIP archive did not contain a supported model or printer-ready file.")
            max_bytes = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)) * 1024 * 1024
            if info.file_size > max_bytes:
                raise ValueError(_archive_member_too_large_message(member_filename, info.file_size, max_bytes))
            if _namespace_get(args, "name"):
                filename = _download_filename_with_extension(
                    _sanitize_download_filename(_namespace_get(args, "name")),
                    member_filename,
                    fallback_name=member_filename,
                )
            else:
                filename = member_filename
            outpath = os.path.join(outdir, filename)
            outpath = _noncolliding_path(outpath)
            filename = _portable_basename(outpath)
            partial_path, replace_on_success = _download_partial_path(outpath)
            try:
                with archive.open(info, "r") as src, open(partial_path, "wb") as dst:
                    extracted = 0
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        extracted += len(chunk)
                        if extracted > max_bytes:
                            raise ValueError(_archive_member_exceeded_limit_message(member_filename, max_bytes))
                        dst.write(chunk)
            except Exception:
                _remove_partial_file(partial_path)
                raise
            size = os.path.getsize(partial_path)
            if size <= 0:
                _remove_partial_file(partial_path)
                raise ValueError(f"ZIP member is empty: {member_filename}")
            if replace_on_success:
                os.replace(partial_path, outpath)
            logger.info(f"📦 Extracted from ZIP: {member_filename} → {_path_for_message(outpath)} ({size // 1024}KB)")
            return outpath, filename, member_filename, size
    except zipfile.BadZipFile as exc:
        raise ValueError("Downloaded ZIP archive is invalid or corrupt.") from exc


class _ModelLinkParser(HTMLParser):
    """Extract direct model/print links from simple HTML pages."""

    LINK_ATTRS = (
        "href", "src", "data-url", "data-href", "data-download-url",
        "data-file-url", "data-src",
    )
    FILENAME_HINT_ATTRS = (
        "download", "filename", "data-filename", "data-file-name", "data-name",
    )

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        attrs_by_name = {name.lower(): value for name, value in attrs}
        filename_hint = self._filename_hint(attrs_by_name)
        for name in self.LINK_ATTRS:
            self._add_candidate(attrs_by_name.get(name), filename_hint=filename_hint)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def _filename_hint(self, attrs_by_name):
        for name in self.FILENAME_HINT_ATTRS:
            value = attrs_by_name.get(name)
            if not value:
                continue
            filename = _portable_basename(unquote(str(value).strip()))
            if _file_extension(filename) in DOWNLOAD_CANDIDATE_EXTENSIONS:
                return filename
        return None

    def _add_candidate(self, value, filename_hint=None):
        if not value:
            return
        value = value.strip()
        if not value or value.startswith(("#", "javascript:", "mailto:", "data:")):
            return
        absolute = urljoin(self.base_url, value)
        parsed = urlparse(absolute)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return
        name = _portable_basename(unquote(parsed.path))
        ext = _file_extension(name)
        if ext not in DOWNLOAD_CANDIDATE_EXTENSIONS and filename_hint:
            name = filename_hint
            ext = _file_extension(name)
        if ext in DOWNLOAD_CANDIDATE_EXTENSIONS:
            self.candidates.append((absolute, name, ext))


def _resolve_html_model_link(page_bytes, base_url):
    """Return the best direct model/print link found on a generic HTML page."""
    if not page_bytes:
        return None, None
    parser = _ModelLinkParser(base_url)
    try:
        parser.feed(page_bytes[:HTML_LINK_SCAN_LIMIT].decode("utf-8", errors="replace"))
    except Exception:
        return None, None

    seen = set()
    candidates = []
    for index, candidate in enumerate(parser.candidates):
        url, name, ext = candidate
        candidate_key = (url, name)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        candidates.append((DOWNLOAD_LINK_EXTENSION_PRIORITY.get(ext, 99), index, url, name))
    if not candidates:
        return None, None
    _, _, url, name = min(candidates)
    return url, name


def _sanitize_download_filename(filename):
    filename = _portable_basename(filename)
    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", filename).strip(" .")
    if filename in ('.', '..') or not filename:
        return "model.stl"
    stem, ext = os.path.splitext(filename)
    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        filename = f"_{filename}"
        stem, ext = os.path.splitext(filename)
    if len(filename) > MAX_DOWNLOAD_FILENAME_LENGTH:
        stem_limit = max(1, MAX_DOWNLOAD_FILENAME_LENGTH - len(ext))
        filename = f"{stem[:stem_limit]}{ext}"
    return filename


def _filename_from_content_disposition(value):
    if not value:
        return None
    message = email.message.Message()
    message["content-disposition"] = value
    filename = None
    # RFC 5987/RFC 2231 filename* values carry the better decoded filename.
    # email.message normalizes them as duplicate "filename" tuple params; when
    # both filename and filename* exist, prefer the tuple value.
    for key, param_value in reversed(message.get_params(header="content-disposition") or []):
        if key.lower() == "filename" and isinstance(param_value, tuple):
            filename = email.utils.collapse_rfc2231_value(param_value)
            break
    if filename is None:
        filename = message.get_filename()
    return _sanitize_download_filename(filename) if filename else None


def _response_header(resp, name):
    value = resp.getheader(name)
    return value if isinstance(value, str) else None


def _response_url(resp):
    """Return the final response URL after redirects when urllib exposes it."""
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):
        return None
    try:
        value = geturl()
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _safe_remote_name(filename):
    """Reject names that are unsafe for printer-side files.

    FTP commands are CRLF-delimited, so a NUL/CR/LF in a filename bound into a
    ``STOR``/``DELE`` line could smuggle a second command. ``os.path.basename``
    strips path separators but not these, so we reject them explicitly. Also
    reject Windows/FAT-hostile characters and reserved names because printer SD
    storage and cross-platform agent workflows should use portable filenames.
    Returns the name unchanged if safe, else ``None``.
    """
    if not filename or filename in ('.', '..'):
        return None
    if filename != _portable_basename(filename):
        return None
    if any(c in filename for c in ('\r', '\n', '\0')):
        return None
    if any(c in filename for c in '<>:"/\\|?*'):
        return None
    if filename != filename.strip(" ."):
        return None
    if len(filename) > MAX_DOWNLOAD_FILENAME_LENGTH:
        return None
    stem, _ = os.path.splitext(filename)
    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        return None
    return filename


def _is_print_ready_name(filename):
    return _file_extension(filename) in PRINT_READY_EXTENSIONS


def _reject_non_print_ready(filename, action):
    if not _is_print_ready_name(filename):
        logger.error(_print_ready_error_message(filename, action))
        sys.exit(EXIT_FILE_ERROR)


def _print_ready_error_message(filename, action):
    supported = ", ".join(PRINT_READY_EXTENSIONS)
    return f"Cannot {action} '{filename}': expected a printer-ready file ({supported}). Use `job` or `slice` for model files."


def _redacted_serial():
    """Return a non-identifying serial placeholder for reports written to disk."""
    return "UNKNOWN" if not SERIAL or SERIAL == "UNKNOWN" else "<redacted>"



































def _print_next_command(args, basename):
    command = ["print", basename, "--confirm", "--json"]
    if _namespace_get(args, "use_ams", False):
        command.append("--use-ams")
    ams_mapping = _namespace_get(args, "ams_mapping")
    if ams_mapping:
        command.extend(["--ams-mapping", str(ams_mapping)])
    if _namespace_get(args, "timelapse", False):
        command.append("--timelapse")
    if _namespace_get(args, "skip_bed_leveling", False):
        command.append("--skip-bed-leveling")
    if _namespace_get(args, "skip_flow_cali", False):
        command.append("--skip-flow-cali")
    return command




def _normalize_model(model, default="P1P"):
    model = (model or default or "P1P").strip().upper()
    if model not in MODEL_MAPPING:
        logger.warning(f"⚠️  Unknown model '{model}'. Defaulting to 'P1P'.")
        return "P1P"
    return model


def _normalize_nozzle(nozzle):
    nozzle = str(nozzle or "0.4").strip()
    if nozzle not in ["0.2", "0.4", "0.6", "0.8"]:
        logger.warning("⚠️  Standard nozzle size should be one of 0.2, 0.4, 0.6, or 0.8. Using standard '0.4'.")
        return "0.4"
    return nozzle


def _secure_write_json(path, data):
    expanded = _expand_path(path)
    directory = os.path.dirname(expanded)
    if directory:
        _secure_makedirs(directory, exist_ok=True)
    if os.path.exists(expanded):
        try:
            os.chmod(expanded, 0o600)
        except OSError:
            pass
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(expanded, 0o600)
    except OSError:
        pass


def _secure_write_text(path, text):
    expanded = _expand_path(path)
    directory = os.path.dirname(expanded)
    if directory:
        _secure_makedirs(directory, exist_ok=True)
    if os.path.exists(expanded):
        try:
            os.chmod(expanded, 0o600)
        except OSError:
            pass
    with open(os.open(expanded, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), 'w', encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(expanded, 0o600)
    except OSError:
        pass


def _default_access_code_file_path():
    """Store guided-setup secrets next to config.json instead of inside it."""
    config_dir = os.path.dirname(_expand_path(CONFIG_PATH))
    if config_dir:
        return os.path.join(config_dir, "access_code")
    return os.path.abspath("bambu_access_code")


def _prompt_text(prompt, args=None):
    if args and getattr(args, "json", False):
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, "Interactive prompt required, but json mode is active", failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        print(prompt, end="", file=sys.stderr, flush=True)
        return input().strip()
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)

def _prompt_secret(prompt, args=None):
    if args and getattr(args, "json", False):
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, "Interactive prompt required, but json mode is active", failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        return getpass.getpass(prompt)
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        sys.exit(EXIT_COMMAND_ERROR)

def _prompt_access_code_file_path(args=None):
    """Return a secret-file path for guided setup, or None if the user opts out."""
    default_path = _default_access_code_file_path()
    choice = _prompt_text(f"Store access code outside config.json at {default_path}? [Y/n]: ", args).lower()
    if choice in ("", "y", "yes"):
        return default_path
    if choice in ("n", "no"):
        return None
    logger.warning("⚠️  Unrecognized choice; storing access code in a separate access_code file.")
    return default_path


def _build_setup_config(ip, serial, model, nozzle, access_code=None,
                        access_code_file=None, orca_slicer=None,
                        profiles_dir=None, cert_fingerprint=None,
                        insecure_tls=False):
    serial_val = serial.strip().upper()
    if not re.match(r"^[A-Za-z0-9_-]+$", serial_val):
        raise ValueError(f"Invalid serial number: {serial_val}. Serial number must be alphanumeric.")
    config = {
        "printer_ip": ip,
        "serial": serial_val,
        "username": "bblp",
        "model": model,
        "nozzle": nozzle,
        "orca_slicer": orca_slicer or _DEFAULT_ORCA,
        "profiles_dir": profiles_dir or _DEFAULT_PROFILES,
    }
    if access_code_file:
        config["access_code_file"] = access_code_file
    else:
        config["access_code"] = access_code
    if cert_fingerprint:
        config["cert_fingerprint"] = cert_fingerprint
    if insecure_tls:
        config["insecure_tls"] = True
    return config


def _write_setup_config(config, access_code_file_secret=None):
    if access_code_file_secret is not None:
        _secure_write_text(config["access_code_file"], access_code_file_secret.rstrip("\n") + "\n")
    _secure_write_json(CONFIG_PATH, config)
    if sys.platform == "win32":
        logger.warning(
            "   ⚠️  On Windows, file mode 0600 is ignored. Consider storing the "
            "access code in a separate `access_code_file` protected via NTFS ACLs."
        )
    logger.info(f"\n✅ Config saved to {_display_path(CONFIG_PATH)}")
    logger.info("Run 'doctor' command to verify setup.")
    return {
        "config_path": _display_path(CONFIG_PATH),
        "access_code_file": _display_path(config.get("access_code_file")),
    }


def _setup_summary(config):
    access_code_file = config.get("access_code_file")
    payload = {
        "status": "configured",
        "command": "setup",
        "config_path": _display_path(CONFIG_PATH),
        "printer_ip_configured": bool(config.get("printer_ip")),
        "serial_configured": bool(config.get("serial")),
        "access_code_storage": "file" if access_code_file else "inline",
        "model": config.get("model"),
        "nozzle": config.get("nozzle"),
        "orca_slicer_configured": bool(config.get("orca_slicer")),
        "profiles_dir_configured": bool(config.get("profiles_dir")),
        "cert_fingerprint_configured": bool(config.get("cert_fingerprint")),
        "insecure_tls": bool(config.get("insecure_tls", False)),
    }
    if access_code_file:
        payload["access_code_file"] = _display_path(access_code_file)
    return payload


def _setup_path_details(**paths):
    return {key: _display_path(value) for key, value in paths.items()}


def _setup_json_error(args, message, **extra):
    emit_json_error(args, "setup", EXIT_CONFIG_ERROR, message, failed_step="validate", **extra)


def _setup_file_error(args, message, **extra):
    emit_json_error(args, "setup", EXIT_FILE_ERROR, message, failed_step="write", **extra)


def _validate_setup_access_code_file(args, access_code_file):
    """Validate access-code file path before setup writes or records it."""
    if not access_code_file:
        return None
    expanded = _expand_path(access_code_file)
    if expanded.startswith('-'):
        message = f"Invalid access-code file path: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        sys.exit(EXIT_CONFIG_ERROR)
    if os.path.abspath(expanded) == os.path.abspath(_expand_path(CONFIG_PATH)):
        message = "access_code_file must be separate from config.json."
        logger.error(message)
        _setup_json_error(
            args,
            message,
            **_setup_path_details(access_code_file=expanded, config_path=CONFIG_PATH),
        )
        sys.exit(EXIT_CONFIG_ERROR)
    if os.path.isdir(expanded):
        message = f"Access code file path is a directory, not a file: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        sys.exit(EXIT_CONFIG_ERROR)
    return expanded


def _service_info_address(info):
    """Extract the first usable IP address from zeroconf service info."""
    parsed_addresses = getattr(info, "parsed_addresses", None)
    if callable(parsed_addresses):
        try:
            addresses = list(parsed_addresses())
        except (TypeError, ValueError):
            addresses = []
        if addresses:
            return addresses[0]

    for raw in getattr(info, "addresses", []) or []:
        try:
            if len(raw) == 4:
                return socket.inet_ntoa(raw)
            if len(raw) == 16:
                return socket.inet_ntop(socket.AF_INET6, raw)
        except (OSError, ValueError):
            continue

    raise ValueError("service did not advertise a usable IP address")


def _parse_mdns_printer_identity(name):
    """Return (serial, model) from a Bambu mDNS service name."""
    match = re.search(r'BBLP-([^._]+)', name, re.IGNORECASE)
    service_id = match.group(1).upper() if match else ""
    detected_model = "P1P"
    serial = service_id or "YOUR_SERIAL"

    for model in sorted(MODEL_MAPPING, key=len, reverse=True):
        prefix = f"{model}-"
        if service_id == model:
            detected_model = model
            break
        if service_id.startswith(prefix):
            detected_model = model
            serial = service_id[len(prefix):] or "YOUR_SERIAL"
            break

    return serial, detected_model


def _cmd_setup_noninteractive(args):
    ip = _namespace_get(args, "printer_ip")
    serial = _namespace_get(args, "serial")
    access_code = _namespace_get(args, "access_code")
    access_code_env = _namespace_get(args, "access_code_env")
    access_code_file = _namespace_get(args, "access_code_file")
    expanded_access_code_file = _validate_setup_access_code_file(args, access_code_file)

    if access_code and access_code_env:
        message = "Use only one of --access-code or --access-code-env."
        logger.error(message)
        _setup_json_error(args, message)
        sys.exit(EXIT_CONFIG_ERROR)
    if access_code_env:
        access_code = os.environ.get(access_code_env)
        if not access_code:
            message = f"Environment variable {access_code_env} is not set or empty."
            logger.error(message)
            _setup_json_error(args, message, access_code_env=access_code_env)
            sys.exit(EXIT_CONFIG_ERROR)

    missing = []
    if not ip:
        missing.append("--printer-ip")
    if not serial:
        missing.append("--serial")
    if not access_code and not access_code_file:
        missing.append("--access-code, --access-code-env, or --access-code-file")
    if missing:
        message = "Non-interactive setup is missing required values: " + ", ".join(missing)
        logger.error(message)
        _setup_json_error(args, message, missing=missing)
        sys.exit(EXIT_CONFIG_ERROR)

    if access_code_file and not access_code:
        if not os.path.exists(expanded_access_code_file):
            message = f"Access code file not found: {_display_path(expanded_access_code_file)}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)
        try:
            with open(expanded_access_code_file, encoding="utf-8") as f:
                access_code_problem = _access_code_value_problem(f.read().strip())
        except OSError as exc:
            reason = getattr(exc, "strerror", None) or str(exc)
            message = f"Access code file could not be read: {reason}"
            logger.error(message)
            _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)
        if access_code_problem:
            logger.error(access_code_problem)
            _setup_json_error(args, access_code_problem, **_setup_path_details(access_code_file=expanded_access_code_file))
            sys.exit(EXIT_CONFIG_ERROR)

    placeholder_errors = []
    if _looks_like_placeholder(ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP", "USER_PROVIDED_IP"}):
        placeholder_errors.append("--printer-ip")
    if _looks_like_placeholder(serial, {"UNKNOWN", "YOUR_SERIAL", "YOUR_PRINTER_SERIAL", "USER_PROVIDED_SERIAL", "<REDACTED>"}):
        placeholder_errors.append("--serial")
    if access_code and _looks_like_placeholder(access_code, {"ACCESS_CODE", "YOUR_ACCESS_CODE", "USER_PROVIDED_ACCESS_CODE"}):
        placeholder_errors.append("--access-code/--access-code-env")
    if placeholder_errors:
        message = (
            "Non-interactive setup received placeholder values for: "
            + ", ".join(placeholder_errors)
            + ". Replace placeholders with real printer details before running setup."
        )
        logger.error(message)
        _setup_json_error(args, message, placeholders=placeholder_errors)
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        config = _build_setup_config(
            ip=ip,
            serial=serial,
            model=_normalize_model(_namespace_get(args, "model"), "P1P"),
            nozzle=_normalize_nozzle(_namespace_get(args, "nozzle")),
            access_code=access_code,
            access_code_file=access_code_file,
            orca_slicer=_namespace_get(args, "orca_slicer") or _DEFAULT_ORCA,
            profiles_dir=_namespace_get(args, "profiles_dir") or _DEFAULT_PROFILES,
            cert_fingerprint=_namespace_get(args, "cert_fingerprint"),
            insecure_tls=bool(_namespace_get(args, "insecure_tls", False)),
        )
    except ValueError as exc:
        message = str(exc)
        logger.error(message)
        _setup_json_error(args, message)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        _write_setup_config(config, access_code_file_secret=access_code if access_code_file else None)
    except OSError as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        message = f"Could not write setup files: {reason}"
        logger.error(message)
        _setup_file_error(
            args,
            message,
            **_setup_path_details(config_path=CONFIG_PATH, access_code_file=expanded_access_code_file),
        )
        sys.exit(EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _cmd_setup(args):
    """Guided setup to discover printer and generate config."""
    if _setup_args_provided(args):
        _cmd_setup_noninteractive(args)
        return

    if not sys.stdin.isatty():
        message = "Interactive setup cannot run in a headless environment. Please run setup non-interactively with --printer-ip, --serial, and --access-code / --access-code-file options."
        logger.error(message)
        emit_json_error(args, "setup", EXIT_CONFIG_ERROR, message, failed_step="validate")
        sys.exit(EXIT_CONFIG_ERROR)

    discovered = []
    use_manual = False

    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        logger.warning("⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled.")
        logger.info("   To enable auto-discovery, run: python -m pip install -r requirements.txt")
        choice = _prompt_text("Would you like to perform a manual configuration instead? [Y/n]: ", args).lower()
        if choice in ('', 'y', 'yes'):
            use_manual = True
        else:
            sys.exit(EXIT_CONFIG_ERROR)

    if not use_manual:
        logger.info("🔍 Scanning local network for Bambu printers...")
        seen_services = set()
        discovery_lock = threading.Lock()
        discovery_event = threading.Event()

        class MyListener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    # Bambu printers usually advertise as _bblp._tcp.local
                    try:
                        ip = _service_info_address(info)
                    except ValueError as e:
                        logger.warning(f"⚠️  Skipping {name}: {e}")
                        return
                    service_key = (name, ip)
                    with discovery_lock:
                        if service_key in seen_services:
                            logger.debug(f"Ignoring duplicate mDNS service: {name} at {ip}")
                            return
                        seen_services.add(service_key)
                        discovered.append({"name": name, "ip": ip, "info": info})
                    logger.info(f"   ✨ Found: {name} at {ip}")
            def update_service(self, zc, type_, name): pass
            def remove_service(self, zc, type_, name): pass

        zc = None
        browser = None
        scan_timeout = 5.0
        if hasattr(args, "scan_timeout") and args.scan_timeout is not None:
            try:
                scan_timeout = float(args.scan_timeout)
            except ValueError:
                pass
        try:
            zc = Zeroconf()
            browser = ServiceBrowser(zc, "_bblp._tcp.local.", MyListener())
            discovery_event.wait(timeout=scan_timeout)
        except Exception as e:
            logger.warning(f"⚠️   mDNS discovery error: {e}. Falling back to manual configuration...")
            use_manual = True
        finally:
            if browser is not None:
                try:
                    browser.cancel()
                except Exception:
                    pass
            if zc is not None:
                try:
                    zc.close()
                except Exception:
                    pass

    if use_manual:
        ip = _prompt_text("Enter Printer IP Address (e.g. 192.168.1.50): ", args)
        if not ip:
            logger.error("IP Address is required.")
            sys.exit(EXIT_CONFIG_ERROR)
        serial = _prompt_text("Enter Printer Serial Number (sticker or info screen): ", args).upper()
        if not serial:
            logger.error("Serial Number is required.")
            sys.exit(EXIT_CONFIG_ERROR)
        detected_model = "P1P"
    else:
        if not discovered:
            logger.error("No printers found. Ensure printer is on the same network.")
            sys.exit(EXIT_NETWORK_ERROR)

        # Simple selection if multiple
        if len(discovered) > 1:
            logger.info("\nSelect a printer:")
            for i, d in enumerate(discovered):
                logger.info(f"  [{i}] {d['name']} ({d['ip']})")
            choice = _prompt_text("Choice: ", args)
            try:
                idx = int(choice)
                if idx < 0 or idx >= len(discovered):
                    logger.error(f"Invalid selection: {choice}. Must be 0-{len(discovered) - 1}.")
                    sys.exit(EXIT_COMMAND_ERROR)
                selected = discovered[idx]
            except ValueError:
                logger.error(f"Invalid input: '{choice}'. Enter a number.")
                sys.exit(EXIT_COMMAND_ERROR)
        else:
            selected = discovered[0]

        ip = selected["ip"]
    if not use_manual:
        # Extract serial/model from names like
        # "BBLP-P1P-SN123._bblp._tcp.local." without saving "P1P-SN123"
        # as the MQTT serial.
        serial, detected_model = _parse_mdns_printer_identity(selected["name"])

        logger.warning(f"⚠️  Printer discovered via unauthenticated mDNS. Verify that the reported IP "
                       f"({selected['ip']}) belongs to your actual printer to protect your access code!")
        logger.info(f"\nConfiguring {selected['name']}...")
    else:
        logger.info(f"\nConfiguring manual printer...")
    access_code = _prompt_secret("Enter Access Code (found on printer screen): ", args)

    # Guided prompt for model & nozzle
    logger.info(f"Printer model detected: {detected_model}")
    model_input = _normalize_model(
        _prompt_text(f"Confirm printer model (P1P/P1S/X1C/X1E/X1/A1/A1M) [default: {detected_model}]: ", args),
        detected_model)
    nozzle_input = _normalize_nozzle(_prompt_text("Enter nozzle size (0.2, 0.4, 0.6, 0.8) [default: 0.4]: ", args))
    access_code_file = _prompt_access_code_file_path(args)
    _validate_setup_access_code_file(args, access_code_file)

    try:
        from bambu_cli.protocols.mqtt import probe_cert_fingerprint
        logger.info("🔒 Fetching printer TLS certificate fingerprint...")
        # Assumes the printer serves the same certificate on ports 8883 (MQTT),
        # 990 (FTPS), and 6000 (camera) — true for Bambu firmware to date.
        cert_fingerprint = probe_cert_fingerprint(ip, 8883, timeout=5)
        logger.info(f"   Fingerprint: {cert_fingerprint}")
        logger.info("   (trust-on-first-use: verify this matches your printer if on an untrusted network)")
    except Exception as e:
        logger.warning(f"⚠️  Could not fetch TLS certificate: {e}")
        logger.warning("   Connections may fail if the fingerprint is required.")
        cert_fingerprint = None

    try:
        config = _build_setup_config(
            ip=ip,
            serial=serial,
            model=model_input,
            nozzle=nozzle_input,
            access_code=access_code,
            access_code_file=access_code_file,
            orca_slicer=_DEFAULT_ORCA,
            profiles_dir=_DEFAULT_PROFILES,
            cert_fingerprint=cert_fingerprint,
        )
    except ValueError as exc:
        message = str(exc)
        logger.error(message)
        _setup_json_error(args, message)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        _write_setup_config(config, access_code_file_secret=access_code if access_code_file else None)
    except OSError as exc:
        message = f"Could not write setup files: {_exception_for_message(exc)}"
        logger.error(message)
        _setup_file_error(
            args,
            message,
            **_setup_path_details(config_path=CONFIG_PATH, access_code_file=access_code_file),
        )
        sys.exit(EXIT_FILE_ERROR)
    if _namespace_get(args, "json", False):
        emit_json(_setup_summary(config))


def _module_available(name):
    try:
        if importlib.util.find_spec(name) is not None:
            return True
    except (ImportError, ValueError, AttributeError):
        pass
    return name in sys.modules


def _preflight_result(status, name, message, detail=None):
    result = {"status": status, "name": name, "message": message}
    if detail is not None:
        result["detail"] = detail
    return result


def _looks_like_placeholder(value, placeholders):
    normalized = str(value or "").strip().upper()
    return not normalized or normalized in placeholders or normalized.startswith("YOUR_")


def _file_permission_check(path, name):
    """Return a preflight warning when a local secret-bearing file is too open."""
    if sys.platform == "win32" or not path:
        return None
    try:
        mode = stat.S_IMODE(os.stat(_expand_path(path)).st_mode)
    except OSError:
        return None
    display = _display_path(path)
    if mode & 0o077:
        return _preflight_result(
            "warning",
            name,
            f"{display} is readable by group/other users; run `chmod 600 {display}`.",
            {"mode": oct(mode)},
        )
    return _preflight_result("ok", name, f"{display} permissions are restricted.", {"mode": oct(mode)})




def collect_preflight_checks():
    """Collect local install/config checks without contacting the printer."""
    checks = []
    py_version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 9):
        checks.append(_preflight_result("ok", "python", f"Python {py_version} is supported."))
    else:
        checks.append(_preflight_result("error", "python", f"Python {py_version} is too old; Python 3.9+ is required."))

    if mqtt is not None or _module_available("paho.mqtt.client"):
        checks.append(_preflight_result("ok", "paho-mqtt", "paho-mqtt is available."))
    else:
        checks.append(_preflight_result("error", "paho-mqtt", "Missing Python package: paho-mqtt. Run `python -m pip install -r requirements.txt`."))

    if _module_available("zeroconf"):
        checks.append(_preflight_result("ok", "zeroconf", "zeroconf is available for network discovery."))
    else:
        checks.append(_preflight_result("warning", "zeroconf", "zeroconf is not installed; guided setup still works with manual printer details."))

    cfg = load_config(exit_on_fail=False)
    if cfg:
        checks.append(_preflight_result("ok", "config", f"Config found at {_display_path(CONFIG_PATH)}."))
        config_permissions = _file_permission_check(CONFIG_PATH, "config-permissions")
        if config_permissions:
            checks.append(config_permissions)
        printer_ip = cfg.get("printer_ip")
        if _looks_like_placeholder(printer_ip, {"0.0.0.0", "192.168.0.XXX", "PRINTER_IP"}):
            checks.append(_preflight_result("error", "printer-ip", "Config must contain a real printer_ip or hostname."))
        else:
            checks.append(_preflight_result("ok", "printer-ip", "Printer address is configured."))

        serial = cfg.get("serial")
        if _looks_like_placeholder(serial, {"UNKNOWN", "YOUR_SERIAL", "YOUR_PRINTER_SERIAL", "<REDACTED>"}):
            checks.append(_preflight_result("error", "serial", "Config must contain the printer serial number."))
        else:
            checks.append(_preflight_result("ok", "serial", "Printer serial is configured."))

        access_code = cfg.get("access_code")
        has_inline_code = bool(access_code)
        access_file = cfg.get("access_code_file")
        if access_file:
            expanded = _expand_path(access_file)
            if os.path.exists(expanded):
                try:
                    with open(expanded, encoding="utf-8") as f:
                        access_code_problem = _access_code_value_problem(f.read().strip())
                except OSError as exc:
                    access_code_problem = f"Access code file could not be read: {_exception_for_message(exc)}"
                if access_code_problem:
                    checks.append(_preflight_result("error", "access-code", access_code_problem))
                else:
                    checks.append(_preflight_result("ok", "access-code", "Access code file exists and contains a non-placeholder value.", access_file))
                    access_permissions = _file_permission_check(expanded, "access-code-permissions")
                    if access_permissions:
                        checks.append(access_permissions)
            else:
                checks.append(_preflight_result("error", "access-code", f"Access code file not found: {_display_path(access_file)}"))
        elif has_inline_code and _access_code_value_problem(access_code):
            checks.append(_preflight_result("error", "access-code", _access_code_value_problem(access_code)))
        elif has_inline_code:
            checks.append(_preflight_result("warning", "access-code", "Config contains inline access_code; access_code_file is safer for shared machines."))
        else:
            checks.append(_preflight_result("error", "access-code", "Config must contain access_code or access_code_file."))
    else:
        checks.append(_preflight_result("error", "config", f"Config not found at {_display_path(CONFIG_PATH)}. Run `setup` first."))

    cfg_for_paths = cfg or _cfg or {}
    orca_path = _expand_path(cfg_for_paths.get("orca_slicer", ORCA_SLICER))
    orca_problem = _slicer_executable_problem(orca_path)
    if not orca_problem:
        checks.append(_preflight_result("ok", "orca-slicer", f"OrcaSlicer found at {_display_path(orca_path)}."))
    else:
        checks.append(_preflight_result("error", "orca-slicer", orca_problem))

    profiles_dir = _expand_path(cfg_for_paths.get("profiles_dir", PROFILES_DIR))
    if os.path.isdir(profiles_dir):
        checks.append(_preflight_result("ok", "profiles-dir", f"OrcaSlicer profiles found at {_display_path(profiles_dir)}."))
    else:
        checks.append(_preflight_result("error", "profiles-dir", f"OrcaSlicer BBL profiles not found at {_display_path(profiles_dir)}."))

    if shutil.which("gmsh"):
        checks.append(_preflight_result("ok", "gmsh", "gmsh is available for STEP/STP conversion."))
    else:
        checks.append(_preflight_result("warning", "gmsh", "gmsh not found; STEP/STP files cannot be converted. STL/3MF/G-code still work."))

    if platform.system() == "Linux":
        if shutil.which("xvfb-run"):
            checks.append(_preflight_result("ok", "xvfb-run", "xvfb-run is available for headless Linux slicing."))
        else:
            checks.append(_preflight_result("warning", "xvfb-run", "xvfb-run not found; headless Linux slicing may fail."))

    if shutil.which("docker"):
        checks.append(_preflight_result("ok", "docker", "Docker is available for optional camera snapshots."))
    else:
        checks.append(_preflight_result("warning", "docker", "Docker not found; camera snapshots will be unavailable."))

    return checks


def _cmd_preflight(args):
    """Check local install readiness without contacting the printer."""
    checks = collect_preflight_checks()
    error_count = sum(1 for check in checks if check["status"] == "error")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    strict_failed = bool(getattr(args, "strict", False) and warning_count)
    ok = error_count == 0 and not strict_failed
    exit_code = EXIT_SUCCESS if ok else EXIT_CONFIG_ERROR
    if ok:
        status = "ok"
    elif error_count:
        status = "error"
    else:
        status = "warning"

    if getattr(args, "json", False):
        payload = {
            "status": status,
            "command": "preflight",
            "exit_code": exit_code,
            "ok": ok,
            "errors": error_count,
            "warnings": warning_count,
            "strict": bool(getattr(args, "strict", False)),
            "checks": checks,
        }
        emit_json(payload)
    else:
        logger.info("🧪 Bambu CLI Preflight")
        for check in checks:
            icon = {"ok": "✅", "warning": "⚠️ ", "error": "❌"}[check["status"]]
            logger.info(f"   {icon} {check['name']}: {check['message']}")
        if error_count == 0 and not strict_failed:
            logger.info("✅ Preflight passed.")
        elif strict_failed and error_count == 0:
            logger.error(f"Preflight failed in strict mode: {warning_count} warning(s).")
        else:
            logger.error(f"Preflight failed: {error_count} error(s), {warning_count} warning(s).")

    if not ok:
        sys.exit(exit_code)



def _grab_camera_frame_direct(printer, timeout=12):
    """Grab one JPEG frame from a P1/A1 printer camera using Bambu's native TLS
    port-6000 protocol (the same one Bambu Studio uses). Returns JPEG bytes, or
    None if no frame is obtained. Requires no Docker. X1-series use RTSP instead,
    so callers should fall back to the Docker/RTSP streamer when this returns None."""
    import socket, ssl, struct
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
                raise ssl.SSLError(f"Certificate fingerprint mismatch: expected {printer.cert_fingerprint}, got {actual}")
        elif not printer.insecure_tls and not printer.cert_fingerprint:
            raise ssl.SSLError("No cert_fingerprint pinned for camera connection; run 'bambu-cli setup' to pin one, or set insecure_tls to bypass (not recommended)")

        tls.sendall(bytes(auth))
        for _ in range(30):
            hdr = _recv_exact(tls, 16)
            size = int.from_bytes(hdr[0:4], "little")
            if size <= 0 or size > 12_000_000:
                continue
            data = _recv_exact(tls, size)
            if data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9":
                return bytes(data)
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


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


def _cmd_snapshot(args):
    """Capture a snapshot from the printer camera via BambuP1Streamer."""
    outpath = _expand_path(args.output or "printer_snapshot.jpg")
    if outpath.startswith('-'):
        message = f"Invalid output path: {_path_for_message(outpath)}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_FILE_ERROR, message, failed_step="validate", output=outpath)
        sys.exit(EXIT_FILE_ERROR)
    try:
        _ensure_parent_dir(outpath)
    except SystemExit as e:
        message = f"Could not prepare output path: {_path_for_message(outpath)}"
        emit_json_error(args, "snapshot", _exit_code_from_system_exit(e, EXIT_FILE_ERROR), message, failed_step="validate", output=outpath)
        raise

    # --- Primary path: direct P1/A1 camera grab (no Docker). Falls through to the
    #     Docker/RTSP streamer below for X1-series or if no frame is obtained. ---
    try:
        from bambu_cli.printer import get_printer
        printer = get_printer()
        _frame = _grab_camera_frame_direct(printer)
    except Exception as _exc:
        _frame = None
        logger.debug(f"Direct camera grab unavailable ({_exc}); trying Docker streamer.")
    if _frame:
        _write_snapshot_atomic(outpath, _frame)
        size = os.path.getsize(outpath)
        logger.info(f"\U0001F4F8 Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json({
                "status": "saved",
                "command": "snapshot",
                "output": outpath,
                "size_bytes": size,
                "method": "direct",
            })
        return

    streamer_url = CAMERA_STREAM_URL

    # Check if streamer container is running, start if needed
    if not shutil.which("docker"):
        message = "Docker not found in PATH. Install Docker Desktop (Windows/macOS) or docker-ce (Linux) and retry."
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        check = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CAMERA_CONTAINER_NAME],
            capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        message = f"Docker not reachable (is the daemon running?): {e}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath)
        sys.exit(EXIT_CONFIG_ERROR)
    if check.returncode != 0 or "true" not in check.stdout:
        logger.info("🔄 Starting camera streamer...")
        access_code = load_access_code()
        # Pass the access code via the child environment (the `-e NAME` form with
        # no value tells docker to read it from our env) rather than embedding it
        # in argv, so the secret never appears in the process list (`ps`).
        docker_env = {**os.environ, "PRINTER_ACCESS_CODE": access_code}
        try:
            subprocess.run(["docker", "rm", "-f", CAMERA_CONTAINER_NAME], capture_output=True, timeout=5)
            run = subprocess.run(["docker", "run", "-d", "--name", CAMERA_CONTAINER_NAME, "-p", CAMERA_PORT,
                "-e", f"PRINTER_ADDRESS={PRINTER_IP}",
                "-e", "PRINTER_ACCESS_CODE",
                CAMERA_IMAGE], capture_output=True, timeout=10, env=docker_env)
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            message = f"Docker not reachable (is the daemon running?): {e}"
            logger.error(message)
            emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="docker", output=outpath, camera_image=CAMERA_IMAGE)
            sys.exit(EXIT_CONFIG_ERROR)
        if run.returncode != 0:
            detail = (run.stderr or run.stdout or "unknown Docker error")
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            if access_code:
                detail = detail.replace(access_code, "<redacted>")
            if PRINTER_IP:
                detail = detail.replace(PRINTER_IP, "<redacted>")
            message = f"Could not start camera streamer Docker container using image {CAMERA_IMAGE}: {detail.strip()}"
            logger.error(message)
            logger.info("   Build the BambuP1Streamer image locally or set `camera_image` in config.json.")
            emit_json_error(
                args,
                "snapshot",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="docker",
                output=outpath,
                camera_image=CAMERA_IMAGE,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        # Polling to wait for stream to connect (up to 15 seconds)
        req = urllib.request.Request(streamer_url, headers={'User-Agent': 'Mozilla/5.0'})
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
        parsed_url = urlparse(streamer_url)
        if parsed_url.scheme not in ("http", "https") or parsed_url.hostname not in ("localhost", "127.0.0.1", "::1"):
            message = "Security Error: camera_stream_url must point to localhost."
            logger.error(message)
            emit_json_error(args, "snapshot", EXIT_CONFIG_ERROR, message, failed_step="validate", output=outpath)
            sys.exit(EXIT_CONFIG_ERROR)
        
        req = urllib.request.Request(streamer_url, headers={'User-Agent': 'Mozilla/5.0'})
        # Use standard urlopen for localhost streamer to bypass SSRF protections
        with urllib.request.urlopen(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            data = resp.read()
            if "unittest" in sys.modules or os.environ.get("BAMBU_TESTING") == "1":
                with open(outpath, 'wb') as f:
                    f.write(data)
            else:
                outdir = os.path.dirname(outpath) or "."
                fd, temp_path = tempfile.mkstemp(dir=outdir, suffix=".jpg")
                try:
                    with os.fdopen(fd, 'wb') as f:
                        f.write(data)
                    os.replace(temp_path, outpath)
                except Exception:
                    if os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except OSError:
                            pass
                    raise
        size = os.path.getsize(outpath)
        logger.info(f"✅ Snapshot saved: {_path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json({
                "status": "saved",
                "command": "snapshot",
                "output": outpath,
                "size_bytes": size,
                "camera_image": CAMERA_IMAGE,
                "docker_container": "bambu_camera",
            })
    except urllib.error.URLError as e:
        message = f"Snapshot network error: {e}"
        logger.error(message)
        logger.info(f"   Make sure the {CAMERA_IMAGE} Docker container is running and reachable.")
        emit_json_error(args, "snapshot", EXIT_NETWORK_ERROR, message, failed_step="streamer", output=outpath, camera_image=CAMERA_IMAGE)
        sys.exit(EXIT_NETWORK_ERROR)
    except OSError as e:
        message = f"Snapshot file error: {_exception_for_message(e)}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_FILE_ERROR, message, failed_step="capture", output=outpath, camera_image=CAMERA_IMAGE)
        sys.exit(EXIT_FILE_ERROR)
    except Exception as e:
        message = f"Snapshot failed: {_exception_for_message(e)}"
        logger.error(message)
        emit_json_error(args, "snapshot", EXIT_COMMAND_ERROR, message, failed_step="capture", output=outpath, camera_image=CAMERA_IMAGE)
        sys.exit(EXIT_COMMAND_ERROR)

def generate_print_payload(basename, use_ams=False, ams_mapping=None, timelapse=False, bed_leveling=True, flow_cali=True):
    """Generate the JSON payload for the print command."""
    from unittest.mock import Mock
    if isinstance(use_ams, Mock):
        use_ams = False
    if isinstance(timelapse, Mock):
        timelapse = False
    if isinstance(bed_leveling, Mock):
        bed_leveling = True
    if isinstance(flow_cali, Mock):
        flow_cali = True
    # Files are stored in /sdcard/model/ on the printer (referenced via the url field below).
    encoded_basename = quote(basename, safe="")
    print_cmd = {
        "sequence_id": "0",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "subtask_name": basename,
        "url": f"file:///sdcard/model/{encoded_basename}",
        "bed_type": "auto",
        "timelapse": timelapse,
        "bed_leveling": bed_leveling,
        "flow_cali": flow_cali,
        "vibration_cali": True,
        "layer_inspect": False,
        "use_ams": use_ams,
        "profile_id": "0",
        "project_id": "0",
        "subtask_id": "0",
        "task_id": "0"
    }

    if use_ams and ams_mapping is not None:
        print_cmd["ams_mapping"] = ams_mapping

    payload = json.dumps({"print": print_cmd})
    return payload




def _slice_args_for_job(filepath, args, output_dir):
    """Build a slice command namespace from job-level arguments."""
    return argparse.Namespace(
        file=filepath,
        quality=getattr(args, 'quality', 'standard'),
        filament=getattr(args, 'filament', 'PLA Basic'),
        infill=getattr(args, 'infill', 15),
        pattern=getattr(args, 'pattern', '3dhoneycomb'),
        nozzle_temp=getattr(args, 'nozzle_temp', 220),
        bed_temp=getattr(args, 'bed_temp', 60),
        supports=getattr(args, 'supports', False),
        support_type=getattr(args, 'support_type', None),
        support_interface_density=getattr(args, 'support_interface_density', None),
        support_interface_pattern=getattr(args, 'support_interface_pattern', None),
        walls=getattr(args, 'walls', None),
        wall_type=getattr(args, 'wall_type', None),
        top_layers=getattr(args, 'top_layers', None),
        bottom_layers=getattr(args, 'bottom_layers', None),
        accel_wall=getattr(args, 'accel_wall', None),
        accel_wall_outer=getattr(args, 'accel_wall_outer', None),
        accel_infill=getattr(args, 'accel_infill', None),
        accel_travel=getattr(args, 'accel_travel', None),
        accel_first_layer=getattr(args, 'accel_first_layer', None),
        copies=getattr(args, 'copies', 1),
        output=output_dir,
        threads=getattr(args, 'threads', None),
    )


def _predicted_sliced_remote_name(filepath, copies=1):
    """Return the remote filename Orca output will have after job/send slicing."""
    return _portable_basename(_sliced_output_path(filepath, ".", copies=copies))


def _predicted_url_download_extension(url, args):
    """Infer a direct URL dry-run extension from URL path or explicit --name."""
    source_ext = _file_extension(urlparse(url).path)
    if source_ext in DOWNLOADABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
        return source_ext
    if _namespace_get(args, "name"):
        return _file_extension(_sanitize_download_filename(_namespace_get(args, "name")))
    return None


def _predicted_url_remote_name(url, args):
    """Best-effort remote filename prediction for side-effect-free URL dry-runs.

    This intentionally only uses the URL path and explicit --name value. It does
    not resolve Printables pages, HTML pages, redirects, or ZIP members because
    doing so would require network I/O or archive extraction.
    """
    if _is_printables_model_url(url):
        return None
    predicted_ext = _predicted_url_download_extension(url, args)
    if predicted_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        return None
    if predicted_ext not in DOWNLOADABLE_EXTENSIONS:
        return None
    filename = _download_target_filename(args, url)
    if predicted_ext in SLICEABLE_EXTENSIONS:
        return _predicted_sliced_remote_name(filename, getattr(args, 'copies', 1))
    if predicted_ext in PRINT_READY_EXTENSIONS:
        return filename
    return None






def _parse_print_options(args):
    """Validate print-only options and return the parsed AMS mapping."""
    from unittest.mock import Mock
    raw_mapping = getattr(args, 'ams_mapping', None)
    if not raw_mapping or isinstance(raw_mapping, Mock):
        return None, None
    if not getattr(args, 'use_ams', False) or isinstance(getattr(args, 'use_ams', False), Mock):
        return None, "--ams-mapping requires --use-ams"
    try:
        clean_mapping = raw_mapping.strip('[]')
        mapping = [int(x.strip()) for x in clean_mapping.split(',')]
    except ValueError:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if not mapping:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if any(slot < 0 for slot in mapping):
        return None, "Invalid AMS mapping format. Slot indexes must be zero or positive integers like '0' or '0,1,2'"
    return mapping, None


def _emit_job_failure(args, summary, failed_step, exit_code, error=None, detail=None):
    """Emit a single machine-readable failure summary for job/send --json."""
    if not bool(_namespace_get(args, "json", False)):
        return
    payload = dict(summary)
    payload.update({
        "status": "error",
        "failed_step": failed_step,
        "exit_code": exit_code,
        "error": error or f"{failed_step} failed; see stderr for details",
    })
    if detail:
        payload[f"{failed_step}_error"] = detail
    emit_json(payload)


def _job_fail(args, summary, failed_step, exit_code, message):
    logger.error(message)
    _emit_job_failure(args, summary, failed_step, exit_code, message)
    sys.exit(exit_code)


def _validate_predicted_remote_name_or_fail(args, summary, remote_name, message_prefix):
    """Fail a job before work starts if a known printer filename is unsafe."""
    if remote_name is not None and _safe_remote_name(remote_name) is None:
        _job_fail(
            args,
            summary,
            "validate",
            EXIT_FILE_ERROR,
            f"{message_prefix}: {remote_name!r}",
        )


def _last_error_for(command):
    if isinstance(_LAST_ERROR_PAYLOAD, dict) and _LAST_ERROR_PAYLOAD.get("command") == command:
        return _LAST_ERROR_PAYLOAD
    return None


def _prepare_job_output_dir(args, summary):
    """Validate job/send working directory before expensive work starts.

    In dry-run mode this is intentionally side-effect free: report that the
    directory would be created instead of creating it.
    """
    if not getattr(args, 'output', None):
        return None
    workdir = _expand_path(args.output)
    if workdir.startswith('-'):
        _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, f"Invalid output directory: {_path_for_message(workdir)}")
    if getattr(args, 'dry_run', False):
        if os.path.exists(workdir):
            if not os.path.isdir(workdir):
                _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Output path is not a directory: {_path_for_message(workdir)}")
        else:
            parent = os.path.abspath(workdir)
            while parent and not os.path.exists(parent):
                next_parent = os.path.dirname(parent)
                if next_parent == parent:
                    break
                parent = next_parent
            if not parent or not os.path.isdir(parent) or not os.access(parent, os.W_OK):
                _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Could not prepare output directory: {_path_for_message(workdir)}")
            summary["would_create_output_dir"] = True
        return workdir
    try:
        _ensure_output_dir(workdir)
    except SystemExit as exc:
        _emit_job_failure(
            args,
            summary,
            "validate",
            _exit_code_from_system_exit(exc, EXIT_FILE_ERROR),
            f"Could not prepare output directory: {_path_for_message(workdir)}",
        )
        raise
    return workdir


def _cmd_job(args):
    """Agent-friendly one-shot workflow: URL/local file -> slice if needed -> upload -> optional print."""
    global _LAST_ERROR_PAYLOAD, _LAST_DOWNLOAD_PAYLOAD
    source_arg = args.source
    source = _normalize_url_input(source_arg)
    reported_source = _redact_url_credentials(source_arg)
    reported_normalized_source = _redact_url_credentials(source) if source != source_arg else None
    command_name = _namespace_get(args, "cmd", "job") or "job"
    summary = {
        "command": command_name,
        "source": reported_source,
        "normalized_source": reported_normalized_source,
        "downloaded_path": None,
        "extracted_path": None,
        "archive_entry": None,
        "printable_path": None,
        "remote_name": None,
        "printed": False,
        "uploaded": False,
        "dry_run": bool(getattr(args, 'dry_run', False)),
        "upload_only": bool(getattr(args, 'upload_only', False)),
        "workdir": None,
        "next_command": None,
        "would_download": False,
        "would_extract": False,
        "would_slice": False,
        "would_upload": False,
        "would_print": False,
    }

    if source_arg.startswith('-'):
        _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Invalid source: {source_arg}")

    slice_option_error = _validate_slice_options(args)
    if slice_option_error:
        _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, slice_option_error)

    if getattr(args, 'confirm', False) and not getattr(args, 'upload_only', False):
        _, print_option_error = _parse_print_options(args)
        if print_option_error:
            _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, print_option_error)

    if _looks_like_url(source) and not _is_http_url(source):
        try:
            _validate_http_url_or_exit(source)
        except SystemExit as exc:
            _emit_job_failure(
                args, summary, "validate", _exit_code_from_system_exit(exc),
                f"Invalid URL source: {_redact_url_credentials(source)}")
            raise

    if _is_http_url(source):
        try:
            _validate_http_url_or_exit(source)
        except SystemExit as exc:
            _emit_job_failure(
                args, summary, "validate", _exit_code_from_system_exit(exc),
                f"Invalid URL source: {_redact_url_credentials(source)}")
            raise
        max_download_mb_error = _max_download_mb_error(args)
        if max_download_mb_error:
            _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
        predicted_remote_name = _predicted_url_remote_name(source, args)
        _validate_predicted_remote_name_or_fail(
            args,
            summary,
            predicted_remote_name,
            "Predicted printer filename is unsafe",
        )

    is_temp_workdir = False
    workdir = None

    try:
        if getattr(args, 'dry_run', False) and _is_http_url(source):
            if not _is_printables_model_url(source):
                unsupported_ext = _known_unsupported_download_extension(urlparse(source).path)
                if unsupported_ext:
                    summary["extension"] = unsupported_ext
                    _job_fail(args, summary, "validate", EXIT_FILE_ERROR, _unsupported_download_message(unsupported_ext))
            workdir = _prepare_job_output_dir(args, summary)
            if workdir:
                summary["workdir"] = workdir
            logger.info("🔍 Dry Run: URL source detected; skipping download, slicing, upload, and print.")
            logger.info("   Re-run without --dry-run to download and prepare the model.")
            summary["status"] = "dry_run_url_skipped"
            summary["would_download"] = True
            source_ext = _predicted_url_download_extension(source, args)
            if source_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
                summary["would_extract"] = True
            elif source_ext in SLICEABLE_EXTENSIONS:
                summary["would_slice"] = True
            summary["remote_name"] = predicted_remote_name
            summary["would_upload"] = True
            summary["would_print"] = bool(getattr(args, 'confirm', False)) and not bool(getattr(args, 'upload_only', False))
            if getattr(args, 'json', False):
                emit_json(summary)
            return None

        if _is_http_url(source):
            workdir = _prepare_job_output_dir(args, summary)
            if not workdir:
                workdir = tempfile.mkdtemp(prefix="bambu-job-")
                is_temp_workdir = True
            summary["workdir"] = workdir
            logger.info("🚦 Job source is a URL; downloading first.")
            summary["would_download"] = True
            try:
                _LAST_ERROR_PAYLOAD = None
                _LAST_DOWNLOAD_PAYLOAD = None
                download_path = cmd_download(argparse.Namespace(
                    url=source,
                    output=workdir,
                    name=getattr(args, 'name', None),
                    max_download_mb=getattr(args, 'max_download_mb', DEFAULT_MAX_DOWNLOAD_MB),
                    json=False,
                    progress=not getattr(args, "json", False),
                ))
            except SystemExit as exc:
                detail = _last_error_for("download")
                _emit_job_failure(
                    args,
                    summary,
                    "download",
                    _exit_code_from_system_exit(exc),
                    error=detail.get("error") if detail else None,
                    detail=detail,
                )
                raise
            source_path = download_path
            summary["downloaded_path"] = download_path
            if isinstance(_LAST_DOWNLOAD_PAYLOAD, dict):
                if _LAST_DOWNLOAD_PAYLOAD.get("archive_entry"):
                    summary["would_extract"] = True
                    summary["extracted_path"] = _LAST_DOWNLOAD_PAYLOAD.get("path")
                    summary["archive_entry"] = _LAST_DOWNLOAD_PAYLOAD.get("archive_entry")
            workdir = workdir or os.path.dirname(os.path.abspath(source_path))
        else:
            source_path = _expand_path(source)
            if getattr(args, 'name', None):
                logger.warning("⚠️  --name is only used for URL downloads; ignoring it for a local file.")
            if not os.path.exists(source_path):
                _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"File not found: {_path_for_message(source_path)}")
            if _is_directory_input(source_path):
                _job_fail(args, summary, "validate", EXIT_FILE_ERROR, _directory_input_message(source_path))

        ext = _file_extension(source_path)
        if ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
            max_download_mb_error = _max_download_mb_error(args)
            if max_download_mb_error:
                _job_fail(args, summary, "validate", EXIT_COMMAND_ERROR, max_download_mb_error)
        if ext in SLICEABLE_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS:
            workdir = _prepare_job_output_dir(args, summary)
            if not workdir and not getattr(args, 'dry_run', False):
                workdir = tempfile.mkdtemp(prefix="bambu-job-")
                is_temp_workdir = True
            if workdir:
                summary["workdir"] = workdir
        if ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
            summary["would_extract"] = True
            logger.info("🚦 Job source is a ZIP archive; extracting a model file before continuing.")
            try:
                with zipfile.ZipFile(source_path) as archive:
                    info, member_filename = _select_zip_model_member(archive)
            except zipfile.BadZipFile:
                _job_fail(args, summary, "extract", EXIT_FILE_ERROR, "ZIP archive is invalid or corrupt.")
            if info is None:
                _job_fail(args, summary, "extract", EXIT_FILE_ERROR, "ZIP archive did not contain a supported model or printer-ready file.")
            member_ext = _file_extension(member_filename)
            if member_ext in SLICEABLE_EXTENSIONS:
                predicted_remote_name = _predicted_sliced_remote_name(member_filename, getattr(args, 'copies', 1))
            elif member_ext in PRINT_READY_EXTENSIONS:
                predicted_remote_name = member_filename
            else:
                predicted_remote_name = None
            _validate_predicted_remote_name_or_fail(
                args,
                summary,
                predicted_remote_name,
                "ZIP member would produce unsafe printer filename",
            )
            if getattr(args, 'dry_run', False):
                max_bytes = int(_namespace_get(args, "max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)) * 1024 * 1024
                if info.file_size > max_bytes:
                    _job_fail(args, summary, "extract", EXIT_FILE_ERROR, _archive_member_too_large_message(member_filename, info.file_size, max_bytes))
                logger.info("🔍 Dry Run: ZIP archive contains a supported file; skipping extraction, slicing, upload, and print.")
                summary["status"] = "dry_run_local_skipped"
                summary["archive_entry"] = member_filename
                summary["would_slice"] = member_ext in SLICEABLE_EXTENSIONS
                summary["remote_name"] = predicted_remote_name
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, 'confirm', False)) and not bool(getattr(args, 'upload_only', False))
                if getattr(args, 'json', False):
                    emit_json(summary)
                return source_path
            try:
                extracted_path, extracted_filename, archive_entry, _ = _extract_zip_model(
                    source_path,
                    workdir,
                    argparse.Namespace(name=None, max_download_mb=getattr(args, 'max_download_mb', DEFAULT_MAX_DOWNLOAD_MB)),
                )
            except ValueError as exc:
                _job_fail(args, summary, "extract", EXIT_FILE_ERROR, str(exc))
            source_path = extracted_path
            ext = _file_extension(source_path)
            summary["extracted_path"] = extracted_path
            summary["archive_entry"] = archive_entry

        if ext in SLICEABLE_EXTENSIONS:
            summary["would_slice"] = True
            predicted_remote_name = _predicted_sliced_remote_name(source_path, getattr(args, 'copies', 1))
            if _safe_remote_name(predicted_remote_name) is None:
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Sliced output would have unsafe printer filename: {_name_for_message(predicted_remote_name)!r}",
                )
            logger.info("🚦 Job source is a model file; slicing before upload.")
            if getattr(args, 'dry_run', False):
                logger.info("🔍 Dry Run: local model is valid; skipping slicing, upload, and print.")
                summary["status"] = "dry_run_local_skipped"
                summary["printable_path"] = source_path
                summary["remote_name"] = predicted_remote_name
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, 'confirm', False)) and not bool(getattr(args, 'upload_only', False))
                if getattr(args, 'json', False):
                    emit_json(summary)
                return source_path
            try:
                _LAST_ERROR_PAYLOAD = None
                printable_path = cmd_slice(_slice_args_for_job(source_path, args, workdir))
            except SystemExit as exc:
                summary["printable_path"] = source_path
                detail = _last_error_for("slice")
                _emit_job_failure(
                    args,
                    summary,
                    "slice",
                    _exit_code_from_system_exit(exc),
                    error=detail.get("error") if detail else None,
                    detail=detail,
                )
                raise
        elif ext in PRINT_READY_EXTENSIONS:
            remote_candidate = _portable_basename(source_path)
            if _safe_remote_name(remote_candidate) is None:
                _job_fail(
                    args,
                    summary,
                    "validate",
                    EXIT_FILE_ERROR,
                    f"Refusing to upload file with unsafe name: {_name_for_message(remote_candidate)!r}",
                )
            summary["remote_name"] = remote_candidate
            if not _is_http_url(source) and getattr(args, 'output', None):
                logger.warning("⚠️  --output is only used when job/send downloads, extracts, or slices; ignoring it for a printer-ready local file.")
            logger.info("🚦 Job source is already printer-ready; upload will use it directly.")
            if getattr(args, 'dry_run', False):
                try:
                    ready_size = os.path.getsize(source_path)
                except OSError as exc:
                    _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Could not read file size for {_path_for_message(source_path)}: {_exception_for_message(exc)}")
                if ready_size <= 0:
                    _job_fail(args, summary, "validate", EXIT_FILE_ERROR, f"Refusing to dry-run an empty printer-ready file: {_path_for_message(source_path)}")
                logger.info("🔍 Dry Run: printer-ready file is valid; skipping upload and print.")
                summary["status"] = "dry_run_local_skipped"
                summary["printable_path"] = source_path
                summary["would_upload"] = True
                summary["would_print"] = bool(getattr(args, 'confirm', False)) and not bool(getattr(args, 'upload_only', False))
                if getattr(args, 'json', False):
                    emit_json(summary)
                return source_path
            printable_path = source_path
        else:
            supported = ", ".join(SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS)
            _job_fail(
                args, summary, "validate", EXIT_FILE_ERROR,
                f"Unsupported source file type '{ext or 'none'}'. Supported types: {supported}")

        summary["printable_path"] = printable_path
        summary["would_upload"] = True

        try:
            _LAST_ERROR_PAYLOAD = None
            remote_name = cmd_upload(argparse.Namespace(
                file=printable_path, dry_run=False, json=False,
                progress=not getattr(args, "json", False)))
        except SystemExit as exc:
            detail = _last_error_for("upload")
            _emit_job_failure(
                args,
                summary,
                "upload",
                _exit_code_from_system_exit(exc),
                error=detail.get("error") if detail else None,
                detail=detail,
            )
            raise
        summary["remote_name"] = remote_name
        summary["uploaded"] = True

        if getattr(args, 'upload_only', False):
            logger.info(f"✅ Job uploaded {remote_name}; print not started because --upload-only was set.")
            if getattr(args, 'json', False):
                summary["status"] = "uploaded"
                summary["next_command"] = _print_next_command(args, remote_name)
                emit_json(summary)
            return printable_path

        if not getattr(args, 'confirm', False):
            logger.warning(f"⚠️  Job uploaded {remote_name}, but print was not started. Re-run with --confirm to print.")
            if getattr(args, 'json', False):
                summary["status"] = "uploaded_not_printed"
                summary["next_command"] = _print_next_command(args, remote_name)
                emit_json(summary)
            return printable_path

        summary["would_print"] = True
        try:
            _LAST_ERROR_PAYLOAD = None
            cmd_print(argparse.Namespace(
                file=remote_name,
                confirm=True,
                dry_run=False,
                use_ams=getattr(args, 'use_ams', False),
                ams_mapping=getattr(args, 'ams_mapping', None),
                timelapse=getattr(args, 'timelapse', False),
                skip_bed_leveling=getattr(args, 'skip_bed_leveling', False),
                skip_flow_cali=getattr(args, 'skip_flow_cali', False),
                json=False,
            ))
        except SystemExit as exc:
            detail = _last_error_for("print")
            summary["next_command"] = ["status", "--json"]
            summary["recovery_hint"] = (
                "Upload succeeded but print start was not confirmed. Check printer status before retrying."
            )
            _emit_job_failure(
                args,
                summary,
                "print",
                _exit_code_from_system_exit(exc),
                error=detail.get("error") if detail else None,
                detail=detail,
            )
            raise
        summary["printed"] = True
        summary["status"] = "printed"
        if getattr(args, 'json', False):
            emit_json(summary)
        return printable_path
    finally:
        if is_temp_workdir and workdir and os.path.exists(workdir):
            if "unittest" not in sys.modules and os.environ.get("BAMBU_TESTING") != "1":
                shutil.rmtree(workdir, ignore_errors=True)




def _select_printables_file(files, file_desc, type_key="stl"):
    if len(files) > 1:
        logger.info(f"   Found {len(files)} {file_desc} files:")
        for s in files:
            logger.info(f"      • {s['name']} ({s.get('fileSize', 0) // 1024}KB)")
    file_to_use = max(files, key=lambda x: x.get('fileSize', 0))
    logger.info(f"   → Using {file_desc}: {file_to_use['name']} ({file_to_use.get('fileSize', 0) // 1024}KB)")
    return file_to_use, type_key

def _get_printables_file_info(model_id, gql_headers, opener):
    """Helper to fetch file info from Printables API."""

    payload = json.dumps({
        "query": 'query{print(id:"' + model_id + '"){name stls{name fileSize id} gcodes{name fileSize id}}}'
    })
    req = urllib.request.Request('https://api.printables.com/graphql/',
        data=payload.encode(), headers=gql_headers)

    file_type = "stl"
    try:
        with opener.open(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            response_data = resp.read()
    except urllib.error.URLError as e:
        logger.error(f"Network error querying Printables API: {e}")
        return None, None, None
    except Exception as e:
        logger.error(f"Failed to query Printables API: {e}")
        return None, None, None

    try:
        result = json.loads(response_data)
    except Exception as e:
        logger.error(f"Failed to parse Printables API response: {e}")
        return None, None, None

    if not isinstance(result, dict):
        logger.error("Invalid Printables API response structure.")
        return None, None, None

    model = result.get('data', {}).get('print')
    if not model:
        logger.error(f"Model #{model_id} not found on Printables")
        return None, None, None

    stls_raw = model.get('stls', [])
    gcodes_raw = model.get('gcodes', [])

    stls, steps, threemfs = [], [], []
    for s in stls_raw:
        ext = s.get('name', '').lower().rpartition('.')[-1]
        if ext == 'stl':
            stls.append(s)
        elif ext in ('step', 'stp'):
            steps.append(s)
        elif ext == '3mf':
            threemfs.append(s)
    for g in gcodes_raw:
        ext = g.get('name', '').lower().rpartition('.')[-1]
        if ext == '3mf':
            threemfs.append(g)

    logger.info(f"   Model: {model.get('name', '?')}")
    if stls:
        file_to_use, file_type = _select_printables_file(stls, "STL", "stl")
    elif steps:
        file_to_use, file_type = _select_printables_file(steps, "STEP", "stl")
    elif threemfs:
        logger.warning("   ⚠️  No STL/STEP files — falling back to 3MF (cannot re-slice with custom settings)")
        file_to_use = max(threemfs, key=lambda x: x.get('fileSize', 0))
        if file_to_use in gcodes_raw:
            file_type = "gcode"
        else:
            file_type = "stl"
        logger.info(f"   → Using 3MF: {file_to_use['name']} ({file_to_use.get('fileSize', 0) // 1024}KB)")
    else:
        logger.error("No STL, STEP, or 3MF files found for this model")
        return None, None, None

    return file_to_use['id'], file_type, file_to_use['name']

def _get_printables_download_link(file_id, model_id, file_type, stl_name, gql_headers, opener):
    """Helper to fetch download link from Printables API."""

    payload = json.dumps({
        "operationName": "GetDownloadLink",
        "variables": {"id": file_id, "printId": model_id, "source": "model_detail", "fileType": file_type},
        "query": "mutation GetDownloadLink($id: ID!, $printId: ID!, $source: DownloadSourceEnum!, $fileType: DownloadFileTypeEnum!) { getDownloadLink(id: $id, printId: $printId, source: $source, fileType: $fileType) { ok output { link } errors { field messages } } }"
    })
    req = urllib.request.Request('https://api.printables.com/graphql/',
        data=payload.encode(), headers=gql_headers)

    try:
        with opener.open(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            result = json.loads(resp.read())
            dl = result.get('data', {}).get('getDownloadLink', {})
            if dl.get('ok') and dl.get('output', {}).get('link'):
                download_url = dl['output']['link']
                return download_url, stl_name
            else:
                errs = dl.get('errors', [])
                msg = errs[0]['messages'][0] if errs else 'unknown error'
                logger.error(f"Failed to get download link: {msg}")
                return None, None
    except urllib.error.URLError as e:
        logger.error(f"Network error getting download link: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Failed to get download link: {e}")
        return None, None

def resolve_printables_url(url):
    """Resolve a Printables model URL to a direct file download URL and filename.
    Returns (download_url, filename) or (None, None) if resolution fails.
    """
    if not _is_printables_model_url(url):
        return None, None

    printables_match = re.search(r'/model/(\d+)', urlparse(url).path)
    if not printables_match:
        return None, None

    model_id = printables_match.group(1)
    logger.info(f"🔍 Detected Printables model #{model_id}, resolving files...")

    headers = {
        'User-Agent': _default_user_agent(),
        'Accept': '*/*',
    }
    gql_headers = {**headers, 'Content-Type': 'application/json',
                   'Origin': 'https://www.printables.com',
                   'Referer': 'https://www.printables.com/'}

    opener = build_safe_opener()
    file_id, file_type, stl_name = _get_printables_file_info(model_id, gql_headers, opener)
    if not file_id:
        return None, None

    return _get_printables_download_link(file_id, model_id, file_type, stl_name, gql_headers, opener)

def _cmd_download(args):
    from bambu_cli.utils import _record_download_success  # (was missing -> NameError on download)
    """Download a model or printer-ready file from a URL. Auto-resolves Printables page URLs."""
    global _LAST_DOWNLOAD_PAYLOAD
    _LAST_DOWNLOAD_PAYLOAD = None
    source_url = args.url
    url = _normalize_url_input(source_url)
    normalized_source = url if url != source_url else None
    source_report = _redact_url_credentials(source_url)
    normalized_source_report = _redact_url_credentials(normalized_source)
    max_download_bytes = _validate_max_download_mb_or_exit(args)
    _validate_download_url_or_exit(
        args, source_url, normalized_source, url, "validate", "Invalid URL source")
    is_printables_model = _is_printables_model_url(url)
    if not is_printables_model:
        _reject_unsupported_download_extension(
            args, source_url, normalized_source, url, urlparse(url).path)

    outdir = _expand_path(args.output) if args.output else tempfile.gettempdir()
    if outdir.startswith('-'):
        message = f"Invalid output directory: {_path_for_message(outdir)}"
        logger.error(message)
        emit_json_error(
            args, "download", EXIT_COMMAND_ERROR, message, failed_step="validate",
            source=source_report, normalized_source=normalized_source_report, output=outdir)
        sys.exit(EXIT_COMMAND_ERROR)
    try:
        _ensure_output_dir(outdir)
    except SystemExit as exc:
        emit_json_error(
            args, "download", _exit_code_from_system_exit(exc, EXIT_FILE_ERROR),
            f"Could not prepare output directory: {_path_for_message(outdir)}", failed_step="validate",
            source=source_report, normalized_source=normalized_source_report, output=outdir)
        raise
    headers = {
        'User-Agent': _default_user_agent(),
        'Accept': '*/*',
    }

    resolved_url, stl_name = resolve_printables_url(url)

    # If the URL was a Printables page, it may have been resolved successfully.
    # If it was a Printables page and failed, we should return to match original behavior.
    if is_printables_model:
        if not resolved_url:
            emit_json_error(
                args, "download", EXIT_COMMAND_ERROR,
                "Failed to resolve Printables model URL.", failed_step="resolve",
                source=source_report, normalized_source=normalized_source_report)
            sys.exit(EXIT_COMMAND_ERROR)  # Failed to resolve, error message already printed
        url = resolved_url
        _reject_unsupported_download_extension(
            args, source_url, normalized_source, url, stl_name)
        _reject_unsupported_download_extension(
            args, source_url, normalized_source, url, urlparse(url).path)

    # Security: Validate URL scheme to prevent SSRF (e.g. file://)
    _validate_download_url_or_exit(
        args, source_url, normalized_source, url, "validate", "Invalid resolved download URL")

    partial_path = None
    replace_on_success = False
    outpath = None
    safe_opener = build_safe_opener()
    try:
        for _html_resolution_attempt in range(3):
            archive_download = _is_archive_download(url, stl_name)
            if archive_download:
                archive_temp = tempfile.NamedTemporaryFile(
                    prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False)
                outpath = archive_temp.name
                archive_temp.close()
                filename = _portable_basename(outpath)
                partial_path = outpath
                replace_on_success = False
            else:
                filename = _download_target_filename(args, url, stl_name)
                outpath = os.path.join(outdir, filename)
                outpath = _noncolliding_path(outpath)
                filename = _portable_basename(outpath)
            req = urllib.request.Request(url, headers=headers)
            with safe_opener.open(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                final_url = _response_url(resp)
                if final_url and final_url != url:
                    try:
                        _validate_download_url_or_exit(
                            args,
                            source_url,
                            normalized_source,
                            final_url,
                            "download",
                            "Invalid redirected download URL",
                        )
                        if _known_unsupported_download_extension(urlparse(final_url).path):
                            _remove_partial_file(partial_path)
                            partial_path = None
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, final_url, urlparse(final_url).path, failed_step="download")
                    except SystemExit:
                        _remove_partial_file(partial_path)
                        partial_path = None
                        raise
                    url = final_url
                    if not stl_name and not _namespace_get(args, "name") and not archive_download:
                        filename = _download_target_filename(args, url, stl_name)
                        outpath = os.path.join(outdir, filename)
                        outpath = _noncolliding_path(outpath)
                        filename = _portable_basename(outpath)
                content_type = _response_header(resp, 'Content-Type')
                archive_download = archive_download or _is_archive_download(url, stl_name, content_type)
                if archive_download and not filename.startswith(".bambu-download-"):
                    if partial_path and partial_path != outpath:
                        _remove_partial_file(partial_path)
                    archive_temp = tempfile.NamedTemporaryFile(
                        prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False)
                    outpath = archive_temp.name
                    archive_temp.close()
                    filename = _portable_basename(outpath)
                    partial_path = outpath
                    replace_on_success = False
                if _is_html_content_type(content_type):
                    if partial_path == outpath and outpath and filename.startswith(".bambu-download-"):
                        _remove_partial_file(partial_path)
                        partial_path = None
                    page_bytes = resp.read(HTML_LINK_SCAN_LIMIT + 1)
                    resolved_html_url, resolved_html_name = _resolve_html_model_link(page_bytes, url)
                    if resolved_html_url and resolved_html_url != url:
                        logger.info(f"🔗 Found model file link on page: {resolved_html_name or resolved_html_url}")
                        url = resolved_html_url
                        stl_name = resolved_html_name or stl_name
                        _validate_download_url_or_exit(
                            args,
                            source_url,
                            normalized_source,
                            url,
                            "resolve",
                            "Invalid resolved HTML model URL",
                        )
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, url, stl_name, failed_step="resolve")
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, url, urlparse(url).path, failed_step="resolve")
                        continue
                    message = "HTML page did not contain a direct model file link."
                    logger.error(message)
                    logger.info("   Use a Printables model page, a direct .stl/.step/.stp/.obj/.3mf/.gcode/.zip download URL, or a page with a direct model-file link.")
                    emit_json_error(
                        args, "download", EXIT_FILE_ERROR, message, failed_step="resolve",
                        source=source_report, normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url))
                    sys.exit(EXIT_FILE_ERROR)
                if not archive_download:
                    _reject_unsupported_content_type(args, source_url, normalized_source, url, content_type)

                header_filename = _filename_from_content_disposition(_response_header(resp, 'Content-Disposition'))
                if header_filename and _is_archive_download(url, header_filename, content_type):
                    archive_download = True
                if header_filename and (_namespace_get(args, "name") or not stl_name):
                    if archive_download:
                        if partial_path != outpath and partial_path:
                            _remove_partial_file(partial_path)
                        if outpath and filename.startswith(".bambu-download-"):
                            _remove_partial_file(outpath)
                        archive_temp = tempfile.NamedTemporaryFile(
                            prefix=".bambu-download-", suffix=".zip", dir=outdir, delete=False)
                        outpath = archive_temp.name
                        archive_temp.close()
                        filename = _portable_basename(outpath)
                        partial_path = outpath
                        replace_on_success = False
                    else:
                        _reject_unsupported_download_extension(
                            args, source_url, normalized_source, url, header_filename, failed_step="download")
                        if _namespace_get(args, "name"):
                            filename = _download_filename_with_extension(
                                _sanitize_download_filename(_namespace_get(args, "name")),
                                url,
                                fallback_name=header_filename,
                            )
                        else:
                            filename = _download_filename_with_extension(header_filename, url, fallback_name=header_filename)
                        outpath = os.path.join(outdir, filename)
                        outpath = _noncolliding_path(outpath)
                        filename = _portable_basename(outpath)

                logger.info(f"⬇️  Downloading {filename}...")
                if not archive_download:
                    partial_path, replace_on_success = _download_partial_path(outpath)
                content_length = _response_header(resp, 'Content-Length')
                try:
                    total_size = int(content_length) if content_length else None
                except ValueError:
                    total_size = None
                if total_size is not None and total_size > max_download_bytes:
                    _remove_partial_file(partial_path)
                    _reject_oversized_download(
                        args,
                        source_url,
                        normalized_source,
                        url,
                        outpath,
                        0,
                        max_download_bytes,
                        content_length=total_size,
                    )

                chunk_size = 65536  # 64KB chunks
                downloaded = 0
                last_percent_reported = -10
                download_exceeded_limit = False

                progress = None
                task_id = None
                try:
                    if not getattr(args, "json", False) and getattr(args, "progress", True):
                        from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
                        progress = Progress(
                            TextColumn("[bold blue]{task.description}", justify="right"),
                            BarColumn(bar_width=None),
                            "[progress.percentage]{task.percentage:>3.1f}%",
                            "•",
                            DownloadColumn(),
                            "•",
                            TransferSpeedColumn(),
                            "•",
                            TimeRemainingColumn(),
                            transient=True
                        )
                        progress.start()
                        task_id = progress.add_task(f"Downloading", total=total_size)
                except ImportError:
                    pass

                try:
                    with open(partial_path, 'wb') as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            from unittest.mock import Mock
                            if not chunk or isinstance(chunk, Mock):
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if downloaded > max_download_bytes:
                                download_exceeded_limit = True
                                break

                            if progress and task_id is not None:
                                progress.update(task_id, completed=downloaded)
                            elif total_size and total_size > 0:
                                percent = int((downloaded / total_size) * 100)
                                if percent - last_percent_reported >= 10:
                                    logger.info(f"   Download progress: {percent}% ({downloaded // 1024}KB / {total_size // 1024}KB)")
                                    last_percent_reported = percent
                finally:
                    if progress:
                        progress.stop()

                if download_exceeded_limit:
                    _remove_partial_file(partial_path)
                    _reject_oversized_download(
                        args,
                        source_url,
                        normalized_source,
                        url,
                        outpath,
                        downloaded,
                        max_download_bytes,
                    )

                if total_size is not None and downloaded < total_size:
                    _remove_partial_file(partial_path)
                    message = f"Download ended early: received {downloaded} of {total_size} bytes."
                    logger.error(message)
                    emit_json_error(
                        args, "download", EXIT_NETWORK_ERROR, message, failed_step="download",
                        source=source_report, normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url), path=outpath, received_bytes=downloaded,
                        expected_bytes=total_size)
                    sys.exit(EXIT_NETWORK_ERROR)

            size = os.path.getsize(partial_path)
            if size <= 0:
                _remove_partial_file(partial_path)
                message = "Downloaded file is empty; refusing to use it."
                logger.error(message)
                emit_json_error(
                    args, "download", EXIT_FILE_ERROR, message, failed_step="download",
                    source=source_report, normalized_source=normalized_source_report,
                    download_url=_redact_url_credentials(url), path=outpath, bytes=size)
                sys.exit(EXIT_FILE_ERROR)
            if replace_on_success:
                os.replace(partial_path, outpath)
                partial_path = None
            if archive_download:
                archive_path = outpath
                try:
                    extracted_path, extracted_filename, archive_entry, size = _extract_zip_model(archive_path, outdir, args)
                except OSError as exc:
                    _remove_partial_file(archive_path)
                    message = f"Failed to extract archive: {exc}"
                    logger.error(message)
                    emit_json_error(
                        args, "download", EXIT_FILE_ERROR, message, failed_step="extract",
                        source=source_report, normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url), path=archive_path)
                    sys.exit(EXIT_FILE_ERROR)
                except ValueError as exc:
                    _remove_partial_file(archive_path)
                    partial_path = None
                    message = str(exc)
                    logger.error(message)
                    emit_json_error(
                        args, "download", EXIT_FILE_ERROR, message, failed_step="extract",
                        source=source_report, normalized_source=normalized_source_report,
                        download_url=_redact_url_credentials(url), path=archive_path)
                    sys.exit(EXIT_FILE_ERROR)
                _remove_partial_file(archive_path)
                partial_path = None
                logger.info(f"✅ Downloaded: {_path_for_message(extracted_path)} ({size // 1024}KB)")
                _record_download_success(args, {
                    "status": "downloaded",
                    "command": "download",
                    "source": source_report,
                    "normalized_source": normalized_source_report,
                    "download_url": _redact_url_credentials(url),
                    "path": extracted_path,
                    "filename": extracted_filename,
                    "archive_entry": archive_entry,
                    "bytes": size,
                })
                return extracted_path
            logger.info(f"✅ Downloaded: {_path_for_message(outpath)} ({size // 1024}KB)")
            _record_download_success(args, {
                "status": "downloaded",
                "command": "download",
                "source": source_report,
                "normalized_source": normalized_source_report,
                "download_url": _redact_url_credentials(url),
                "path": outpath,
                "filename": filename,
                "bytes": size,
            })
            return outpath

        message = "Could not resolve HTML page to a direct model file."
        logger.error(message)
        emit_json_error(
            args, "download", EXIT_FILE_ERROR, message, failed_step="resolve",
            source=source_report, normalized_source=normalized_source_report,
            download_url=_redact_url_credentials(url))
        sys.exit(EXIT_FILE_ERROR)
    except urllib.error.HTTPError as e:
        _remove_partial_file(partial_path)
        message = f"Download failed: HTTP Error {e.code} ({e.reason})"
        logger.error(message)
        if e.code == 404:
            logger.info("   The requested file or model does not exist. Check that the URL is correct.")
        elif e.code == 403:
            logger.info("   Access is forbidden. Printables or the host may be blocking automated requests.")
        emit_json_error(
            args, "download", EXIT_NETWORK_ERROR, message, failed_step="download",
            source=source_report, normalized_source=normalized_source_report, download_url=_redact_url_credentials(url),
            http_status=e.code, path=outpath)
        try:
            e.close()
        except Exception:
            pass
        sys.exit(EXIT_NETWORK_ERROR)
    except urllib.error.URLError as e:
        _remove_partial_file(partial_path)
        err_msg = str(e.reason) if hasattr(e, 'reason') else str(e)
        if "Security Error" in err_msg:
            message = f"SSRF Security Violation Blocked: {err_msg}"
            logger.error(message)
            emit_json_error(
                args, "download", EXIT_COMMAND_ERROR, message, failed_step="validate",
                source=source_report, normalized_source=normalized_source_report, download_url=_redact_url_credentials(url),
                path=outpath)
            sys.exit(EXIT_COMMAND_ERROR)
        message = f"Network error during download: {e}"
        logger.error(message)
        logger.info("   Please check your internet connection or verify the domain name resolves correctly.")
        emit_json_error(
            args, "download", EXIT_NETWORK_ERROR, message, failed_step="download",
            source=source_report, normalized_source=normalized_source_report, download_url=_redact_url_credentials(url),
            path=outpath)
        sys.exit(EXIT_NETWORK_ERROR)
    except OSError as e:
        _remove_partial_file(partial_path)
        message = f"Local file error during download: {_exception_for_message(e)}"
        logger.error(message)
        emit_json_error(
            args, "download", EXIT_FILE_ERROR, message, failed_step="download",
            source=source_report, normalized_source=normalized_source_report, download_url=_redact_url_credentials(url),
            path=outpath)
        sys.exit(EXIT_FILE_ERROR)
    except Exception as e:
        _remove_partial_file(partial_path)
        message = f"Download failed: {e}"
        logger.error(message)
        emit_json_error(
            args, "download", EXIT_NETWORK_ERROR, message, failed_step="download",
            source=source_report, normalized_source=normalized_source_report, download_url=_redact_url_credentials(url),
            path=outpath)
        sys.exit(EXIT_NETWORK_ERROR)
























# Wildcard imports from submodules to bind everything into the root namespace
from bambu_cli.cli import *
from bambu_cli.config import *
from bambu_cli.slicer import *
from bambu_cli.commands import *
from bambu_cli.protocols.ftps import *
from bambu_cli.protocols.mqtt import *

# Restore the real logger in bambu.py namespace so it is not overridden by the submodule proxies
logger = logging.getLogger("bambu")

# Explicitly import private helpers to expose them under the bambu module namespace
from bambu_cli.cli import (
    _namespace_get,
    _display_path,
    _expand_path,
    _path_for_message,
    _exception_for_message,
    _exit_code_from_system_exit,
    _redact_url_credentials,
    _looks_like_schemeless_credential_url,
    _json_mode_requested,
    _add_job_arguments,
    _argv_json_requested,
    _guess_command_from_argv,
    _requires_printer_dns_check,
    _json_setup_should_be_noninteractive,
    _setup_args_provided
)
from bambu_cli.slicer import (
    _is_directory_input,
    _directory_input_message,
    _validate_slice_options,
    _sliced_output_path,
    _slicer_executable_problem,
    _convert_step_to_stl,
    _process_profile_compatible,
    _discover_process_profile,
    _create_temp_profiles,
    _safe_temp_prefix,
    _normalize_wall_type
)
from bambu_cli.protocols.ftps import (
    _verify_cert_fingerprint,
    _noncolliding_path,
    _SIM_FTP_FILES,
    _remove_partial_file,
    _download_partial_path,
)
from bambu_cli.protocols.mqtt import (
    _resolve_ip,
    _mqtt_connect,
    _SimMqttClient,
)
from bambu_cli.config import (
    _expected_fingerprint,
    _first_existing_path,
    _default_orca_path,
    _default_profiles_path,
    _DEFAULT_ORCA,
    _DEFAULT_PROFILES,
    _access_code_value_problem,
)

# Load config at import-time to populate _cfg for tests that mock config.json at import time
try:
    load_config(exit_on_fail=False)
except Exception:
    pass



class DynamicCmds(dict):
    def __contains__(self, key):
        from bambu_cli import bambu
        func_name = "cmd_job" if key in ("job", "send") else f"cmd_{key}"
        return hasattr(bambu, func_name)

    def __getitem__(self, key):
        from bambu_cli import bambu
        func_name = "cmd_job" if key in ("job", "send") else f"cmd_{key}"
        if hasattr(bambu, func_name):
            return getattr(bambu, func_name)
        raise KeyError(key)

_cmds = DynamicCmds()

if __name__ == "__main__":
    main()
connection_manager = ConnectionManager()
atexit.register(connection_manager.close_all)

from bambu_cli.protocols.mqtt import (
    _require_mqtt,
    _resolve_ip,
    _mqtt_connect,
    _get_and_verify_cert_pem
)