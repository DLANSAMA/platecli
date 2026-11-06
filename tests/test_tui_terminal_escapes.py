"""Printer-supplied strings must not reach the TUI with terminal control characters."""

from __future__ import annotations

from bambu_cli.tui.services import StatusSnapshot, ams_tray_rows, job_progress_lines, status_lines

OSC52 = "\x1b]52;c;ZWNobw==\x07x.gcode"


def _snap() -> StatusSnapshot:
    raw = {"gcode_state": "RUN\x1b[2J", "gcode_file": OSC52, "mc_percent": 5}
    ams = {"units": [{"id": 0, "trays": [{"slot": 0, "type": "PLA\x1b]52;c;AA==\x07", "remain": 50}]}]}
    return StatusSnapshot(ok=True, raw=raw, ams=ams)


def _flat(rows) -> str:
    return "".join(str(v) for row in rows for v in (row.values() if isinstance(row, dict) else row))


def test_status_rows_strip_escapes():
    assert "\x1b" not in _flat(status_lines(_snap()))


def test_job_rows_strip_escapes():
    assert "\x1b" not in _flat(job_progress_lines(_snap()))


def test_ams_rows_strip_escapes():
    assert "\x1b" not in _flat(ams_tray_rows(_snap()))


def test_error_text_strips_escapes():
    snap = StatusSnapshot(ok=False, error="boom\x1b]52;c;AA==\x07")
    assert "\x1b" not in _flat(status_lines(snap))
    assert "\x1b" not in _flat(job_progress_lines(snap))
