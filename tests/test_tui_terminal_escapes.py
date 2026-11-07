"""Printer-supplied strings must not reach the TUI with terminal control characters."""

from __future__ import annotations

from datetime import datetime

from bambu_cli.tui.services import StatusSnapshot, ams_view, feed_text, job_view, state_badge, wifi_text

OSC52 = "\x1b]52;c;ZWNobw==\x07x.gcode"
NOW = datetime(2026, 11, 6, 12, 0)


def _snap() -> StatusSnapshot:
    raw = {
        "gcode_state": "RUN\x1b[2J",
        "gcode_file": OSC52,
        "subtask_name": "job\x1b]0;title\x07",
        "mc_percent": 5,
        "wifi_signal": "-40dBm\x1b[31m",
    }
    tray = {"slot": 0, "type": "PLA\x1b]52;c;AA==\x07", "remain": 50, "active": True}
    ams = {"units": [{"id": 0, "trays": [tray]}], "external_tray": {"slot": 254, "type": "\x1b[5mPETG"}}
    return StatusSnapshot(ok=True, raw=raw, ams=ams)


def test_job_view_strips_escapes():
    view = job_view(_snap(), NOW)
    assert "\x1b" not in view.name + "".join(view.lines) + view.badge.label


def test_unknown_state_badge_strips_escapes():
    assert "\x1b" not in state_badge(_snap()).label


def test_ams_view_strips_escapes():
    view = ams_view(_snap())
    assert view is not None
    assert all("\x1b" not in tray.material and "\x1b" not in tray.label for tray in view.trays)
    assert "\x1b" not in (feed_text(_snap()) or "")


def test_wifi_text_strips_escapes():
    assert "\x1b" not in (wifi_text(_snap()) or "")


def test_error_text_strips_escapes():
    snap = StatusSnapshot(ok=False, error="boom\x1b]52;c;AA==\x07")
    assert "\x1b" not in "".join(job_view(snap, NOW).lines)
