"""Direct model-file link extraction from generic HTML pages."""

from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from bambu_cli.constants import (
    DOWNLOAD_CANDIDATE_EXTENSIONS,
    DOWNLOAD_LINK_EXTENSION_PRIORITY,
    HTML_LINK_SCAN_LIMIT,
)
from bambu_cli.download.naming import _file_extension
from bambu_cli.fsutil import _portable_basename


def _is_html_content_type(content_type):
    return (content_type or "").split(";", 1)[0].strip().lower() in ("text/html", "application/xhtml+xml")


class _ModelLinkParser(HTMLParser):
    """Extract direct model/print links from simple HTML pages."""

    LINK_ATTRS = (
        "href",
        "src",
        "data-url",
        "data-href",
        "data-download-url",
        "data-file-url",
        "data-src",
    )
    FILENAME_HINT_ATTRS = (
        "download",
        "filename",
        "data-filename",
        "data-file-name",
        "data-name",
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
