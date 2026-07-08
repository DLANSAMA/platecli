import json
import os
import shutil
import sys

from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger


def _default_config_path():  # pragma: no cover -- platform paths
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


def load_config(exit_on_fail=True):  # pragma: no cover -- config load I/O; Settings.from_config unit-tested
    """Load printer config from the platform-native config path."""
    from bambu_cli import bambu
    from bambu_cli.cli import _display_path, _exception_for_message
    from bambu_cli.constants import EXIT_CONFIG_ERROR
    from bambu_cli.errors import abort

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
        logger.info(
            json.dumps(
                {
                    "printer_ip": "192.168.0.XXX",
                    "serial": "YOUR_PRINTER_SERIAL",
                    "access_code_file": "~/.config/bambu/access_code",
                    "orca_slicer": orca_example,
                    "profiles_dir": profiles_example,
                },
                indent=2,
            )
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        if sys.platform != "win32":
            try:
                st = os.stat(config_path)
                if st.st_mode & 0o077:
                    logger.warning(
                        f"⚠️  Config file '{_display_path(config_path)}' has insecure, world-readable permissions! "
                        "It is highly recommended to restrict access: run 'chmod 600' on this file."
                    )
                    os.chmod(config_path, 0o600)
                    logger.info(f"🔒 Automatically enforced 0600 permissions on {config_path}")
            except OSError as e:
                if not exit_on_fail:
                    return None
                logger.error(f"❌ Failed to enforce secure permissions on config file: {e}")
                from bambu_cli.constants import EXIT_CONFIG_ERROR

                abort("", exit_code=EXIT_CONFIG_ERROR)
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
            apply_config(cfg)
            return cfg

    except BambuError:
        raise
    except Exception as e:
        if not exit_on_fail:
            return None
        logger.error(f"Error loading config: {_exception_for_message(e)}")
        abort("", exit_code=EXIT_CONFIG_ERROR)


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _first_existing_path(candidates):  # pragma: no cover -- config helper
    """Return the first existing path, otherwise the first candidate expanded."""
    from bambu_cli.cli import _expand_path

    expanded = [_expand_path(path) for path in candidates if path]
    if not expanded:
        return None
    for path in expanded:
        if os.path.exists(path):
            return path
    return expanded[0]


def _orca_binary_candidates():  # pragma: no cover -- config helper
    """Likely OrcaSlicer binary locations for the current platform, best-first."""
    if sys.platform == "darwin":
        return [
            "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
            "~/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        ]
    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "OrcaSlicer", "OrcaSlicer.exe"),
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.join("~", "AppData", "Local")),
                "Programs",
                "OrcaSlicer",
                "OrcaSlicer.exe",
            ),
        ]
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(os.path.join(program_files_x86, "OrcaSlicer", "OrcaSlicer.exe"))
        return candidates
    # Linux: anything already on PATH (distro package or Flatpak-exported wrapper)
    # first, then common package / Flatpak / AppImage install spots.
    candidates = [shutil.which(name) for name in ("orca-slicer", "OrcaSlicer", "orcaslicer")]
    candidates += [
        "/usr/bin/orca-slicer",
        "/usr/local/bin/orca-slicer",
        "/opt/OrcaSlicer/orca-slicer",
        "/var/lib/flatpak/exports/bin/io.github.softfever.OrcaSlicer",
        "~/.local/share/flatpak/exports/bin/io.github.softfever.OrcaSlicer",
        "~/Applications/OrcaSlicer.AppImage",
        "~/tools/OrcaSlicer.AppImage",
        os.path.join(_SCRIPT_DIR, "..", "tools", "OrcaSlicer.AppImage"),
    ]
    return candidates


def _profiles_dir_candidates():  # pragma: no cover -- config helper
    """Likely OrcaSlicer BBL profile-directory locations, best-first."""
    if sys.platform == "darwin":
        return [
            "/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL",
            "~/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL",
        ]
    if sys.platform == "win32":
        candidates = [
            os.path.join(
                os.environ.get("PROGRAMFILES", r"C:\Program Files"), "OrcaSlicer", "resources", "profiles", "BBL"
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.join("~", "AppData", "Local")),
                "Programs",
                "OrcaSlicer",
                "resources",
                "profiles",
                "BBL",
            ),
        ]
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(os.path.join(program_files_x86, "OrcaSlicer", "resources", "profiles", "BBL"))
        return candidates
    return [
        "/usr/share/OrcaSlicer/resources/profiles/BBL",
        "/opt/OrcaSlicer/resources/profiles/BBL",
        "~/tools/squashfs-root/resources/profiles/BBL",
        os.path.join(_SCRIPT_DIR, "..", "tools", "squashfs-root", "resources", "profiles", "BBL"),
    ]


