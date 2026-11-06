"""plate go: untrusted text is printed literally, and a failed job keeps the sliced file."""

from __future__ import annotations

import io
import os

import pytest
from rich.console import Console

from bambu_cli.errors import BambuError
from bambu_cli.interactive import core
from bambu_cli.interactive.core import WizardState
from bambu_cli.interactive.prompts import Prompts
from bambu_cli.interactive.session import GoDeps, GoSteps, cmd_go
from tests.test_interactive_session import (  # noqa: F401
    Recorder,
    ScriptedPrompts,
    _args,
    _install_ready_settings,
    _make_stl,
    _reset_context,
    _sliced_3mf,
    _tty,
)


def _printed(text: str) -> str:
    buf = io.StringIO()
    Prompts(console=Console(file=buf, force_terminal=False, width=200)).print(text)
    return buf.getvalue()


def test_print_keeps_bracketed_names():
    assert "Benchy [remix].stl" in _printed("Benchy [remix].stl")


def test_print_does_not_raise_on_stray_closing_tag():
    assert "x[/b]" in _printed("File not found: x[/b]")


def test_preview_name_is_display_safe(tmp_path):
    _install_ready_settings(tmp_path)
    state = WizardState(printable_path=str(tmp_path / "a.gcode"), sliced=False)
    rows = dict(core.preview_rows(state, str(tmp_path / "evil\x1b]52;c;AA==\x07.gcode")))
    assert "\x1b" not in rows["Model"]


def test_job_failure_keeps_sliced_file(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path, "keep.gcode.3mf")
    job = Recorder(raises=BambuError("printer offline"))
    steps = GoSteps(download=Recorder(return_value=stl), slice=Recorder(return_value=sliced), job=job)
    prompts = ScriptedPrompts(["https://example.com/cube.stl", True, "PLA", "standard", False, True])
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with pytest.raises(BambuError):
            cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    finally:
        os.chdir(old)
    assert any("keep.gcode.3mf" in m and "kept" in m.lower() for m in prompts.printed)
    assert os.path.exists(sliced)
