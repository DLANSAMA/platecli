"""Raw G-code obeys the same heater ceilings as slice overrides.

Before the 2026-11 audit, ``plate gcode "M104 S2200" --confirm`` went straight
to the printer: the only checks were non-empty and no CR/LF/NUL, while every
slice-time temperature was held to 350 °C nozzle / 150 °C bed.
"""

from __future__ import annotations

import argparse

import pytest

from bambu_cli.commands.gcode import _heater_problem, cmd_gcode
from bambu_cli.constants import EXIT_COMMAND_ERROR
from bambu_cli.errors import BambuError


class _RecordingPrinter:
    def __init__(self):
        self.sent = []

    def send_command(self, payload):
        self.sent.append(payload)
        return True


class _Ctx:
    def __init__(self):
        self.printer_obj = _RecordingPrinter()

    def printer(self):
        return self.printer_obj


@pytest.mark.parametrize(
    "gcode",
    [
        "M104 S2200",
        "m140 s500",
        "M104S9999",
        "M109 R400",
        "M190 S151",
        "M140 S150.5",
        "M0104 S999",
        "M104.0 S999",
        "M104 S200 ; S999",  # comments are scanned too: conservative by design
        "M104 S2e3",  # exponent: strtod-style parsers read 2000
        "M104 S0x3E8",  # hex: strtod-style parsers read 1000
        "M104 S250T0",
        "M104 S-5",
        "M104 S٩٩٩",  # Arabic-Indic digits
    ],
)
def test_out_of_range_or_unreadable_heater_values_are_refused_before_sending(gcode):
    assert _heater_problem(gcode) is not None
    ctx = _Ctx()
    with pytest.raises(BambuError) as excinfo:
        cmd_gcode(argparse.Namespace(code=gcode, confirm=True, json=False), ctx=ctx)
    assert excinfo.value.exit_code == EXIT_COMMAND_ERROR
    assert ctx.printer_obj.sent == []


@pytest.mark.parametrize(
    "gcode",
    ["M104 S220", "M104 S350", "M190 S60", "M140 S150", "M109 S0", "G28", "M106 S255", "M104 S200 M106 S255", "M141 S40"],
)
def test_ordinary_g_code_still_passes(gcode):
    assert _heater_problem(gcode) is None
    ctx = _Ctx()
    cmd_gcode(argparse.Namespace(code=gcode, confirm=True, json=False), ctx=ctx)
    assert len(ctx.printer_obj.sent) == 1
