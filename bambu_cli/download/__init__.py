"""Model download pipeline: SSRF-safe HTTP, URL/filename validation, ZIP
extraction, HTML link scraping, and Printables GraphQL resolution.

Collaborators used by the download command (HTTP opener, Printables resolver,
collision-safe paths) are **injected** into ``_cmd_download`` /
``_extract_zip_model`` — tests pass fakes rather than patching this package
namespace. Re-exports below are for convenient imports, not for mock targets.
"""

from bambu_cli.download.downloader import (  # noqa: F401
    _cmd_download,
    _response_header,
    _response_url,
)
from bambu_cli.download.extract import (  # noqa: F401
    _archive_member_exceeded_limit_message,
    _archive_member_too_large_message,
    _extract_zip_model,
    _is_archive_download,
    _is_zip_content_type,
    _select_zip_model_member,
)
from bambu_cli.download.html_links import (  # noqa: F401
    _is_html_content_type,
    _ModelLinkParser,
    _resolve_html_model_link,
)
from bambu_cli.download.naming import (  # noqa: F401
    _download_filename_with_extension,
    _download_source_extension,
    _download_target_filename,
    _file_extension,
    _filename_from_content_disposition,
    _has_command_injection_chars,
    _is_print_ready_name,
    _name_for_message,
    _portable_basename,
    _print_ready_error_message,
    _reject_non_print_ready,
    _safe_remote_name,
    _sanitize_download_filename,
)
from bambu_cli.download.validation import (  # noqa: F401
    _is_http_url,
    _known_unsupported_content_type,
    _known_unsupported_download_extension,
    _looks_like_url,
    _max_download_mb_error,
    _normalize_url_input,
    _reject_oversized_download,
    _reject_unsupported_content_type,
    _reject_unsupported_download_extension,
    _unsupported_download_message,
    _validate_download_url_or_exit,
    _validate_http_url_or_exit,
    _validate_max_download_mb_or_exit,
)
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
from bambu_cli.printables import (  # noqa: F401
    _is_printables_model_url,
    resolve_printables_url,
)
from bambu_cli.protocols.ftps import (  # noqa: F401
    _download_partial_path,
    _noncolliding_path,
    _remove_partial_file,
)
