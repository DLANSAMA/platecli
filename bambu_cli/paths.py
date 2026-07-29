"""Filesystem/path helpers shared by the CLI and domain modules.

These were extracted from ``bambu_cli.cli`` so domain code no longer has to
reach back into the CLI entrypoint for path expansion and display compaction
(roadmap B.4). They contain no argument parsing and never terminate the
process; domain callers that need to fail do so via ``BambuError`` / ``abort``.
"""

import os

__all__ = [
    "expand_path",
    "display_path",
    "path_for_message",
    "exception_for_message",
]


def expand_path(path):
    """Expand user and environment variables in local filesystem paths."""
    if path is None:
        return None
    return os.path.expandvars(os.path.expanduser(str(path)))


_HOME_DIR = os.path.expanduser("~")
_NORM_HOME_DIR = None


def _get_norm_home_dir():
    global _NORM_HOME_DIR
    if _NORM_HOME_DIR is None:
        try:
            _NORM_HOME_DIR = os.path.normcase(os.path.abspath(_HOME_DIR))
        except (TypeError, ValueError, OSError):
            _NORM_HOME_DIR = _HOME_DIR
    return _NORM_HOME_DIR


def display_path(path):
    """Return a user-facing path with the current home directory compacted."""
    if path is None:
        return None
    text = str(path)

    # Inline expand_path for speed and caching
    expanded = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(expanded):
        return text

    norm_home = _get_norm_home_dir()
    try:
        norm_expanded = os.path.normcase(os.path.abspath(expanded))
    except (TypeError, ValueError, OSError):
        return text

    if norm_expanded == norm_home:
        return "~"
    prefix = norm_home + os.sep
    if norm_expanded.startswith(prefix):
        return "~" + os.sep + os.path.relpath(expanded, _HOME_DIR)
    return text


def path_for_message(path):
    """Return a local path suitable for human and agent-facing messages."""
    display = display_path(path)
    if display is None or os.sep == "/":
        return display
    return display.replace(os.sep, "/")


def exception_for_message(exc):
    """Return exception text with local filesystem paths compacted for output."""
    message = str(exc)
    for attr in ("filename", "filename2"):
        path = getattr(exc, attr, None)
        if path is not None:
            message = message.replace(str(path), display_path(path))
    return message
