"""Entry-level contracts: --version, parser errors, missing subcommand, config errors, and the --confirm gate."""

from tests.json_contract_base import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# JsonArgumentParser bad-argument contract
# ---------------------------------------------------------------------------


def test_bad_argument_parse_error_shape(monkeypatch, tmp_path, capsys):
    # slice requires a positional "file"; omit it under --json to trigger
    # argparse's own error() path (JsonArgumentParser.error).
    exc = run_main(monkeypatch, tmp_path, ["slice", "--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["slice"]},
                "failed_step": {"enum": ["parse"]},
                "exit_code": {"enum": [5]},
                "error": STR,
            },
        },
    )
    assert capsys.readouterr().err.strip() == ""


def test_bad_argument_parse_error_shape_global_json_flag(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json", "job"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert payload["status"] == "error"
    assert payload["failed_step"] == "parse"
    assert payload["command"] == "job"


# ---------------------------------------------------------------------------
# main(): missing-subcommand contract
# ---------------------------------------------------------------------------


def test_missing_subcommand_json_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["error"]},
                "command": {"enum": ["main"]},
                "failed_step": {"enum": ["parse"]},
                "exit_code": {"enum": [5]},
                "error": STR,
            },
        },
    )


def test_missing_subcommand_without_json_prints_usage_not_json(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, [])
    assert exc is not None and exc.code == 5
    out, err = capsys.readouterr()
    assert out.strip() == ""
    assert "usage:" in err.lower()


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_json_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["--json", "--version"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["ok"]},
                "command": {"enum": ["version"]},
                "version": STR,
            },
        },
    )
    assert payload["version"] == __import__("bambu_cli.constants", fromlist=["VERSION"]).VERSION


# ---------------------------------------------------------------------------
# config-error contract (printer-network command, no config, no --sim)
# ---------------------------------------------------------------------------


def test_config_error_shape_for_network_command(monkeypatch, tmp_path, capsys):
    # Force the "never configured" state (default printer_ip 0.0.0.0)
    # explicitly so this test doesn't depend on run order.
    from bambu_cli import context
    from bambu_cli.context import RuntimeContext

    context.set_current(RuntimeContext())
    exc = run_main(monkeypatch, tmp_path, ["status", "--json"])
    assert exc is not None and exc.code == 1
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("status"))
    assert payload["failed_step"] == "config"


# ---------------------------------------------------------------------------
# Parser-driven --confirm gate: locks the whole refusal contract in one place.
# ---------------------------------------------------------------------------

# subcommand -> (extra argv, payload key that must be False in the refusal)
PHYSICAL_COMMANDS = {
    "print": (["ready.3mf"], "printed"),
    "stop": ([], "stopped"),
    "pause": ([], "paused"),
    "resume": ([], "resumed"),
    "gcode": (["M105"], "sent"),
    "delete": (["old.3mf"], "deleted"),
}

# Subcommands that expose --confirm but do NOT refuse without it: for job/send
# the download/slice/upload really happened, so they exit 0 with
# "uploaded_not_printed" (bambu_cli/job/orchestrate.py). Deliberate.
NON_REFUSING_CONFIRM_COMMANDS = {"job", "send"}


def _subcommands_with_confirm():
    parser = build_parser()
    names = set()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            if any("--confirm" in a.option_strings for a in subparser._actions):
                names.add(name)
    return names


def test_confirm_flag_inventory_matches_physical_commands():
    """A new --confirm command must be classified here, or this fails."""
    assert _subcommands_with_confirm() == set(PHYSICAL_COMMANDS) | NON_REFUSING_CONFIRM_COMMANDS


@pytest.mark.parametrize("cmd", sorted(PHYSICAL_COMMANDS))
def test_physical_commands_refuse_without_confirm(cmd, monkeypatch, tmp_path, capsys):
    extra, false_key = PHYSICAL_COMMANDS[cmd]
    exc = run_main(monkeypatch, tmp_path, ["--sim", cmd, *extra, "--json"])
    assert exc is not None and exc.code == 5, f"{cmd} must refuse with EXIT_COMMAND_ERROR"
    payload = read_json(capsys)
    assert payload["status"] == "confirmation_required"
    assert payload["command"] == cmd
    assert payload[false_key] is False
    assert "--confirm" in payload["next_command"]


# ---------------------------------------------------------------------------
# tui: interactive-only error-envelope contract (mirrors go)
# ---------------------------------------------------------------------------


def test_tui_json_error_envelope_shape(monkeypatch, tmp_path, capsys):
    # `plate tui --json` never launches the UI: it emits the standard error
    # envelope (exit 5, failed_step parse) exactly like `go`.
    exc = run_main(monkeypatch, tmp_path, ["--json", "tui"])
    assert exc is not None and exc.code == 5
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("tui"))
    assert payload["failed_step"] == "parse"


def test_tui_non_tty_stdin_exits_5(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    exc = run_main(monkeypatch, tmp_path, ["tui"])
    assert exc is not None and exc.code == 5
