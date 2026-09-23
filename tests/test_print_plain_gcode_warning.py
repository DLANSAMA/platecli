"""Printing a plain .gcode file is flagged as unverified on hardware.

``print`` starts every file with the ``project_file`` command, whose ``param``
names ``Metadata/plate_<n>.gcode`` inside a 3MF. A plain .gcode has no such
member. The community protocol notes list a separate ``gcode_file`` command,
but the only first-hand report found (X1C, LAN mode) could not get it to start
a custom file, so neither path is confirmed. Until one is verified on a real
printer, platecli says so before sending instead of implying it works.
"""

from __future__ import annotations

import argparse
from unittest import mock

import pytest


@pytest.mark.parametrize("name, warned", [("part.gcode", True), ("part.3mf", False), ("part.gcode.3mf", False)])
def test_plain_gcode_print_warns_before_sending(monkeypatch, name, warned):
    from bambu_cli.commands import print_cmd

    monkeypatch.setattr("bambu_cli.protocols.mqtt.execute_print_command", lambda *a, **k: None)
    with mock.patch.object(print_cmd.logger, "warning") as warning:
        print_cmd.cmd_print(
            argparse.Namespace(file=name, confirm=True, dry_run=False, json=False),
            ctx=argparse.Namespace(printer=lambda: object()),
        )
    texts = " ".join(str(call.args[0]) for call in warning.call_args_list)
    assert ("not verified" in texts) is warned
