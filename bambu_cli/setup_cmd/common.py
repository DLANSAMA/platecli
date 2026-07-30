"""Shared setup helpers: config path, secure writes, prompts, config building."""

import errno
import getpass
import json
import os
import re
import shutil
import sys
import tempfile

from bambu_cli.config import CONFIG_PATH, MODEL_MAPPING
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.paths import display_path as _display_path
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.utils import _secure_makedirs, emit_json_error


def _config_path():
    """Return the active config path (patch ``CONFIG_PATH`` or this helper in tests)."""
    return CONFIG_PATH


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


def _atomic_secure_write(path, text, *, backup=False):
    """Write ``text`` to ``path`` atomically at mode 0600.

    A crash mid-write must never leave a truncated file: write a sibling temp file,
    fsync it, optionally back up the previous file, then os.replace (atomic on POSIX
    and for same-volume renames on Windows).

    ``tempfile.mkstemp`` creates the temp file with O_EXCL and honours the process umask,
    so on a standard POSIX system it opens as 0600 from the outset — there is no window
    where the file is world-readable. The explicit ``os.chmod(tmp_path, 0o600)`` below is
    belt-and-braces for exotic umask/ACL setups. On Windows POSIX modes are not enforced,
    which is why every chmod is best-effort rather than load-bearing.

    ``backup=True`` copies the existing file to ``<path>.bak`` before replacing it.
    Use only for config.json; do **not** pass ``backup=True`` for secret files (access
    code) because a second copy of the credential on disk is a worse tradeoff than the
    recovery convenience.

    One environment defeats the rename: when the target directory is behind a
    filesystem-virtualization boundary (Windows MSIX/AppContainer redirection of
    ``%APPDATA%``, some sandboxes), ``os.replace`` reports ``EXDEV`` /
    ``ERROR_NOT_SAME_DEVICE`` even though both paths are in the *same* directory. There
    the atomic write cannot succeed at all, so rather than fail every config and
    access-code write we fall back to writing the target in place. That fallback is
    **not** crash-safe — see :func:`_write_in_place` — so it is taken only for that
    specific errno and it warns.
    """
    expanded = _expand_path(path)
    directory = os.path.dirname(expanded)
    if directory:
        _secure_makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(expanded) + ".", suffix=".tmp", dir=directory or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        if backup and os.path.exists(expanded):
            bak = expanded + ".bak"
            try:
                shutil.copy2(expanded, bak)
                os.chmod(bak, 0o600)
            except OSError:
                pass
        try:
            os.replace(tmp_path, expanded)
        except OSError as exc:
            if not _is_cross_device_error(exc):
                raise
            logger.warning(
                f"⚠️  Atomic replace is unavailable for {_display_path(expanded)} "
                "(the directory is redirected across a filesystem boundary); writing in "
                "place instead. An interrupted write could leave this file incomplete."
            )
            _write_in_place(expanded, text)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        os.chmod(expanded, 0o600)
    except OSError:
        pass


def _is_cross_device_error(exc):
    """True when an OSError is a cross-device rename refusal.

    Windows raises ``ERROR_NOT_SAME_DEVICE`` (winerror 17) for a redirected directory;
    POSIX uses ``EXDEV``. Python maps winerror 17 to ``errno.EXDEV`` inconsistently
    across versions, so check both.
    """
    if getattr(exc, "errno", None) == errno.EXDEV:
        return True
    return getattr(exc, "winerror", None) == 17


