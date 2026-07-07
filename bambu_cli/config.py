import os
import sys
import json

from bambu_cli.logging_utils import logger

def _default_config_path():
    """Return the platform-native default config path, preferring an existing
    legacy ``~/.config/bambu/config.json`` for back-compat across installs."""
    from bambu_cli.cli import _expand_path
    if os.environ.get("XDG_CONFIG_HOME") and sys.platform not in ("win32", "darwin"):
        return os.path.join(_expand_path(os.environ["XDG_CONFIG_HOME"]), "bambu", "config.json")
    legacy = _expand_path(os.path.join("~", ".config", "bambu", "config.json"))
    if os.path.exists(legacy):
        return legacy
    if sys.platform == "win32":
        base = _expand_path(os.environ.get("APPDATA") or os.path.join("~", "AppData", "Roaming"))
        return os.path.join(base, "bambu", "config.json")
    if sys.platform == "darwin":
        return _expand_path(os.path.join("~", "Library", "Application Support", "bambu", "config.json"))
    xdg = _expand_path(os.environ.get("XDG_CONFIG_HOME") or os.path.join("~", ".config"))
    return os.path.join(xdg, "bambu", "config.json")

CONFIG_PATH = _default_config_path()

def load_config(exit_on_fail=True):
    """Load printer config from the platform-native config path."""
    from bambu_cli import bambu
    from bambu_cli.cli import _display_path, _exception_for_message
    from bambu_cli.constants import EXIT_CONFIG_ERROR
    config_path = getattr(bambu, "CONFIG_PATH", CONFIG_PATH)
    if not os.path.exists(config_path):
        if not exit_on_fail:
            return None
        logger.error(f"Config not found. Create {_display_path(config_path)}:")
        if sys.platform == "win32":
            orca_example = r"C:\Program Files\OrcaSlicer\OrcaSlicer.exe"
            profiles_example = r"C:\Program Files\OrcaSlicer\resources\profiles\BBL"
        elif sys.platform == "darwin":
            orca_example = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"
            profiles_example = "/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL"
        else:
            orca_example = "~/tools/OrcaSlicer.AppImage"
            profiles_example = "~/tools/squashfs-root/resources/profiles/BBL"
        logger.info(json.dumps({
            "printer_ip": "192.168.0.XXX",
            "serial": "YOUR_PRINTER_SERIAL",
            "access_code_file": "~/.config/bambu/access_code",
            "orca_slicer": orca_example,
            "profiles_dir": profiles_example
        }, indent=2))
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        if sys.platform != "win32":
            try:
                st = os.stat(config_path)
                if st.st_mode & 0o077:
                    logger.warning(f"⚠️  Config file '{_display_path(config_path)}' has insecure, world-readable permissions! "
                                   "It is highly recommended to restrict access: run 'chmod 600' on this file.")
                    os.chmod(config_path, 0o600)
                    logger.info(f"🔒 Automatically enforced 0600 permissions on {config_path}")
            except OSError as e:
                if not exit_on_fail:
                    return None
                logger.error(f"❌ Failed to enforce secure permissions on config file: {e}")
                from bambu_cli.constants import EXIT_CONFIG_ERROR
                sys.exit(EXIT_CONFIG_ERROR)
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
            apply_config(cfg)
            return cfg

    except Exception as e:
        if not exit_on_fail:
            return None
        logger.error(f"Error loading config: {_exception_for_message(e)}")
        sys.exit(EXIT_CONFIG_ERROR)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _first_existing_path(candidates):
    """Return the first existing path, otherwise the first candidate expanded."""
    from bambu_cli.cli import _expand_path
    expanded = [_expand_path(path) for path in candidates if path]
    if not expanded:
        return None
    for path in expanded:
        if os.path.exists(path):
            return path
    return expanded[0]