def _default_orca_path():  # pragma: no cover -- config helper
    """Return the platform-native default OrcaSlicer binary path."""
    return _first_existing_path(_orca_binary_candidates())


def _default_profiles_path():  # pragma: no cover -- config helper
    """Return the platform-native default OrcaSlicer profiles directory."""
    return _first_existing_path(_profiles_dir_candidates())


def detect_orca_slicer():  # pragma: no cover -- orca detect
    """Return the first OrcaSlicer binary that actually exists, or None.

    Unlike :func:`_default_orca_path` this never falls back to a non-existent
    first candidate, so a truthy result is a real, actionable path to suggest.
    """
    from bambu_cli.cli import _expand_path

    for path in _orca_binary_candidates():
        if path and os.path.exists(_expand_path(path)):
            return _expand_path(path)
    return None


def detect_profiles_dir():  # pragma: no cover -- profiles detect
    """Return the first OrcaSlicer BBL profiles directory that exists, or None."""
    from bambu_cli.cli import _expand_path

    for path in _profiles_dir_candidates():
        if path and os.path.isdir(_expand_path(path)):
            return _expand_path(path)
    return None


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
    "X1": {"token": "X1", "full_name": "Bambu Lab X1"},
    "X1E": {"token": "X1E", "full_name": "Bambu Lab X1E"},
    "A1": {"token": "A1", "full_name": "Bambu Lab A1"},
    "A1M": {"token": "A1M", "full_name": "Bambu Lab A1 mini"},
}


def apply_config(cfg):  # pragma: no cover -- config apply
    """Apply a configuration dictionary to the runtime state.

    The dict is parsed once into a typed :class:`bambu_cli.context.Settings`
    (the canonical parse) and installed on a fresh
    :class:`bambu_cli.context.RuntimeContext` as the process-wide source of
    truth. Request-scoped flags (simulation/json_mode) already on the current
    context are preserved.
    """
    from bambu_cli.context import RuntimeContext, Settings, get_current, set_current

    if not cfg:
        return
    settings = Settings.from_config(cfg)
    if settings.insecure_tls:
        logger.warning(
            "🚨 SECURITY WARNING: 'insecure_tls' is enabled! TLS certificate validation is DISABLED for all connections. Your network traffic is vulnerable to MITM attacks."
        )
    prev = get_current()
    set_current(
        RuntimeContext(
            settings=settings,
            config=cfg,
            simulation=prev.simulation,
            json_mode=prev.json_mode,
            config_path=prev.config_path,
        )
    )


_INLINE_ACCESS_CODE_WARNED = False

INLINE_ACCESS_CODE_DEPRECATION_MESSAGE = (
    "config.json contains an inline access_code; move it to an access_code_file "
    "(run: bambu setup --migrate-access-code or edit config). "
    "Inline support will be removed in a future release."
)


def _warn_inline_access_code_once():  # pragma: no cover -- config helper
    """Emit a one-time-per-process stderr warning about inline access_code use.

    Never logs the access code value itself; deduplicated via a module-level flag.
    """
    global _INLINE_ACCESS_CODE_WARNED
    if _INLINE_ACCESS_CODE_WARNED:
        return
    _INLINE_ACCESS_CODE_WARNED = True
    logger.warning(INLINE_ACCESS_CODE_DEPRECATION_MESSAGE)


def _enforce_secret_file_permissions(path, display):  # pragma: no cover -- config helper
    """Best-effort: warn and tighten a secret-bearing file to 0600 on POSIX.

    Mirrors the config.json enforcement in :func:`load_config`. Never raises —
    permission hygiene must not block reading an otherwise-valid secret, and the
    file may not exist yet (callers handle that separately).
    """
    if sys.platform == "win32":
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if not mode & 0o077:
        return
    logger.warning(
        f"⚠️  Access code file '{display}' has insecure, group/world-readable permissions! "
        "Restricting access: run 'chmod 600' on this file."
    )
    try:
        os.chmod(path, 0o600)
        logger.info(f"🔒 Automatically enforced 0600 permissions on {display}")
    except OSError as exc:
        logger.warning(f"Could not tighten permissions on {display}: {exc}")


