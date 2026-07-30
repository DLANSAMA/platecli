"""Migrate an inline access_code from config.json into a separate secret file."""

import json
import os

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import EXIT_CONFIG_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.paths import display_path as _display_path
from bambu_cli.paths import exception_for_message as _exception_for_message
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.setup_cmd.common import (
    _config_path,
    _default_access_code_file_path,
    _scrub_config_backup,
    _secure_write_json,
    _secure_write_text,
    _setup_json_error,
)
from bambu_cli.utils import emit_json


def _secure_write_json_no_secret_backup(path, config):
    """Write config.json for a migration WITHOUT leaving a plaintext-secret .bak.

    The pre-migration config.json contains the inline access_code we are moving
    out; ``_secure_write_json``'s default backup=True would copy that plaintext
    secret to config.json.bak and defeat the whole migration. Write with
    backup=False and scrub any stale .bak from an earlier run.
    """
    _secure_write_json(path, config, backup=False)
    _scrub_config_backup(path)


def migrate_access_code(config_path=None, access_code_file_path=None):
    """Move an inline ``access_code`` in config.json into a separate,
    0600-protected ``access_code_file`` and remove the inline value.

    Returns a summary dict with a ``status`` of ``migrated``, ``noop``, or
    ``error``. Never logs the access code value itself.
    """
    from bambu_cli.config import read_config_json

    path = config_path or _config_path()
    expanded_config = _expand_path(path)
    # utf-8-sig via the canonical reader: a Windows-editor BOM must not make
    # migrate fail (and leave the inline secret in place) on the exact config
    # every other command loads fine.
    config = read_config_json(expanded_config)

    inline_code = config.get("access_code")
    existing_file = config.get("access_code_file")

    # Both keys present: the inline code silently wins in load_access_code, so
    # refusing to touch it (the old no-op) left the plaintext secret in config
    # forever and the file rotation ignored. Strip the stale inline key instead
    # of no-op'ing when access_code_file is already configured.
    if existing_file:
        if not inline_code:
            return {
                "status": "noop",
                "reason": "access_code_file is already configured; nothing to migrate.",
                "config_path": _display_path(expanded_config),
                "access_code_file": _display_path(_expand_path(existing_file)),
            }
        del config["access_code"]
        _secure_write_json_no_secret_backup(path, config)
        return {
            "status": "migrated",
            "reason": "Removed stale inline access_code; access_code_file was already configured.",
            "config_path": _display_path(expanded_config),
            "access_code_file": _display_path(_expand_path(existing_file)),
        }

    if not inline_code:
        return {
            "status": "noop",
            "reason": "No inline access_code found in config.",
            "config_path": _display_path(expanded_config),
        }

    target = access_code_file_path or _default_access_code_file_path()
    expanded_target = _expand_path(target)
    inline_secret = str(inline_code).rstrip("\n") + "\n"
    # Whether the target existed BEFORE this call. Gates the orphan cleanup on a
    # config-write failure: we may only unlink a secret file *we just created*,
    # never a pre-existing identical file we resumed onto (that is a prior run's
    # legitimate output and must survive for the retry).
    target_preexisted = os.path.exists(expanded_target)
    if target_preexisted:
        # A pre-existing target is normally a refusal (don't clobber an unrelated
        # secret). But a prior migration attempt that wrote the secret file and
        # then failed on the config write leaves *our own* identical output here;
        # tolerating that makes the two-write migration idempotent/retryable
        # instead of wedging every retry behind "target already exists".
        try:
            with open(expanded_target, encoding="utf-8") as f:
                existing_secret = f.read()
        except OSError:
            existing_secret = None
        if existing_secret != inline_secret:
            return {
                "status": "error",
                "reason": f"Target access-code file already exists: {_display_path(expanded_target)}",
                "config_path": _display_path(expanded_config),
                "access_code_file": _display_path(expanded_target),
            }
        # else: fall through — the file already holds exactly the code we would
        # write, so this is a resumed migration; finish the config write.
    else:
        # If this raises, nothing was written on the config side yet — no orphan.
        _secure_write_text(expanded_target, inline_secret)

    config["access_code_file"] = target
    del config["access_code"]
    try:
        _secure_write_json_no_secret_backup(path, config)
    except OSError:
        # The config write failed but the secret file now exists. Best-effort
        # remove our own orphan so a retry is not blocked by "target already
        # exists" — but only if we CREATED it this call. A pre-existing identical
        # file we resumed onto is a prior run's output; leave it for the retry.
        if not target_preexisted:
            try:
                os.unlink(expanded_target)
            except OSError:
                pass
        raise

    return {
        "status": "migrated",
        "config_path": _display_path(expanded_config),
        "access_code_file": _display_path(expanded_target),
    }


def _cmd_migrate_access_code(args):
    """Non-interactive: move inline access_code into access_code_file.

    Wired up via the (planned) ``plate setup --migrate-access-code`` flag.
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
