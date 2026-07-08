"""Setup and preflight commands: guided/non-interactive config creation,
mDNS printer discovery, secure secret storage, and local readiness checks.

This package replaces the former ``bambu_cli/setup_cmd.py`` monolith. Every
name the old module exposed is re-exported here so imports and the
``bambu_cli.bambu`` facade keep working. Tests that patch preflight
dependencies target the submodule (``bambu_cli.setup_cmd.preflight.<name>``).
"""
from bambu_cli.config import load_config  # noqa: F401 -- re-exported for compat
from bambu_cli.setup_cmd.common import (  # noqa: F401
    _build_setup_config,
    _config_path,
    _default_access_code_file_path,
    _looks_like_placeholder,
    _normalize_model,
    _normalize_nozzle,
    _prompt_access_code_file_path,
    _prompt_secret,
    _prompt_text,
    _secure_write_json,
    _secure_write_text,
    _setup_file_error,
    _setup_json_error,
    _setup_path_details,
    _setup_summary,
    _validate_setup_access_code_file,
    _write_setup_config,
)
from bambu_cli.setup_cmd.migrate import (  # noqa: F401
    _cmd_migrate_access_code,
    migrate_access_code,
)
from bambu_cli.setup_cmd.preflight import (  # noqa: F401
    _cmd_preflight,
    _file_permission_check,
    _module_available,
    _preflight_result,
    collect_preflight_checks,
)
from bambu_cli.setup_cmd.wizard import (  # noqa: F401
    _cmd_setup,
    _cmd_setup_noninteractive,
    _parse_mdns_printer_identity,
    _service_info_address,
)
