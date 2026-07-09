"""File ops: upload, list, delete on the printer."""

import os
import sys

from bambu_cli.cli import (
    _exception_for_message,
    _expand_path,
    _namespace_get,
    _path_for_message,
)
from bambu_cli.config import get_upload_timeout
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.download.naming import (
    _is_print_ready_name,
    _name_for_message,
    _portable_basename,
    _print_ready_error_message,
    _safe_remote_name,
)
from bambu_cli.errors import abort
from bambu_cli.logging_utils import logger
from bambu_cli.slicer import _directory_input_message, _is_directory_input
from bambu_cli.utils import emit_json, emit_json_error


def cmd_upload(args, ctx=None):
    """Upload a file to the printer via FTPS with binary retry/resume."""
    from bambu_cli.printer import get_printer

    ctx = ctx or RuntimeContext.for_request(args)
    filepath = _expand_path(args.file)
    if filepath.startswith("-"):
        message = f"Invalid filepath: {_path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not os.path.exists(filepath):
        message = f"File not found: {_path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if _is_directory_input(filepath):
        message = _directory_input_message(filepath)
        logger.error(message)
        emit_json_error(args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)

    filename = _portable_basename(filepath)
    if _safe_remote_name(filename) is None:
        message = f"Refusing to upload file with unsafe name: {_name_for_message(filename)!r}"
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if not _is_print_ready_name(filename):
        message = _print_ready_error_message(filename, "upload")
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    try:
        filesize = os.path.getsize(filepath)
    except OSError as exc:
        message = f"Could not read file size for {_path_for_message(filepath)}: {_exception_for_message(exc)}"
        logger.error(message)
        emit_json_error(
            args, "upload", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath, remote_name=filename
        )
        abort("", exit_code=EXIT_FILE_ERROR)
    if filesize <= 0:
        message = f"Refusing to upload empty file: {_path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(
            args,
            "upload",
            EXIT_FILE_ERROR,
            message,
            failed_step="validate",
            file=filepath,
            remote_name=filename,
            bytes=filesize,
        )
        abort("", exit_code=EXIT_FILE_ERROR)

    if getattr(args, "dry_run", False):
        logger.info(f"🔍 Dry Run: Validating printer connectivity for {filename}...")
        printer = get_printer()
        try:
            # Uploads go over FTPS, so the dry-run must exercise FTPS, not MQTT.
            with printer.get_ftp_client(timeout=5):
                pass
            logger.info("   ✅ Printer reachable.")
        except Exception:
            message = "Dry run failed: Could not reach printer."
            logger.error(message)
            emit_json_error(
                args, "upload", EXIT_NETWORK_ERROR, message, failed_step="dry_run", file=filepath, remote_name=filename
            )
            abort("", exit_code=EXIT_NETWORK_ERROR)

        logger.info(f"   ✅ Local file {_path_for_message(filepath)} exists ({filesize // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "dry_run_ok",
                    "command": "upload",
                    "file": filepath,
                    "remote_name": filename,
                    "bytes": filesize,
                    "uploaded": False,
                }
            )
        return filename

    logger.info(f"📤 Uploading {filename} ({filesize // 1024}KB)...")

    printer = get_printer()

    progress = None
    task_id = None
    upload_callback = None
    try:
        if not getattr(args, "json", False) and getattr(args, "progress", True) and sys.stdout.isatty():
            from rich.progress import DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

            progress = Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                transient=True,
            )
            progress.start()
            task_id = progress.add_task(f"Uploading {filename}...", total=filesize)

            def _cb(block):
                progress.update(task_id, advance=len(block))

            upload_callback = _cb
    except ImportError:
        pass

    try:
        on_resume = None
        if progress is not None and task_id is not None:
            on_resume = lambda n: progress.update(task_id, completed=n)
        success = printer.upload_file(
            filepath,
            f"/model/{filename}",
            timeout=get_upload_timeout(args),
            progress_callback=upload_callback,
            on_resume=on_resume,
        )
    finally:
        if progress:
            progress.stop()

    if success:
        logger.info(f"✅ Uploaded {filename} to printer")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "uploaded",
                    "command": "upload",
                    "file": filepath,
                    "remote_name": filename,
                    "bytes": filesize,
                    "uploaded": True,
                }
            )
        return filename
    else:
        # 4 attempts mirrors upload_file.max_retries (3 retries + initial try)
        message = "Upload failed after 4 attempts."
        logger.error(f"❌ {message}")
        emit_json_error(
            args,
            "upload",
            EXIT_NETWORK_ERROR,
            message,
            failed_step="upload",
            file=filepath,
            remote_name=filename,
        )
        abort("", exit_code=EXIT_NETWORK_ERROR)


def cmd_files(args, ctx=None):
    """List files on the printer."""
    from bambu_cli.printer import get_printer

    ctx = ctx or RuntimeContext.for_request(args)
    json_mode = bool(_namespace_get(args, "json", False))
    try:
        printer = get_printer()
        files = printer.list_files("/model/")
        if files is None:
            raise Exception("Failed to list files via printer API")
        remote_files = [{"name": _portable_basename(path), "path": path} for path in files]
        if json_mode:
            emit_json(
                {
                    "status": "ok",
                    "command": "files",
                    "count": len(remote_files),
                    "files": remote_files,
                }
            )
            return
        if not files:
            logger.info("No files on printer.")
            return
        logger.info("📁 Files on printer:")
        for f in files:
            logger.info(f"   {f}")
    except Exception as e:
        message = f"Error listing files: {e}"
        logger.error(message)
        emit_json_error(args, "files", EXIT_NETWORK_ERROR, message, failed_step="ftps", files=[])
        abort("", exit_code=EXIT_NETWORK_ERROR)


def cmd_delete(args, ctx=None):
    """Delete a file from the printer via FTPS."""
    from bambu_cli.printer import get_printer

    ctx = ctx or RuntimeContext.for_request(args)
    filename = str(args.file or "")
    if _safe_remote_name(filename) is None:
        message = f"Refusing to delete file with unsafe name: {_name_for_message(filename)!r}"
        logger.error(message)
        emit_json_error(args, "delete", EXIT_FILE_ERROR, message, failed_step="validate", file=filename, deleted=False)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not args.confirm:
        logger.warning(f"⚠️  This will DELETE '{filename}' from the printer. Add --confirm to proceed.")
        if bool(_namespace_get(args, "json", False)):
            emit_json(
                {
                    "status": "confirmation_required",
                    "command": "delete",
                    "file": filename,
                    "deleted": False,
                    "next_command": ["delete", filename, "--confirm", "--json"],
                }
            )
        abort("", exit_code=EXIT_COMMAND_ERROR)

    try:
        printer = get_printer()
        if printer.delete_file(f"/model/{filename}"):
            logger.info(f"🗑️  Deleted {filename} from printer")
            if bool(_namespace_get(args, "json", False)):
                emit_json(
                    {
                        "status": "deleted",
                        "command": "delete",
                        "file": filename,
                        "deleted": True,
                    }
                )
        else:
            raise Exception("Delete operation failed in printer client.")
    except Exception as e:
        message = f"Delete failed: {e}"
        logger.error(message)
        emit_json_error(args, "delete", EXIT_NETWORK_ERROR, message, failed_step="ftps", file=filename, deleted=False)
        abort("", exit_code=EXIT_NETWORK_ERROR)
