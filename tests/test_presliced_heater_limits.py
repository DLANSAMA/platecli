"""Pre-sliced G-code is held to the heater limits before it reaches the printer.

``plate gcode`` refused ``M104 S400`` while a downloaded 3MF carrying the same
line went straight to the SD card through ``upload``/``job``/``send``: platecli
never generated that G-code, so nothing had checked it.
"""

from __future__ import annotations

import argparse
import io
import zipfile

import pytest

from bambu_cli.commands.files import cmd_upload
from bambu_cli.constants import EXIT_FILE_ERROR
from bambu_cli.errors import BambuError
from bambu_cli.gcode_limits import file_heater_problem

_STAMP = (2026, 1, 1, 0, 0, 0)


class _Printer:
    simulation_mode = False

    def __init__(self):
        self.uploads = []

    def status(self, timeout=None, retries=2, *, require_complete=True):
        return {"gcode_state": "IDLE"}

    def upload_file(self, local, remote, **kwargs):
        self.uploads.append(remote)
        return True


def _three_mf(path, members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("[Content_Types].xml", date_time=_STAMP), "<Types/>")
        for name, text in members.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=_STAMP), text)
    path.write_bytes(buf.getvalue())
    return path


def _upload(path, printer, dry_run=False):
    args = argparse.Namespace(file=str(path), dry_run=dry_run, json=False, progress=False)
    return cmd_upload(args, ctx=argparse.Namespace(printer=lambda: printer))


BAMBU_LIKE = (
    "; HEADER_BLOCK_START\n; nozzle_temperature = 220\n; HEADER_BLOCK_END\n"
    "M140 S55 ; set bed temperature\n"
    "M104 S220 ; Set nozzle\n"
    "M109 S25 H140\n"
    "M190 S55\n"
    "G28\n"
    "M104 T1 S230\n"
    "M106 P1 S255\n"
    "M104 S0\n"
)


@pytest.mark.parametrize(
    "line",
    ["M104 S400", "M109 S2200 ; typo", "M140 S200", "M190 T0 S900", "  m104 s351", "N12 M104 S999", "M104 S2e3"],
)
def test_a_hot_plate_in_a_3mf_is_refused_before_upload(tmp_path, line):
    path = _three_mf(tmp_path / "model.gcode.3mf", {"Metadata/plate_1.gcode": BAMBU_LIKE + line + "\n"})
    printer = _Printer()
    with pytest.raises(BambuError) as excinfo:
        _upload(path, printer)
    assert excinfo.value.exit_code == EXIT_FILE_ERROR
    assert "Metadata/plate_1.gcode line 12" in str(excinfo.value)
    assert printer.uploads == []


def test_every_plate_is_checked_not_only_the_first(tmp_path):
    path = _three_mf(
        tmp_path / "two.3mf",
        {"Metadata/plate_1.gcode": BAMBU_LIKE, "Metadata/plate_2.gcode": "G28\nM140 S300\n"},
    )
    assert "Metadata/plate_2.gcode line 2" in file_heater_problem(path)


def test_a_plain_gcode_file_is_checked(tmp_path):
    path = tmp_path / "hot.gcode"
    path.write_text("G28\nM104 S500\n")
    printer = _Printer()
    with pytest.raises(BambuError) as excinfo:
        _upload(path, printer, dry_run=True)
    assert "hot.gcode line 2" in str(excinfo.value)
    assert printer.uploads == []


def test_ordinary_sliced_output_uploads(tmp_path):
    path = _three_mf(tmp_path / "model.gcode.3mf", {"Metadata/plate_1.gcode": BAMBU_LIKE})
    printer = _Printer()
    assert _upload(path, printer) == "model.gcode.3mf"
    assert printer.uploads == ["/model/model.gcode.3mf"]


def test_a_member_that_cannot_be_read_fails_closed(tmp_path):
    path = _three_mf(tmp_path / "broken.3mf", {"Metadata/plate_1.gcode": "M104 S220\n" * 2000})
    data = bytearray(path.read_bytes())
    start = data.index(b"Metadata/plate_1.gcode") + len(b"Metadata/plate_1.gcode")
    data[start : start + 40] = b"\xff" * 40  # corrupt the deflate stream right after the local header
    path.write_bytes(bytes(data))
    problem = file_heater_problem(path)
    assert problem and "could not read Metadata/plate_1.gcode" in problem


def test_job_dry_run_reports_the_refusal_too(tmp_path, capsys):
    import json

    from bambu_cli.cli import build_parser
    from bambu_cli.context import RuntimeContext
    from bambu_cli.job import JobSteps, _run_job

    path = _three_mf(tmp_path / "model.3mf", {"Metadata/plate_1.gcode": "G28\nM104 S400\n"})
    args = build_parser().parse_args(["job", str(path), "--dry-run", "--json"])
    with pytest.raises(BambuError):
        _run_job(RuntimeContext(), args, JobSteps())
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed_step"] == "validate"
    assert "above the 350 °C limit" in payload["error"]
