"""Migrate an inline access_code from config.json into a separate secret file."""

import json
import os

from bambu_cli.cli import _display_path, _exception_for_message, _expand_path, _namespace_get
from bambu_cli.constants import EXIT_CONFIG_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.setup_cmd.common import (
    _config_path,
    _default_access_code_file_path,
    _secure_write_json,
    _secure_write_text,
    _setup_json_error,
)
from bambu_cli.utils import emit_json


def migrate_access_code(config_path=None, access_code_file_path=None):  # pragma: no cover -- access code migrate
    """Move an inline ``access_code`` in config.json into a separate,
    0600-protected ``access_code_file`` and remove the inline value.

    Returns a summary dict with a ``status`` of ``migrated``, ``noop``, or
    ``error``. Never logs the access code value itself.
    """
    path = config_path or _config_path()
    expanded_config = _expand_path(path)
    with open(expanded_config, encoding="utf-8") as f:
        config = json.load(f)

    if config.get("access_code_file"):
        return {
            "status": "noop",
            "reason": "access_code_file is already configured; nothing to migrate.",
            "config_path": _display_path(expanded_config),
        }

    access_code = config.get("access_code")
    if not access_code:
        return {
            "status": "noop",
            "reason": "No inline access_code found in config.",
            "config_path": _display_path(expanded_config),
        }

    target = access_code_file_path or _default_access_code_file_path()
    expanded_target = _expand_path(target)
    if os.path.exists(expanded_target):
        return {
            "status": "error",
            "reason": f"Target access-code file already exists: {_display_path(expanded_target)}",
            "config_path": _display_path(expanded_config),
            "access_code_file": _display_path(expanded_target),
        }

    _secure_write_text(expanded_target, str(access_code).rstrip("\n") + "\n")
    config["access_code_file"] = target
    del config["access_code"]
    _secure_write_json(path, config)

    return {
        "status": "migrated",
        "config_path": _display_path(expanded_config),
        "access_code_file": _display_path(expanded_target),
    }


def _cmd_migrate_access_code(args):  # pragma: no cover -- migrate cmd
    """Non-interactive: move inline access_code into access_code_file.

    Wired up via the (planned) ``bambu setup --migrate-access-code`` flag.
    """
    try:
        result = migrate_access_code(
            config_path=_config_path(),
            access_code_file_path=_namespace_get(args, "access_code_file"),
        )
    except FileNotFoundError:
        message = f"Config not found: {_display_path(_config_path())}"
        logger.error(message)
        _setup_json_error(args, message)
        abort("", exit_code=EXIT_CONFIG_ERROR)
    except (OSError, json.JSONDecodeError) as exc:
        message = f"Could not migrate access code: {_exception_for_message(exc)}"
        logger.error(message)
        _setup_json_error(args, message)
        abort("", exit_code=EXIT_CONFIG_ERROR)

    status = result["status"]
    if status == "migrated":
        logger.info(f"✅ Moved access_code to {result['access_code_file']}; config.json updated.")
    elif status == "noop":
        logger.info(result["reason"])
    else:
        logger.error(result["reason"])

    payload = {
        "command": "migrate-access-code",
        "status": status,
        **{k: v for k, v in result.items() if k != "status"},
    }
    if _namespace_get(args, "json", False):
        emit_json(payload)
    if status == "error":
        abort("", exit_code=EXIT_CONFIG_ERROR)