def _default_orca_path():
    """Return the platform-native default OrcaSlicer binary path."""
    if sys.platform == "darwin":
        return _first_existing_path([
            "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
            "~/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        ])
    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "OrcaSlicer", "OrcaSlicer.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.join("~", "AppData", "Local")), "Programs", "OrcaSlicer", "OrcaSlicer.exe"),
        ]
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(os.path.join(program_files_x86, "OrcaSlicer", "OrcaSlicer.exe"))
        return _first_existing_path(candidates)
    return _first_existing_path([
        "~/tools/OrcaSlicer.AppImage",
        os.path.join(_SCRIPT_DIR, "..", "tools", "OrcaSlicer.AppImage"),
    ])

def _default_profiles_path():
    """Return the platform-native default OrcaSlicer profiles directory."""
    if sys.platform == "darwin":
        return _first_existing_path([
            "/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL",
            "~/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL",
        ])
    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "OrcaSlicer", "resources", "profiles", "BBL"),
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.join("~", "AppData", "Local")), "Programs", "OrcaSlicer", "resources", "profiles", "BBL"),
        ]
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(os.path.join(program_files_x86, "OrcaSlicer", "resources", "profiles", "BBL"))
        return _first_existing_path(candidates)
    return _first_existing_path([
        "~/tools/squashfs-root/resources/profiles/BBL",
        os.path.join(_SCRIPT_DIR, "..", "tools", "squashfs-root", "resources", "profiles", "BBL"),
    ])

_DEFAULT_ORCA = _default_orca_path()
_DEFAULT_PROFILES = _default_profiles_path()
DEFAULT_PRINTER_IP = "0.0.0.0"
DEFAULT_SERIAL = "UNKNOWN"
DEFAULT_MQTT_PORT = 8883
DEFAULT_PRINTER_MODEL = "P1P"
DEFAULT_NOZZLE_SIZE = "0.4"
DEFAULT_CAMERA_IMAGE = "bambu_p1_streamer"
DEFAULT_CAMERA_CONTAINER_NAME = "bambu_camera"
DEFAULT_CAMERA_PORT = "1985:1984"


MODEL_MAPPING = {
    "P1P": {"token": "P1P", "full_name": "Bambu Lab P1P"},
    "P1S": {"token": "P1S", "full_name": "Bambu Lab P1S"},
    "X1C": {"token": "X1C", "full_name": "Bambu Lab X1 Carbon"},
    "X1":  {"token": "X1",  "full_name": "Bambu Lab X1"},
    "X1E": {"token": "X1E", "full_name": "Bambu Lab X1E"},
    "A1":  {"token": "A1",  "full_name": "Bambu Lab A1"},
    "A1M": {"token": "A1M", "full_name": "Bambu Lab A1 mini"}
}

def apply_config(cfg):
    """Dynamically apply a configuration dictionary to the global state."""
    from bambu_cli import bambu
    from bambu_cli.cli import _expand_path
    if not cfg:
        return
    bambu._cfg = cfg
    bambu.PRINTER_IP = cfg.get("printer_ip", DEFAULT_PRINTER_IP)
    bambu.SERIAL = cfg.get("serial", DEFAULT_SERIAL)
    bambu.MQTT_PORT = cfg.get("mqtt_port", DEFAULT_MQTT_PORT)
    bambu.INSECURE_TLS = cfg.get("insecure_tls", False)
    if bambu.INSECURE_TLS:
        logger.warning("🚨 SECURITY WARNING: 'insecure_tls' is enabled! TLS certificate validation is DISABLED for all connections. Your network traffic is vulnerable to MITM attacks.")
    bambu.ORCA_SLICER = _expand_path(cfg.get("orca_slicer", _DEFAULT_ORCA or ""))
    bambu.PROFILES_DIR = _expand_path(cfg.get("profiles_dir", _DEFAULT_PROFILES or ""))
    bambu.PRINTER_MODEL = cfg.get("model", cfg.get("printer_model", DEFAULT_PRINTER_MODEL)).upper()
    bambu.NOZZLE_SIZE = str(cfg.get("nozzle", cfg.get("nozzle_size", DEFAULT_NOZZLE_SIZE)))
    bambu.CAMERA_IMAGE = cfg.get("camera_image", DEFAULT_CAMERA_IMAGE)
    bambu.CAMERA_CONTAINER_NAME = cfg.get("camera_container_name", DEFAULT_CAMERA_CONTAINER_NAME)
    bambu.CAMERA_PORT = cfg.get("camera_port", DEFAULT_CAMERA_PORT)
    
    host_port = bambu.CAMERA_PORT.split(":")[0]
    bambu.CAMERA_STREAM_URL = cfg.get("camera_stream_url", f"http://localhost:{host_port}/api/frame.jpeg?src=p1s")


