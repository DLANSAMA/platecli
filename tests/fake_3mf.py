"""A small, valid 3MF for tests that only need a file upload accepts.

``upload`` opens every 3MF to check its heater targets and refuses one it
cannot open, so placeholder bytes such as ``b"PK\\x03\\x04 sliced"`` no longer
stand in for a sliced file.
"""

from __future__ import annotations

import io
import zipfile

_STAMP = (2026, 1, 1, 0, 0, 0)


def minimal_3mf(pad: int = 0) -> bytes:
    """A valid 3MF with no G-code; ``pad`` adds that many stored bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("[Content_Types].xml", date_time=_STAMP), "<Types/>")
        if pad:
            zf.writestr(zipfile.ZipInfo("Metadata/pad.bin", date_time=_STAMP), b"x" * pad)
    return buf.getvalue()
