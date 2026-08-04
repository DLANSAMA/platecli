"""Filesystem helpers shared across layers.

These four functions are pure path/file mechanics — no transport, no config, no
knowledge of what is being written. They previously lived in
``protocols/ftps.py`` and ``download/naming.py``, which meant the OrcaSlicer
runner had to import the *Bambu FTPS module* to delete a partial file
(``slicer/output.py``) and the download package had to reach into FTPS
internals for a temp-path helper. Both are boundary violations that
``scripts/check_layers.py`` now rejects.

Nothing here touches the network or the printer. Rank 10 in the layer table:
importable from anywhere, imports nothing but the standard library and
``bambu_cli.paths``.

Names keep their historical leading underscore so the move stays a pure
relocation — this package already shares underscore-prefixed helpers between
modules (``_file_extension`` spans six of them), and renaming here would bury
the structural change in churn.
"""

from __future__ import annotations

import os
import tempfile

__all__ = [
    "_download_partial_path",
    "_noncolliding_path",
    "_portable_basename",
    "_remove_partial_file",
]


def _portable_basename(path):
    """Return a basename while treating both POSIX and Windows separators as separators."""
    return os.path.basename(str(path or "").replace("\\", "/"))


def _remove_partial_file(path):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _download_partial_path(outpath):
    if not os.path.exists(outpath):
        return outpath, False
    directory = os.path.dirname(outpath) or "."
    basename = os.path.basename(outpath) or "download"
    fd, temp_path = tempfile.mkstemp(prefix=f".{basename}.", suffix=".part", dir=directory)
    os.close(fd)
    return temp_path, True


def _noncolliding_path(path):
    from bambu_cli.paths import path_for_message as _path_for_message

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return path
    except FileExistsError:
        pass

    directory = os.path.dirname(path)
    basename = os.path.basename(path)
    stem, ext = os.path.splitext(basename)
    stem = stem or "download"
    for index in range(1, 1000):
        candidate = os.path.join(directory, f"{stem}-{index}{ext}")
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not find an unused filename near {_path_for_message(path)}")