def load_access_code():
    from bambu_cli import bambu
    from bambu_cli.cli import _expand_path, _display_path, _exception_for_message
    from bambu_cli.constants import EXIT_CONFIG_ERROR
    if "access_code" in bambu._cfg:
        access_code = str(bambu._cfg["access_code"]).strip()
        problem = _access_code_value_problem(access_code)
        if problem:
            logger.error(problem)
            sys.exit(EXIT_CONFIG_ERROR)
        return access_code
    if "access_code_file" in bambu._cfg:
        path = _expand_path(bambu._cfg["access_code_file"])
        try:
            with open(path, encoding="utf-8") as f:
                access_code = f.read().strip()
        except FileNotFoundError:
            logger.error(f"Access code file not found: {_display_path(path)}")
            sys.exit(EXIT_CONFIG_ERROR)
        except OSError as exc:
            logger.error(f"Access code file could not be read: {_exception_for_message(exc)}")
            sys.exit(EXIT_CONFIG_ERROR)
        problem = _access_code_value_problem(access_code)
        if problem:
            logger.error(problem)
            sys.exit(EXIT_CONFIG_ERROR)
        return access_code
    logger.error("No 'access_code' or 'access_code_file' in config.json")
    sys.exit(EXIT_CONFIG_ERROR)


def _access_code_value_problem(value):
    normalized = str(value or "").strip().upper()
    if not normalized or normalized in {"ACCESS_CODE", "YOUR_ACCESS_CODE", "USER_PROVIDED_ACCESS_CODE"} or normalized.startswith("YOUR_"):
        return "Config must contain a real access_code or access_code_file."
    return None


def load_username():
    """Return the MQTT username from config, defaulting to 'bblp'."""
    from bambu_cli import bambu
    return bambu._cfg.get("username", "bblp")


def _expected_fingerprint():
    """Return the normalized (lowercase, separator-free) pinned SHA-256, or None."""
    from bambu_cli import bambu
    fp = bambu._cfg.get("cert_fingerprint")
    if not fp:
        return None
    return fp.lower().replace(":", "").replace(" ", "")


def fingerprint_sha256(der_cert):
    """Hex SHA-256 of a DER-encoded certificate, or None if no cert."""
    import hashlib
    if not der_cert:
        return None
    return hashlib.sha256(der_cert).hexdigest()


def _timeout_from(args, key, default):
    """Resolve a timeout from CLI args, then config, then the default."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get
    if args:
        val = _namespace_get(args, key)
        if val is not None:
            return float(val)
    if bambu._cfg:
        val = bambu._cfg.get(key)
        if val is not None:
            return float(val)
    return default


def get_network_timeout(args=None):
    from bambu_cli.constants import DEFAULT_NETWORK_TIMEOUT
    return _timeout_from(args, "network_timeout", DEFAULT_NETWORK_TIMEOUT)


def get_slicer_timeout(args=None):
    from bambu_cli.constants import SLICER_TIMEOUT
    return _timeout_from(args, "slicer_timeout", SLICER_TIMEOUT)


def get_command_timeout(args=None):
    from bambu_cli.constants import COMMAND_TIMEOUT
    return _timeout_from(args, "command_timeout", COMMAND_TIMEOUT)


def get_upload_timeout(args=None):
    from bambu_cli.constants import UPLOAD_TIMEOUT
    return _timeout_from(args, "upload_timeout", UPLOAD_TIMEOUT)
