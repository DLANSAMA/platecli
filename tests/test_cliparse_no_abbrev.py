"""Option abbreviations are off, so the literal ``--confirm`` gate cannot be bypassed by ``--conf``."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from bambu_cli import cli
from bambu_cli.cliparse import JsonArgumentParser, build_parser
from bambu_cli.constants import EXIT_COMMAND_ERROR


def _subparsers(parser):
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def test_every_parser_is_json_parser_without_abbrev():
    parser = build_parser()
    assert isinstance(parser, JsonArgumentParser) and parser.allow_abbrev is False
    subs = _subparsers(parser)
    assert subs
    for name, sub in subs.items():
        assert isinstance(sub, JsonArgumentParser), name
        assert sub.allow_abbrev is False, name
        for nested in _subparsers(sub).values():
            assert nested.allow_abbrev is False


@pytest.mark.parametrize(
    "argv",
    [
        ["stop", "--conf"],
        ["print", "x.3mf", "--con"],
        ["gcode", "G28", "--conf"],
        ["delete", "x.3mf", "--conf"],
    ],
)
@pytest.mark.parametrize("json_mode", [False, True])
def test_abbreviated_confirm_is_a_usage_error(argv, json_mode, capsys):
    argv = argv + (["--json"] if json_mode else [])
    with (
        patch("sys.argv", ["plate", *argv]),
        patch("bambu_cli.commands.cmd_stop") as stop,
        patch("bambu_cli.commands.cmd_print") as pr,
        patch("bambu_cli.commands.cmd_gcode") as gc,
        patch("bambu_cli.commands.cmd_delete") as de,
        pytest.raises(SystemExit) as exc,
    ):
        cli.main()
    assert exc.value.code == EXIT_COMMAND_ERROR
    for m in (stop, pr, gc, de):
        m.assert_not_called()
    out = capsys.readouterr()
    if json_mode:
        payload = json.loads(out.out)
        assert payload["failed_step"] == "parse"
        assert payload["exit_code"] == EXIT_COMMAND_ERROR


def test_full_confirm_still_parses():
    args = build_parser().parse_args(["stop", "--confirm"])
    assert args.confirm is True
