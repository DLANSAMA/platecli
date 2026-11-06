"""`upload` must not replace the file the printer is printing right now.

``upload_file`` deletes ``/model/<name>`` before storing the new bytes. When
that is the file of the running print (re-running ``job`` on the same model
while the last one prints), the file is pulled out from under the printer.
What the firmware then does is unverified on hardware, so the upload asks the
printer first and refuses. A status query that fails does not block the upload.
"""

from __future__ import annotations

import argparse

import pytest

from bambu_cli.constants import EXIT_PRINTER_ERROR
from bambu_cli.errors import BambuError


class _Printer:
    def __init__(self, status=None, status_error=None):
        self._status = status
        self._status_error = status_error
        self.uploads = []
        self.last_size_verified = True
        self.simulation_mode = False

    def status(self, timeout=None, retries=2, *, require_complete=True):
        if self._status_error:
            raise self._status_error
        return self._status

    def upload_file(self, local, remote, **kwargs):
        self.uploads.append(remote)
        return True


def _upload(tmp_path, printer, name="model.3mf"):
    from bambu_cli.commands.files import cmd_upload

    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04 sliced")
    args = argparse.Namespace(file=str(path), dry_run=False, json=False, progress=False)
    return cmd_upload(args, ctx=argparse.Namespace(printer=lambda: printer))


@pytest.mark.parametrize(
    "status",
    [
        {"gcode_state": "RUNNING", "subtask_name": "model.3mf"},
        {"gcode_state": "PAUSE", "subtask_name": "model"},
        {"gcode_state": "PREPARE", "gcode_file": "/sdcard/model/model.3mf"},
        {"gcode_state": "RUNNING", "gcode_file": "model.3mf"},
    ],
)
def test_refuses_to_replace_the_file_being_printed(tmp_path, status):
    printer = _Printer(status=status)
    with pytest.raises(BambuError) as caught:
        _upload(tmp_path, printer)
    assert caught.value.exit_code == EXIT_PRINTER_ERROR
    assert "printing" in str(caught.value)
    assert printer.uploads == []


@pytest.mark.parametrize(
    "status",
    [
        {"gcode_state": "RUNNING", "subtask_name": "other.3mf", "gcode_file": "/sdcard/model/other.3mf"},
        {"gcode_state": "IDLE", "subtask_name": "model.3mf"},
        {"gcode_state": "FINISH", "subtask_name": "model.3mf"},
        None,
    ],
)
def test_uploads_when_that_file_is_not_printing(tmp_path, status):
    printer = _Printer(status=status)
    assert _upload(tmp_path, printer) == "model.3mf"
    assert printer.uploads == ["/model/model.3mf"]


def test_status_failure_does_not_block_the_upload(tmp_path):
    printer = _Printer(status_error=OSError("mqtt down"))
    assert _upload(tmp_path, printer) == "model.3mf"
    assert printer.uploads == ["/model/model.3mf"]


class _DeltaFirstPrinter(_Printer):
    """Mid-print the first report is usually a delta with no state or file name."""

    def status(self, timeout=None, retries=2, *, require_complete=True):
        if not require_complete:
            return {"mc_percent": 4, "bed_temper": 60.5}
        return {
            "gcode_state": "RUNNING",
            "subtask_name": "model.3mf",
            "mc_percent": 4,
            "bed_temper": 60.5,
            "nozzle_temper": 220,
        }


def test_a_delta_first_report_does_not_let_the_upload_through(tmp_path):
    printer = _DeltaFirstPrinter()
    with pytest.raises(BambuError) as caught:
        _upload(tmp_path, printer)
    assert caught.value.exit_code == EXIT_PRINTER_ERROR
    assert printer.uploads == []


def test_file_listing_does_not_pass_terminal_escapes(capsys, monkeypatch):
    from bambu_cli import printer as printer_mod
    from bambu_cli.commands.files import cmd_files

    class _ListingPrinter:
        simulation_mode = False

        def list_files(self, *args, **kwargs):
            return ["\x1b]52;c;ZWNobw==\x07evil.3mf", "\x1b[2Jcube.3mf"]

    # cmd_files builds its printer through get_printer(), as tests/test_cmd_files.py patches it.
    monkeypatch.setattr(printer_mod, "get_printer", lambda *a, **k: _ListingPrinter())
    cmd_files(argparse.Namespace(json=False, path=None), ctx=argparse.Namespace())
    captured = capsys.readouterr()
    assert "\x1b" not in captured.out + captured.err
