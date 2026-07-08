"""The `config` command: show the effective config or validate it locally."""

import json
import os

from bambu_cli.cli import _display_path, _exception_for_message, _expand_path, _namespace_get
from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_SUCCESS
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.setup_cmd.common import _config_path
from bambu_cli.setup_cmd.preflight import collect_preflight_checks
from bambu_cli.utils import emit_json, emit_json_error

# The subset of preflight checks that judge config.json itself (not the local
# install): `config validate` reports exactly these.
CONFIG_CHECK_NAMES = {
    "config",
    "config-permissions",
    "printer-ip",
    "serial",
    "access-code",
    "access-code-permissions",
    "orca-slicer",
    "profiles-dir",
}

_REDACTED_CONFIG_KEYS = ("access_code",)


def _redacted_config(config):  # pragma: no cover -- config cmd
    """Return a copy of the config dict safe to print (no secret values)."""
    redacted = dict(config)
    for key in _REDACTED_CONFIG_KEYS:
        if redacted.get(key):
            redacted[key] = "<redacted>"
    return redacted


def _cmd_config_show(args):  # pragma: no cover -- config cmd
    config_path = _expand_path(_config_path())
    if not os.path.exists(config_path):
        message = f"Config not found at {_display_path(config_path)}. Run `setup` first."
        logger.error(message)
        emit_json_error(
            args,
            "config",
            EXIT_CONFIG_ERROR,
            message,
            failed_step="config",
            action="show",
            config_path=_display_path(config_path),
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        message = f"Could not read config: {_exception_for_message(exc)}"
        logger.error(message)
        emit_json_error(
            args,
            "config",
            EXIT_CONFIG_ERROR,
            message,
            failed_step="config",
            action="show",
            config_path=_display_path(config_path),
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)

    redacted = _redacted_config(config)
    if _namespace_get(args, "json", False):
        emit_json(
            {
                "status": "ok",
                "command": "config",
                "action": "show",
                "config_path": _display_path(config_path),
                "config": redacted,
            }
        )
        return
    logger.info(f"📄 Config: {_display_path(config_path)}")
    print(json.dumps(redacted, indent=2))


def _cmd_config_validate(args):  # pragma: no cover -- config cmd
    checks = [check for check in collect_preflight_checks() if check["name"] in CONFIG_CHECK_NAMES]
    error_count = sum(1 for check in checks if check["status"] == "error")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    strict_failed = bool(_namespace_get(args, "strict", False) and warning_count)
    ok = error_count == 0 and not strict_failed
    exit_code = EXIT_SUCCESS if ok else EXIT_CONFIG_ERROR
    if ok:
        status = "ok"
    elif error_count:
        status = "error"
    else:
        status = "warning"

    if _namespace_get(args, "json", False):
        emit_json(
            {
                "status": status,
                "command": "config",
                "action": "validate",
                "exit_code": exit_code,
                "ok": ok,
                "errors": error_count,
                "warnings": warning_count,
                "strict": bool(_namespace_get(args, "strict", False)),
                "config_path": _display_path(_config_path()),
                "checks": checks,
            }
        )
    else:
        logger.info(f"🧪 Validating {_display_path(_config_path())}")
        for check in checks:
            icon = {"ok": "✅", "warning": "⚠️ ", "error": "❌"}[check["status"]]
            logger.info(f"   {icon} {check['name']}: {check['message']}")
        if ok:
            logger.info("✅ Config is valid.")
        elif strict_failed and error_count == 0:
            logger.error(f"Config validation failed in strict mode: {warning_count} warning(s).")
        else:
            logger.error(f"Config validation failed: {error_count} error(s), {warning_count} warning(s).")

    if not ok:
        abort("", exit_code=exit_code)


def _cmd_config(args):  # pragma: no cover -- config cmd
    """Dispatch `config show` / `config validate`."""
    action = _namespace_get(args, "action")
    if action == "show":
        _cmd_config_show(args)
    else:
        _cmd_config_validate(args)
