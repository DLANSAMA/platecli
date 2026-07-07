"""Model download pipeline: SSRF-safe HTTP, URL/filename validation, ZIP
extraction, HTML link scraping, and Printables GraphQL resolution."""
import email.message
import email.utils
import os
import re
import socket  # noqa: F401 -- re-exported for test compat (download.socket patching)
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from bambu_cli.cli import (
    _exception_for_message,
    _exit_code_from_system_exit,
    _expand_path,
    _looks_like_schemeless_credential_url,
    _namespace_get,
    _path_for_message,
    _redact_url_credentials,
)
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    DOWNLOAD_CANDIDATE_EXTENSIONS,
    DOWNLOAD_LINK_EXTENSION_PRIORITY,
    DOWNLOAD_TIMEOUT,
    DOWNLOADABLE_EXTENSIONS,
    EXIT_COMMAND_ERROR,
    EXIT_FILE_ERROR,
    EXIT_NETWORK_ERROR,
    HTML_LINK_SCAN_LIMIT,
    KNOWN_UNSUPPORTED_CONTENT_TYPES,
    KNOWN_UNSUPPORTED_DOWNLOAD_EXTENSIONS,
    MAX_DOWNLOAD_FILENAME_LENGTH,
    PRINT_READY_EXTENSIONS,
    WINDOWS_RESERVED_FILENAMES,
)
from bambu_cli.logging_utils import logger

# MAX_DOWNLOAD_REDIRECT_HOPS, _dns_cache, _get_safe_connection, and the Safe*
# handler classes below are not used directly in this module; they are
# re-exported so existing tests that patch/inspect them via
# ``bambu_cli.download.<name>`` keep working after the SSRF-safety layer
# moved to netsafety.py.
from bambu_cli.netsafety import (  # noqa: F401
    MAX_DOWNLOAD_REDIRECT_HOPS,
    SafeHTTPHandler,
    SafeHTTPRedirectHandler,
    SafeHTTPSHandler,
    _default_user_agent,
    _dns_cache,
    _get_safe_connection,
    build_safe_opener,
)
from bambu_cli.printables import (
    _is_printables_model_url,
    resolve_printables_url,
)
from bambu_cli.protocols.ftps import (
    _download_partial_path,
    _noncolliding_path,
    _remove_partial_file,
)
from bambu_cli.utils import (
    _ensure_output_dir,
    _record_download_success,
    emit_json_error,
)


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


def _cmd_download(args):
    """Download a model or printer-ready file from a URL. Auto-resolves Printables page URLs."""
    from bambu_cli import utils
    utils._LAST_DOWNLOAD_PAYLOAD = None
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
                archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
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
                    archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
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
                        archive_temp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed immediately; only the name is used
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
                        from rich.progress import (
                            BarColumn,
                            DownloadColumn,
                            Progress,
                            TextColumn,
                            TimeRemainingColumn,
                            TransferSpeedColumn,
                        )
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
                        task_id = progress.add_task("Downloading", total=total_size)
                except ImportError:
                    pass

                try:
                    with open(partial_path, 'wb') as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
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
