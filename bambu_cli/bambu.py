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

This module is the CLI entry point and the shared runtime-state namespace.
Command logic lives in the sibling modules (cli, commands, download, job,
setup_cmd, camera, slicer, config, printer, protocols); every public and
private helper is re-exported here so ``from bambu_cli import bambu`` remains
a stable facade for tests and scripts, and so runtime state can be patched in
one place (``bambu.SIMULATION_MODE``, ``bambu.PRINTER_IP``, ...).
"""
import logging
import sys

# Best-effort: make emoji/unicode output work on Windows consoles that default to cp1252.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# --- Runtime state -----------------------------------------------------------
# Mutable, config-derived globals. Modules read these via bambu.<NAME> so a
# loaded config (config.apply_config) or a test patch takes effect everywhere.
SIMULATION_MODE = False
ALLOW_PRIVATE_IPS = False
_LAST_ERROR_PAYLOAD = None  # canonical copies live in bambu_cli.utils

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


def _redacted_serial():
    """Return a non-identifying serial placeholder for reports written to disk."""
    return "UNKNOWN" if not SERIAL or SERIAL == "UNKNOWN" else "<redacted>"


# --- Facade ------------------------------------------------------------------
# Bind every public name from the implementation modules into this namespace.
from bambu_cli.constants import *
from bambu_cli.cli import *
from bambu_cli.config import *
from bambu_cli.slicer import *
from bambu_cli.download import *
from bambu_cli.job import *
from bambu_cli.setup_cmd import *
from bambu_cli.camera import *
from bambu_cli.commands import *
from bambu_cli.protocols.ftps import *
from bambu_cli.protocols.mqtt import *

# Restore the real logger in bambu.py namespace so it is not overridden by the submodule proxies
logger = logging.getLogger("bambu")

# Private helpers also exposed under the bambu namespace for tests and cross-module use
from bambu_cli.utils import (
    _ensure_output_dir,
    _ensure_parent_dir,
    _secure_makedirs,
)
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
    _setup_args_provided,
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
    _normalize_wall_type,
)
from bambu_cli.download import (
    _cmd_download,
    _default_user_agent,
    _download_filename_with_extension,
    _download_source_extension,
    _download_target_filename,
    _extract_zip_model,
    _file_extension,
    _filename_from_content_disposition,
    _get_printables_download_link,
    _get_printables_file_info,
    _is_archive_download,
    _is_html_content_type,
    _is_http_url,
    _is_print_ready_name,
    _is_printables_model_url,
    _is_zip_content_type,
    _known_unsupported_content_type,
    _known_unsupported_download_extension,
    _looks_like_url,
    _max_download_mb_error,
    _ModelLinkParser,
    _name_for_message,
    _normalize_url_input,
    _portable_basename,
    _print_ready_error_message,
    _reject_non_print_ready,
    _reject_oversized_download,
    _reject_unsupported_content_type,
    _reject_unsupported_download_extension,
    _resolve_html_model_link,
    _response_header,
    _response_url,
    _safe_remote_name,
    _sanitize_download_filename,
    _select_printables_file,
    _select_zip_model_member,
    _unsupported_download_message,
    _validate_download_url_or_exit,
    _validate_http_url_or_exit,
    _validate_max_download_mb_or_exit,
)
from bambu_cli.job import (
    _cmd_job,
    _emit_job_failure,
    _job_fail,
    _last_error_for,
    _parse_print_options,
    _predicted_sliced_remote_name,
    _predicted_url_download_extension,
    _predicted_url_remote_name,
    _prepare_job_output_dir,
    _print_next_command,
    _slice_args_for_job,
    _validate_predicted_remote_name_or_fail,
)
from bambu_cli.setup_cmd import (
    _build_setup_config,
    _cmd_preflight,
    _cmd_setup,
    _cmd_setup_noninteractive,
    _default_access_code_file_path,
    _file_permission_check,
    _looks_like_placeholder,
    _module_available,
    _normalize_model,
    _normalize_nozzle,
    _parse_mdns_printer_identity,
    _preflight_result,
    _prompt_access_code_file_path,
    _prompt_secret,
    _prompt_text,
    _secure_write_json,
    _secure_write_text,
    _service_info_address,
    _setup_file_error,
    _setup_json_error,
    _setup_path_details,
    _setup_summary,
    _validate_setup_access_code_file,
    _write_setup_config,
)
from bambu_cli.camera import (
    _cmd_snapshot,
    _grab_camera_frame_direct,
    _write_snapshot_atomic,
)
from bambu_cli.config import (
    _expected_fingerprint,
    _first_existing_path,
    _default_orca_path,
    _default_profiles_path,
    _DEFAULT_ORCA,
    _DEFAULT_PROFILES,
    _access_code_value_problem,
    get_network_timeout,
    get_slicer_timeout,
    get_command_timeout,
    get_upload_timeout,
)
from bambu_cli.protocols.ftps import (
    _verify_cert_fingerprint,
    _noncolliding_path,
    _SIM_FTP_FILES,
    _remove_partial_file,
    _download_partial_path,
)
from bambu_cli.protocols.mqtt import (
    _require_mqtt,
    _resolve_ip,
    _mqtt_connect,
    _get_and_verify_cert_pem,
    _SimMqttClient,
)

# Load config at import-time to populate _cfg for tests that mock config.json at import time
try:
    load_config(exit_on_fail=False)
except Exception:
    pass

class DynamicCmds(dict):
    """Resolve command handlers through this module so tests can patch cmd_*."""

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
