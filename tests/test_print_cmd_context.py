"""Tests verifying cmd_print uses the injected RuntimeContext."""

import argparse
from unittest.mock import MagicMock, patch

from bambu_cli.commands.print_cmd import cmd_print
from bambu_cli.context import RuntimeContext


def test_cmd_print_uses_context_printer():
    fake_printer = MagicMock()
    ctx = MagicMock(spec=RuntimeContext)
    ctx.printer.return_value = fake_printer

    args = argparse.Namespace(
        file="model.gcode",
        dry_run=True,
        confirm=False,
        json=False,
        use_ams=False,
        timelapse=False,
        skip_bed_leveling=False,
        skip_flow_cali=False,
    )

    with (
        patch("bambu_cli.commands.print_cmd._safe_remote_name", return_value="model.gcode"),
        patch("bambu_cli.commands.print_cmd._is_print_ready_name", return_value=True),
        patch("bambu_cli.commands.print_cmd._parse_print_options", return_value=(None, None)),
        patch("bambu_cli.protocols.mqtt.execute_print_command") as mock_exec,
    ):
        cmd_print(args, ctx=ctx)

    ctx.printer.assert_called_once()
    mock_exec.assert_called_once()
    assert mock_exec.call_args[0][0] is fake_printer
