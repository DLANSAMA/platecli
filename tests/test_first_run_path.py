"""The first-run person path: what a Bambu owner who has never used a CLI sees.

Covers the copy contract end to end — bare ``plate`` off a TTY, the ``--confirm``
/ ``--sim`` / ``go`` help strings, the ``go`` / ``tui`` non-TTY refusal, the
missing-OrcaSlicer and HTML-page errors printing exactly once with a next step,
and the README / manual leading with the same path. No printer, no network, no
real slicer: ``--sim`` plus injected collaborators throughout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli.cli import build_parser, main
from bambu_cli.cliparse import first_run_text
from bambu_cli.errors import BambuError

ROOT = Path(__file__).resolve().parents[1]
CUBE = str(ROOT / "tests" / "fixtures" / "cube.stl")


def _subparser(name):
    for action in build_parser()._actions:
        if getattr(action, "choices", None) and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"no subparser {name!r}")


def _write_config(tmp_path, **overrides):
    cfg = {
        "printer_ip": "192.0.2.1",
        "serial": "01P00A000000000",
        "access_code": "12345678",
        "model": "P1S",
    }
    cfg.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(path, 0o600)
    return str(path)


def _cli(monkeypatch, argv, config_path):
    """Point main() at ``config_path`` with a silent, mockable logger."""
    monkeypatch.setattr(sys, "argv", ["plate", *argv])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", config_path)
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)


def _messages(mock_logger, level):
    return [str(c.args[0]) for c in getattr(mock_logger, level).call_args_list]


# ---------------------------------------------------------------------------
# 1. bare `plate` off a TTY: the short person path, not the argparse dump
# ---------------------------------------------------------------------------


def test_first_run_text_is_the_person_path_in_order():
    text = first_run_text()
    steps = [
        "Install OrcaSlicer",
        "not Bambu Studio",
        "LAN mode",
        "not your\n     Bambu account password",
        "factory-reset",
        "plate setup",
        "plate go",
    ]
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions), "first-run steps must appear in the order a person needs them"
    # Short by construction: a screenful, not twenty subcommands.
    assert len(text.splitlines()) < 25
    assert "positional arguments" not in text
    # Scripts are pointed at job, with --confirm named as the thing that starts a print.
    assert "plate job <url> --json" in text and "--confirm" in text


def test_first_run_text_names_the_platform_install_command(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "brew install --cask orcaslicer" in first_run_text()
    monkeypatch.setattr(sys, "platform", "win32")
    assert "winget install --id SoftFever.OrcaSlicer" in first_run_text()


def test_bare_plate_non_tty_prints_first_run_text_not_help(monkeypatch, tmp_path, capsys):
    from bambu_cli import commands as commands_mod

    _cli(monkeypatch, [], str(tmp_path / "missing" / "config.json"))
    monkeypatch.setattr(commands_mod, "cmd_go", lambda args: (_ for _ in ()).throw(AssertionError("wizard ran")))
    monkeypatch.setattr(commands_mod, "cmd_tui", lambda args: (_ for _ in ()).throw(AssertionError("tui ran")))

    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == first_run_text().strip()


def test_bare_plate_json_keeps_error_envelope(monkeypatch, tmp_path, capsys):
    """--json is a machine flag: the envelope contract wins over the first-run text."""
    _cli(monkeypatch, ["--json"], str(tmp_path / "missing" / "config.json"))
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 5
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "error"
    assert "plate setup" not in captured.err


# ---------------------------------------------------------------------------
# 2. help strings: --confirm (job/send), --sim, go
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["job", "send"])
def test_job_confirm_help_admits_upload_without_print(command):
    help_text = " ".join(_subparser(command).format_help().split())
    assert "uploaded_not_printed" in help_text
    assert "uploaded to the printer" in help_text


@pytest.mark.parametrize("command", ["print", "stop", "pause", "resume", "gcode", "delete"])
def test_gated_commands_still_take_confirm(command):
    assert "--confirm" in _subparser(command).format_help()


def test_sim_help_says_fake_printer_not_protocol_test():
    help_text = " ".join(build_parser().format_help().split())
    assert "fake printer" in help_text
    assert "no hardware and no printer config" in help_text
    assert "not a protocol test" in help_text
    assert "Enable simulation mode" not in help_text


def test_go_help_does_not_promise_no_slicer():
    top = " ".join(build_parser().format_help().split())
    go = " ".join(_subparser("go").format_help().split())
    for text in (top, go):
        assert "no slicer knowledge" not in text
        assert "without touching a slicer" not in text
    assert "OrcaSlicer still does the slicing" in top


# ---------------------------------------------------------------------------
# 3. go / tui non-TTY refusal copy
# ---------------------------------------------------------------------------


def _assert_refusal_copy(message, command):
    assert message.startswith(f"plate {command} is interactive")
    assert "plate job <url> --json" in message
    assert "add --confirm only to start the print" in message
    assert "plate --sim status" in message
    # The old copy handed scripts a loaded gun.
    assert "use 'plate job <url> --confirm' for scripts" not in message


def test_go_non_tty_refusal_names_confirm_as_the_print_trigger(monkeypatch):
    from bambu_cli.interactive.session import _NON_TTY_MESSAGE, cmd_go

    _assert_refusal_copy(_NON_TTY_MESSAGE, "go")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(BambuError) as ei:
        cmd_go(MagicMock(json=False, sim=False))
    assert str(ei.value) == _NON_TTY_MESSAGE
    assert ei.value.exit_code == 5


def test_tui_non_tty_refusal_names_confirm_as_the_print_trigger(monkeypatch):
    from bambu_cli.tui.entry import _NON_TTY_MESSAGE, cmd_tui

    _assert_refusal_copy(_NON_TTY_MESSAGE, "tui")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(BambuError) as ei:
        cmd_tui(MagicMock(json=False))
    assert str(ei.value) == _NON_TTY_MESSAGE
    assert ei.value.exit_code == 5


def test_troubleshooting_quotes_the_current_tui_refusal():
    from bambu_cli.tui.entry import _NON_TTY_MESSAGE

    doc = (ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    assert _NON_TTY_MESSAGE in doc


# ---------------------------------------------------------------------------
# 4. errors print once and name the next step
# ---------------------------------------------------------------------------


def test_expected_failure_is_logged_once_through_main(monkeypatch, tmp_path):
    """emit_json_error logs and raises; cli.main must not log the same line again."""
    from bambu_cli import commands as commands_mod
    from bambu_cli.utils import emit_json_error

    _cli(monkeypatch, ["--sim", "status"], _write_config(tmp_path))

    def failing_status(args, **_kw):
        emit_json_error(args, "status", 3, "the one and only line", failed_step="probe")

    monkeypatch.setattr(commands_mod, "cmd_status", failing_status)
    with patch("bambu_cli.logging_utils._BACKEND") as log, pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 3
    assert _messages(log, "error").count("the one and only line") == 1


def test_plain_bambu_error_is_still_logged_by_main(monkeypatch, tmp_path):
    """A raise that did NOT log itself keeps getting its one line from cli.main."""
    from bambu_cli import commands as commands_mod

    _cli(monkeypatch, ["--sim", "status"], _write_config(tmp_path))

    def failing_status(args, **_kw):
        raise BambuError("unlogged failure", exit_code=3, failed_step="probe")

    monkeypatch.setattr(commands_mod, "cmd_status", failing_status)
    with patch("bambu_cli.logging_utils._BACKEND") as log, pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 3
    assert _messages(log, "error").count("unlogged failure") == 1


def test_missing_orca_prints_once_and_says_install_then_setup(monkeypatch, tmp_path):
    config = _write_config(tmp_path, orca_slicer=str(tmp_path / "nope" / "orca-slicer"), profiles_dir=str(tmp_path))
    _cli(monkeypatch, ["--sim", "slice", CUBE], config)
    # Nothing installed anywhere on this machine, whatever the CI host has.
    monkeypatch.setattr("bambu_cli.config.detect_orca_slicer", lambda: None)

    with patch("bambu_cli.logging_utils._BACKEND") as log, pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 1
    errors = [m for m in _messages(log, "error") if "OrcaSlicer not found" in m]
    assert len(errors) == 1, errors
    assert "then run `plate setup`" in errors[0]
    # The contradictory "edit config.json / tools/" hint is gone when there is nothing to point at.
    assert not any("update 'orca_slicer'" in m for m in _messages(log, "info"))


def test_missing_orca_still_names_a_detected_install(monkeypatch, tmp_path):
    detected = tmp_path / "real" / "orca-slicer"
    detected.parent.mkdir()
    detected.write_text("", encoding="utf-8")
    detected.chmod(0o755)
    config = _write_config(tmp_path, orca_slicer=str(tmp_path / "nope" / "orca-slicer"), profiles_dir=str(tmp_path))
    _cli(monkeypatch, ["--sim", "slice", CUBE], config)
    monkeypatch.setattr("bambu_cli.config.detect_orca_slicer", lambda: str(detected))

    with patch("bambu_cli.logging_utils._BACKEND") as log, pytest.raises(SystemExit):
        main()
    assert any("Detected OrcaSlicer at" in m for m in _messages(log, "info"))
    assert sum("OrcaSlicer not found" in m for m in _messages(log, "error")) == 1


def test_doctor_without_printer_points_at_the_no_printer_checks(monkeypatch, tmp_path):
    from tests.bambu_test_base import settings_ctx

    # No config file, and the baseline context pinned to "unconfigured" (a missing
    # file leaves whatever context is installed in place).
    _cli(monkeypatch, ["doctor"], str(tmp_path / "missing" / "config.json"))
    with (
        settings_ctx(printer_ip="0.0.0.0"),
        patch("bambu_cli.logging_utils._BACKEND") as log,
        pytest.raises(SystemExit) as ei,
    ):
        main()
    assert ei.value.code == 1
    errors = _messages(log, "error")
    assert len(errors) == 1
    assert "plate setup" in errors[0]
    assert "plate preflight" in errors[0] and "plate --sim status" in errors[0]


def test_other_printer_commands_keep_the_short_not_configured_line(monkeypatch, tmp_path):
    from tests.bambu_test_base import settings_ctx

    _cli(monkeypatch, ["status"], str(tmp_path / "missing" / "config.json"))
    with (
        settings_ctx(printer_ip="0.0.0.0"),
        patch("bambu_cli.logging_utils._BACKEND") as log,
        pytest.raises(SystemExit),
    ):
        main()
    errors = _messages(log, "error")
    assert errors == ["Printer IP is not configured. Please run `plate setup` first."]


# ---------------------------------------------------------------------------
# 5. README and manual lead with the person path; job --confirm is demoted
# ---------------------------------------------------------------------------


def _section(markdown, heading):
    match = re.search(rf"^{re.escape(heading)}\n(.*?)(?=^## |\Z)", markdown, flags=re.MULTILINE | re.DOTALL)
    assert match, f"missing section {heading!r}"
    return match.group(1)


def test_readme_hero_is_the_person_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    print_something = _section(readme, "## Print something")
    for phrase in ("Bambu Studio", "LAN mode", "account password", "factory-reset", "plate setup", "plate go"):
        assert phrase in print_something, phrase
    # The human path comes before the agent one-liner.
    assert print_something.index("plate go") < print_something.index("plate job")
    # The pipeline hero line no longer sells `job --confirm` as *the* command.
    hero = readme[: readme.index("## Install")]
    assert "plate job <url> --confirm" not in hero
    # OrcaSlicer is called out as a second slicer before the pip install line.
    install = _section(readme, "## Install")
    assert install.index("not Bambu Studio") < install.index("pip install platecli")


def test_readme_confirm_copy_is_honest_about_job_and_send():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uploaded_not_printed" in readme
    assert "still download, slice, and upload" in readme
    for stale in ("nothing on the printer moves", "without touching a slicer", "no slicer knowledge"):
        assert stale not in readme, stale


def test_manual_leads_with_first_print_and_demotes_agents():
    manual = (ROOT / "docs" / "manual.md").read_text(encoding="utf-8")
    toc = re.findall(r"^- \[([^\]]+)\]\(#", manual, flags=re.MULTILINE)
    assert toc[0] == "Your first print"
    assert toc.index("Setup") < toc.index("Use with AI agents")
    assert toc.index("Setup") < toc.index("Installing from source")
    headings = re.findall(r"^## (.+)$", manual, flags=re.MULTILINE)
    assert headings[0] == "Your first print"
    first = _section(manual, "## Your first print")
    for phrase in ("Bambu Studio", "LAN mode", "account password", "plate setup", "plate go"):
        assert phrase in first, phrase
    assert "uploaded_not_printed" in manual
    assert "without touching a slicer" not in manual
