"""Printer-side names refuse every control and bidi character, and repair agrees.

``_safe_remote_name`` used to reject only CR/LF/NUL, so ESC, C1 controls and
bidi overrides could reach the SD card and every later terminal line that
shows the name. ``_sanitize_download_filename`` must keep producing names the
check accepts, or a file that downloads could not be uploaded.
"""

import pytest

from bambu_cli.download.naming import _safe_remote_name, _sanitize_download_filename

UNSAFE = [
    "ok\x1b[2J.3mf",
    "we ird\x1b[31m.gcode",
    "csi\x9b31m.stl",
    "abc‮gcode.3mf",
    "iso⁦late.stl",
    "tab\there.stl",
]


@pytest.mark.parametrize("name", UNSAFE)
def test_control_and_bidi_names_are_refused(name):
    assert _safe_remote_name(name) is None


@pytest.mark.parametrize("name", UNSAFE)
def test_repair_output_is_always_accepted(name):
    repaired = _sanitize_download_filename(name)
    assert _safe_remote_name(repaired) == repaired


@pytest.mark.parametrize("name", ["Benchy [remix].stl", "модель.stl", "cube v2.3mf"])
def test_ordinary_names_are_unchanged(name):
    assert _safe_remote_name(name) == name
    assert _sanitize_download_filename(name) == name