def _write_in_place(expanded, text):
    """Write ``text`` straight to ``expanded`` at mode 0600, without a rename.

    Deliberately not crash-safe: it truncates the target before writing, so an
    interrupted write loses the previous contents. Used only where the atomic path is
    impossible. Writing directly (rather than copying the temp file over) avoids
    leaving a second copy of a secret on disk.
    """
    fd = os.open(expanded, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def _secure_write_json(path, data, *, backup=True):
    # Serialize before touching the filesystem: an unserializable payload must leave the
    # existing file intact rather than truncating it half-way through json.dump.
    # backup=True keeps config.json.bak so a crash mid-write doesn't lose printer_ip/serial.
    # Pass backup=False when the *previous* config held a secret (e.g. migrating an inline
    # access_code out): copying it to config.json.bak would defeat the migration by leaving
    # a plaintext copy of the credential on disk. Callers doing that should also scrub any
    # pre-existing .bak via :func:`_scrub_config_backup`.
    _atomic_secure_write(path, json.dumps(data, indent=2), backup=backup)


def _scrub_config_backup(path):
    """Best-effort remove a ``<config>.bak`` left by an earlier backup=True write.

    A prior ``_secure_write_json`` (or a still-present pre-migration backup) may
    hold a plaintext copy of a secret we are moving out of config.json. Deleting
    the stale ``.bak`` is the least-surprising outcome: config.json.bak exists
    only as crash-recovery scaffolding, and keeping a plaintext credential in it
    would defeat the migration. Never raises — a missing or unremovable .bak must
    not fail the migration that already succeeded.
    """
    bak = _expand_path(path) + ".bak"
    try:
        os.unlink(bak)
    except OSError:
        pass


def _secure_write_text(path, text):
    # backup=False: a second copy of the printer access code on disk is a worse tradeoff
    # than the recovery convenience (lost codes can be re-read from the printer).
    _atomic_secure_write(path, text, backup=False)


def _default_access_code_file_path():
    """Store guided-setup secrets next to config.json instead of inside it."""
    config_dir = os.path.dirname(_expand_path(_config_path()))
    if config_dir:
        return os.path.join(config_dir, "access_code")
    return os.path.abspath("bambu_access_code")


def _prompt_text(prompt, args=None):  # pragma: no cover -- interactive prompt
    if args and getattr(args, "json", False):
        emit_json_error(
            args,
            "setup",
            EXIT_CONFIG_ERROR,
            "Interactive prompt required, but json mode is active",
            failed_step="validate",
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        print(prompt, end="", file=sys.stderr, flush=True)
        return input().strip()
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR)


def _prompt_secret(prompt, args=None):  # pragma: no cover -- interactive secret
    if args and getattr(args, "json", False):
        emit_json_error(
            args,
            "setup",
            EXIT_CONFIG_ERROR,
            "Interactive prompt required, but json mode is active",
            failed_step="validate",
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    try:
        return getpass.getpass(prompt)
    except EOFError:
        print("\nInput cancelled.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR)


def _prompt_access_code_file_path(args=None):  # pragma: no cover -- interactive path
    """Return a secret-file path for guided setup, or None if the user opts out."""
    default_path = _default_access_code_file_path()
    choice = _prompt_text(f"Store access code outside config.json at {default_path}? [Y/n]: ", args).lower()
    if choice in ("", "y", "yes"):
        return default_path
    if choice in ("n", "no"):
        return None
    logger.warning("⚠️  Unrecognized choice; storing access code in a separate access_code file.")
    return default_path


def _build_setup_config(
    ip,
    serial,
    model,
    nozzle,
    access_code=None,
    access_code_file=None,
    orca_slicer=None,
    profiles_dir=None,
    cert_fingerprint=None,
    insecure_tls=False,
):
    from bambu_cli.config import _DEFAULT_ORCA, _DEFAULT_PROFILES

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


# Keys the setup wizard owns. For these the freshly built config is authoritative,
# including *absence*: declining insecure_tls, or moving an inline access_code into
# an access_code_file, must REMOVE the stale key rather than preserve it. Every
# other key found on disk is kept (see _merge_with_existing).
_WIZARD_OWNED_KEYS = frozenset(
    {
        "printer_ip",
        "serial",
        "username",
        "model",
        "nozzle",
        "orca_slicer",
        "profiles_dir",
        "access_code",
        "access_code_file",
        "cert_fingerprint",
        "insecure_tls",
    }
)


def _merge_with_existing(config, path=None):
    """Carry over config keys the wizard does not manage.

    ``_build_setup_config`` builds a dict from scratch, so writing it verbatim
    silently dropped every hand-added key -- ``camera_port``, the ``*_timeout``
    values, and security opt-ins such as ``camera_direct_only``. Losing a security
    control because the user re-ran setup for an unrelated reason is the worst
    case: it still looks enabled in their config history and is not in effect.

    Only non-owned keys are carried over, so this cannot resurrect a secret or a
    downgrade the wizard just removed.
    """
    path = path or _config_path()
    try:
        # utf-8-sig, matching config.load_config: Windows editors prepend a BOM.
        with open(path, encoding="utf-8-sig") as f:
            existing = json.load(f)
    except FileNotFoundError:
        return dict(config)
    except (OSError, ValueError) as exc:
        logger.warning(
            f"⚠️  Could not read the existing config at {_display_path(str(path))} ({exc}); "
            "settings that setup does not manage could not be preserved."
        )
        return dict(config)
    if not isinstance(existing, dict):
        return dict(config)

    preserved = {key: value for key, value in existing.items() if key not in _WIZARD_OWNED_KEYS}
    merged = dict(config)
    # preserved excludes every owned key, so update() can never clobber a value
    # the wizard just decided.
    merged.update(preserved)
    if preserved:
        logger.info(f"   Kept existing settings that setup does not manage: {', '.join(sorted(preserved))}")
    return merged


def _existing_config_has_inline_secret_being_removed(path, new_config):
    """True when the on-disk config has an inline access_code the new one drops.

    Used to decide whether config.json.bak is safe to write: if the previous
    file held a plaintext ``access_code`` and the fresh config no longer carries
    one inline (it moved to an access_code_file, or was cleared), a .bak would
    leak that secret. Best-effort — an unreadable/absent existing config means
    "no secret to leak", so keep the normal crash-recovery backup.
    """
    if new_config.get("access_code"):
        return False
    try:
        with open(_expand_path(path), encoding="utf-8-sig") as f:
            existing = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(existing, dict) and bool(existing.get("access_code"))


def _write_setup_config(config, access_code_file_secret=None):
    if access_code_file_secret is not None:
        _secure_write_text(config["access_code_file"], access_code_file_secret.rstrip("\n") + "\n")
    # If the config we are about to overwrite carried an inline access_code that
    # the new config no longer has inline (the classic inline->file switch),
    # backing it up to config.json.bak would leave a plaintext copy of the
    # secret on disk — the same leak migrate avoids. Write without a backup and
    # scrub any stale .bak in that case; otherwise keep the crash-recovery .bak.
    config_path = _config_path()
    keep_backup = not _existing_config_has_inline_secret_being_removed(config_path, config)
    _secure_write_json(config_path, _merge_with_existing(config), backup=keep_backup)
    if not keep_backup:
        _scrub_config_backup(config_path)
    if sys.platform == "win32":
        logger.warning(
            "   ⚠️  On Windows, file mode 0600 is ignored. Consider storing the "
            "access code in a separate `access_code_file` protected via NTFS ACLs."
        )
    logger.info(f"\n✅ Config saved to {_display_path(_config_path())}")
    logger.info("Run 'doctor' command to verify setup.")
    return {
        "config_path": _display_path(_config_path()),
        "access_code_file": _display_path(config.get("access_code_file")),
    }


def _setup_summary(config):
    access_code_file = config.get("access_code_file")
    payload = {
        "status": "configured",
        "command": "setup",
        "config_path": _display_path(_config_path()),
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
    if expanded.startswith("-"):
        message = f"Invalid access-code file path: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if os.path.abspath(expanded) == os.path.abspath(_expand_path(_config_path())):
        message = "access_code_file must be separate from config.json."
        logger.error(message)
        _setup_json_error(
            args,
            message,
            **_setup_path_details(access_code_file=expanded, config_path=_config_path()),
        )
        abort("", exit_code=EXIT_CONFIG_ERROR)
    if os.path.isdir(expanded):
        message = f"Access code file path is a directory, not a file: {_display_path(expanded)}"
        logger.error(message)
        _setup_json_error(args, message, **_setup_path_details(access_code_file=expanded))
        abort("", exit_code=EXIT_CONFIG_ERROR)
    return expanded


def _looks_like_placeholder(value, placeholders):
    normalized = str(value or "").strip().upper()
    return not normalized or normalized in placeholders or normalized.startswith("YOUR_")