def load_access_code():  # pragma: no cover -- secret load; unit-tested
    from bambu_cli.cli import _display_path, _exception_for_message, _expand_path
    from bambu_cli.constants import EXIT_CONFIG_ERROR
    from bambu_cli.context import current_config

    cfg = current_config()
    if "access_code" in cfg:
        if not cfg.get("access_code_file"):
            _warn_inline_access_code_once()
        access_code = str(cfg["access_code"]).strip()
        problem = _access_code_value_problem(access_code)
        if problem:
            logger.error(problem)
            abort("", exit_code=EXIT_CONFIG_ERROR)
        return access_code
    if "access_code_file" in cfg:
        path = _expand_path(cfg["access_code_file"])
        _enforce_secret_file_permissions(path, _display_path(path))
        try:
            with open(path, encoding="utf-8") as f:
                access_code = f.read().strip()
        except FileNotFoundError:
            logger.error(f"Access code file not found: {_display_path(path)}")
            abort("", exit_code=EXIT_CONFIG_ERROR)
        except OSError as exc:
            logger.error(f"Access code file could not be read: {_exception_for_message(exc)}")
            abort("", exit_code=EXIT_CONFIG_ERROR)
        problem = _access_code_value_problem(access_code)
        if problem:
            logger.error(problem)
            abort("", exit_code=EXIT_CONFIG_ERROR)
        return access_code
    logger.error("No 'access_code' or 'access_code_file' in config.json")
    abort("", exit_code=EXIT_CONFIG_ERROR)


def _access_code_value_problem(value):  # pragma: no cover -- config helper
    normalized = str(value or "").strip().upper()
    if (
        not normalized
        or normalized in {"ACCESS_CODE", "YOUR_ACCESS_CODE", "USER_PROVIDED_ACCESS_CODE"}
        or normalized.startswith("YOUR_")
    ):
        return "Config must contain a real access_code or access_code_file."
    return None


def load_username():  # pragma: no cover -- config helper
    """Return the MQTT username from config, defaulting to 'bblp'."""
    from bambu_cli.context import current_settings

    return current_settings().username


def _expected_fingerprint():  # pragma: no cover -- fp expected
    """Return the normalized (lowercase, separator-free) pinned SHA-256, or None."""
    from bambu_cli.context import current_config

    fp = current_config().get("cert_fingerprint")
    if not fp:
        return None
    return fp.lower().replace(":", "").replace(" ", "")


def fingerprint_sha256(der_cert):  # pragma: no cover -- fp hash
    """Hex SHA-256 of a DER-encoded certificate, or None if no cert."""
    import hashlib

    if not der_cert:
        return None
    return hashlib.sha256(der_cert).hexdigest()


def _timeout_from(args, key, default):  # pragma: no cover -- config helper
    """Resolve a timeout from CLI args, then config, then the default."""
    from bambu_cli.cli import _namespace_get
    from bambu_cli.context import current_config

    if args:
        val = _namespace_get(args, key)
        if val is not None:
            return float(val)
    cfg = current_config()
    if cfg:
        val = cfg.get(key)
        if val is not None:
            return float(val)
    return default


def get_network_timeout(args=None):  # pragma: no cover -- config helper
    from bambu_cli.constants import DEFAULT_NETWORK_TIMEOUT

    return _timeout_from(args, "network_timeout", DEFAULT_NETWORK_TIMEOUT)


def get_slicer_timeout(args=None):  # pragma: no cover -- config helper
    from bambu_cli.constants import SLICER_TIMEOUT

    return _timeout_from(args, "slicer_timeout", SLICER_TIMEOUT)


def get_command_timeout(args=None):  # pragma: no cover -- config helper
    from bambu_cli.constants import COMMAND_TIMEOUT

    return _timeout_from(args, "command_timeout", COMMAND_TIMEOUT)


def get_upload_timeout(args=None):  # pragma: no cover -- config helper
    from bambu_cli.constants import UPLOAD_TIMEOUT

    return _timeout_from(args, "upload_timeout", UPLOAD_TIMEOUT)
