"""State-machine tests for the ``plate go`` wizard (bambu_cli.interactive.session).

Prompts are scripted through an injected fake (``ScriptedPrompts``); pipeline
collaborators are injected through ``GoSteps`` — no TTY, no printer, no network,
no real slicer. This is exactly why the prompt layer and the steps are injectable.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import context as _context  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.context import RuntimeContext, Settings  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive import prompts as prompts_mod  # noqa: E402
from bambu_cli.interactive import session as session_mod  # noqa: E402
from bambu_cli.interactive.session import GoDeps, GoSteps, cmd_go  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class ScriptedPrompts:
    """A prompt layer that replays a scripted list of answers.

    Each ``text`` / ``choice`` / ``confirm`` call pops the next scripted answer.
    A scripted value of ``prompts_mod.CANCELLED`` simulates Ctrl-C / EOF at that
    prompt. Running out of scripted answers is a test bug and raises loudly.
    """

    def __init__(self, answers):
        self._answers = list(answers)
        self.printed = []
        self.asked = []

    def _next(self, label):
        self.asked.append(label)
        if not self._answers:
            raise AssertionError(f"ScriptedPrompts ran out of answers at '{label}'")
        return self._answers.pop(0)

    def text(self, message, *, default=None):
        return self._next(f"text:{message}")

    def choice(self, message, choices, *, default=None):
        return self._next(f"choice:{message}")

    def confirm(self, message, *, default=False):
        return self._next(f"confirm:{message}")

    def print(self, message=""):
        self.printed.append(message)


class Recorder:
    """A callable that records the namespace it was called with and returns a value."""

    def __init__(self, return_value=None, raises=None):
        self.calls = []
        self.return_value = return_value
        self.raises = raises

    def __call__(self, ns=None, **kwargs):
        self.calls.append(ns)
        if self.raises is not None:
            raise self.raises
        return self.return_value


@pytest.fixture(autouse=True)
def _tty(monkeypatch):
    """cmd_go requires a TTY; pretend stdin is one for the state-machine tests."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    yield


@pytest.fixture(autouse=True)
def _reset_context():
    """Isolate the process-wide RuntimeContext between tests."""
    saved = _context.get_current()
    yield
    _context.set_current(saved)
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None


def _install_ready_settings(tmp_path, **overrides):
    """Install a RuntimeContext whose preflight passes: real orca exe + profiles dir."""
    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(orca, 0o755)
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    settings = Settings(
        printer_ip="192.168.1.23",
        printer_model="P1S",
        nozzle_size="0.4",
        orca_slicer=str(orca),
        profiles_dir=str(profiles),
    )
    settings = replace(settings, **overrides)
    _context.set_current(RuntimeContext(settings=settings))
    return settings


def _make_stl(tmp_path, name="cube.stl"):
    p = tmp_path / name
    p.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    return str(p)


def _sliced_3mf(tmp_path, name="cube.gcode.3mf"):
    """A minimal sliced .3mf carrying a parseable slice_info.config estimate."""
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config>'
            '<metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/>'
            "</config>",
        )
    return str(p)


def _make_zip(tmp_path, member="model.stl", name="bundle.zip"):
    """A local .zip carrying one sliceable model member."""
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(member, "solid cube\nendsolid cube\n")
    return str(p)


def _args(**overrides):
    ns = argparse.Namespace(cmd="go", source=None, json=False, sim=False)
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_calls_download_slice_job_in_order(tmp_path, monkeypatch):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)

    download = Recorder(return_value=stl)
    slicer = Recorder(return_value=sliced)
    job = Recorder(return_value=sliced)
    steps = GoSteps(download=download, slice=slicer, job=job)

    prompts = ScriptedPrompts(
        [
            "https://example.com/cube.stl",  # step 1 source
            True,  # step 2 print on this printer?
            "PLA",  # step 3 material
            "standard",  # step 4 quality
            False,  # step 5 supports
            True,  # step 7 start print now?
        ]
    )
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))

    assert len(download.calls) == 1
    assert download.calls[0].url == "https://example.com/cube.stl"
    assert len(slicer.calls) == 1
    # slice namespace derives from the PLA/standard preset
    assert slicer.calls[0].filament == "Bambu PLA Basic @base"
    assert slicer.calls[0].quality == "standard"
    assert len(job.calls) == 1
    # The final print job runs on the SLICED file with confirm=True.
    assert job.calls[0].source == sliced
    assert job.calls[0].confirm is True
    assert any("Printing" in m for m in prompts.printed)


def test_local_file_source_skips_download(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    download = Recorder(return_value=None)
    slicer = Recorder(return_value=sliced)
    job = Recorder(return_value=sliced)
    steps = GoSteps(download=download, slice=slicer, job=job)

    prompts = ScriptedPrompts([stl, True, "PLA", "standard", False, True])
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))

    assert download.calls == []  # local path: no download
    assert len(slicer.calls) == 1
    assert len(job.calls) == 1


def test_positional_source_skips_first_prompt(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    steps = GoSteps(download=Recorder(), slice=Recorder(return_value=sliced), job=Recorder())
    prompts = ScriptedPrompts([True, "PLA", "standard", False, True])
    cmd_go(_args(source=stl), GoDeps(prompts=prompts, steps=steps))
    # No 'text:' prompt was issued because the positional was used.
    assert not any(a.startswith("text:") for a in prompts.asked)


# ---------------------------------------------------------------------------
# Confirm gate (SABOTAGE-VERIFIED)
# ---------------------------------------------------------------------------


def test_decline_at_confirm_offers_upload_only(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    job = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(return_value=stl), slice=Recorder(return_value=sliced), job=job)

    prompts = ScriptedPrompts(
        [
            "https://example.com/cube.stl",
            True,  # printer
            "PLA",
            "standard",
            False,  # supports
            False,  # start print now? -> NO
            True,  # upload anyway? -> YES
        ]
    )
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))

    # Upload-only path: job is still called, but with confirm=False (no print).
    assert len(job.calls) == 1
    assert job.calls[0].confirm is False


def test_decline_both_calls_nothing_and_keeps_file(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    job = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(return_value=stl), slice=Recorder(return_value=sliced), job=job)

    prompts = ScriptedPrompts(
        ["https://example.com/cube.stl", True, "PLA", "standard", False, False, False]
    )
    monkeypatch_cwd = tmp_path / "cwd"
    monkeypatch_cwd.mkdir()
    old = os.getcwd()
    os.chdir(monkeypatch_cwd)
    try:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    finally:
        os.chdir(old)

    assert job.calls == []  # nothing sent
    assert any("Nothing sent" in m for m in prompts.printed)


# ---------------------------------------------------------------------------
# Cancellation (Ctrl-C / EOF) at each prompt -> exit 5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        [prompts_mod.CANCELLED],  # cancel at source
        ["https://x.com/cube.stl", prompts_mod.CANCELLED],  # cancel at printer
        ["https://x.com/cube.stl", True, prompts_mod.CANCELLED],  # material
        ["https://x.com/cube.stl", True, "PLA", prompts_mod.CANCELLED],  # quality
        ["https://x.com/cube.stl", True, "PLA", "standard", prompts_mod.CANCELLED],  # supports
        # cancel at the final confirm (after download+slice)
        ["https://x.com/cube.stl", True, "PLA", "standard", False, prompts_mod.CANCELLED],
    ],
)
def test_cancel_at_each_step_exits_5(tmp_path, script, capsys):
    _install_ready_settings(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    steps = GoSteps(
        download=Recorder(return_value=_make_stl(tmp_path)),
        slice=Recorder(return_value=sliced),
        job=Recorder(return_value=sliced),
    )
    prompts = ScriptedPrompts(script)
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 5
    assert "cancelled" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Preflight: unconfigured printer -> setup offer
# ---------------------------------------------------------------------------


def test_unconfigured_printer_offers_setup(tmp_path):
    # Start unconfigured (printer_ip == sentinel); setup "fixes" it.
    configured = _install_ready_settings(tmp_path)
    _context.set_current(RuntimeContext(settings=replace(configured, printer_ip="0.0.0.0")))

    setup = Recorder()
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    steps = GoSteps(
        setup=setup,
        download=Recorder(return_value=stl),
        slice=Recorder(return_value=sliced),
        job=Recorder(return_value=sliced),
    )

    # After setup runs, make load_config install a configured context.
    import bambu_cli.config as config_mod

    def fake_load_config(**kwargs):
        _context.set_current(RuntimeContext(settings=configured))

    prompts = ScriptedPrompts(
        [
            True,  # "Run setup now?" -> yes
            stl,  # source
            True,  # printer
            "PLA",
            "standard",
            False,
            True,  # start print
        ]
    )
    import unittest.mock as m

    with m.patch.object(config_mod, "load_config", fake_load_config):
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))

    assert len(setup.calls) == 1


def test_decline_setup_exits_config_error(tmp_path):
    configured = _install_ready_settings(tmp_path)
    _context.set_current(RuntimeContext(settings=replace(configured, printer_ip="0.0.0.0")))
    steps = GoSteps(setup=Recorder(), download=Recorder(), slice=Recorder(), job=Recorder())
    prompts = ScriptedPrompts([False])  # "Run setup now?" -> no
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 1


# ---------------------------------------------------------------------------
# Preflight: missing slicer -> exit 1 BEFORE any prompt
# ---------------------------------------------------------------------------


def test_missing_slicer_exits_1_before_any_prompt(tmp_path):
    configured = _install_ready_settings(tmp_path)
    _context.set_current(RuntimeContext(settings=replace(configured, orca_slicer="/no/such/orca")))
    prompts = ScriptedPrompts([])  # any prompt would raise "ran out of answers"
    steps = GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 1
    assert prompts.asked == []  # no prompt was issued


# ---------------------------------------------------------------------------
# Bad URL re-prompt x3 -> exit 3
# ---------------------------------------------------------------------------


def test_bad_url_reprompt_three_times_exits_3(tmp_path):
    _install_ready_settings(tmp_path)
    prompts = ScriptedPrompts(
        [
            "ftp://bad/scheme",  # invalid scheme (not http/https)
            "also://bad",
            "still://bad",
        ]
    )
    steps = GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 3
    # exactly 3 source prompts were issued
    assert sum(1 for a in prompts.asked if a.startswith("text:")) == 3


# ---------------------------------------------------------------------------
# --sim end-to-end reaching "printed" through the injected prompt layer
# ---------------------------------------------------------------------------


def test_sim_flag_propagates_to_job_namespace(tmp_path):
    """Under --sim the fake job step must receive sim=True and confirm=True."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)

    printed = {"value": False}

    def fake_job(ns=None, **kwargs):
        # Emulate the real job pipeline reaching a print under sim.
        assert ns.confirm is True
        assert ns.sim is True
        printed["value"] = True

    steps = GoSteps(
        download=Recorder(return_value=stl),
        slice=Recorder(return_value=sliced),
        job=fake_job,
    )
    prompts = ScriptedPrompts([stl, True, "PLA", "standard", False, True])
    cmd_go(_args(sim=True), GoDeps(prompts=prompts, steps=steps))
    assert printed["value"] is True


def test_pipeline_runs_download_then_slice_then_job_in_order(tmp_path):
    """A shared sequence log proves download -> slice -> job happen in that order."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)

    sequence = []

    def rec(label, value):
        def _run(ns=None, **kwargs):
            sequence.append(label)
            return value

        return _run

    steps = GoSteps(
        download=rec("download", stl),
        slice=rec("slice", sliced),
        job=rec("job", sliced),
    )
    prompts = ScriptedPrompts(["https://example.com/cube.stl", True, "PLA", "standard", False, True])
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert sequence == ["download", "slice", "job"]


def test_happy_path_carries_temps_into_slice_namespace(tmp_path):
    """The PLA preset temps (220/60->55) reach the slice namespace, not just filament/quality."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    slicer = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(return_value=stl), slice=slicer, job=Recorder(return_value=sliced))
    prompts = ScriptedPrompts(["https://example.com/cube.stl", True, "PLA", "standard", False, True])
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    slice_ns = slicer.calls[0]
    assert slice_ns.nozzle_temp == 220
    assert slice_ns.bed_temp == 55


def test_true_sim_e2e_reaches_printed_through_real_cmd_job(tmp_path):
    """TRUE end-to-end: real cmd_job under ctx.simulation, only the slicer faked.

    The wizard feeds cmd_job a valid minimal sliced .3mf; under the installed
    simulation context the real upload+print steps run against the simulated
    printer and reach "printed" without raising.
    """
    from bambu_cli.commands import cmd_job

    # Install a simulation context exactly as cli.main() would for `--sim`.
    configured = _install_ready_settings(tmp_path)
    sim_ctx = RuntimeContext(settings=replace(configured, serial="SIM123"), simulation=True)
    _context.set_current(sim_ctx)

    stl = _make_stl(tmp_path)

    def fake_slice(ns=None, **kwargs):
        # Produce a valid minimal sliced .3mf (the way test_slicer_estimate builds them).
        out = os.path.join(ns.output, "cube.gcode.3mf")
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr(
                "Metadata/slice_info.config",
                '<config><metadata key="prediction" value="600"/>'
                '<metadata key="weight" value="10"/></config>',
            )
            zf.writestr("Metadata/plate_1.gcode", "; sliced\nG28\n")
        return out

    steps = GoSteps(download=Recorder(return_value=stl), slice=fake_slice, job=cmd_job)
    prompts = ScriptedPrompts([stl, True, "PLA", "standard", False, True])

    # Reaching here without raising means real cmd_job uploaded + printed under sim.
    cmd_go(_args(sim=True), GoDeps(prompts=prompts, steps=steps))
    assert any("Printing" in m for m in prompts.printed)


# ---------------------------------------------------------------------------
# --json + non-TTY behavior
# ---------------------------------------------------------------------------


def test_json_mode_emits_error_envelope_and_exits_5(tmp_path, capsys):
    _install_ready_settings(tmp_path)
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(json=True))
    assert ei.value.exit_code == 5
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["command"] == "go"
    assert payload["exit_code"] == 5
    assert payload["failed_step"] == "parse"


def test_non_tty_stdin_aborts_exit_5(tmp_path, monkeypatch):
    _install_ready_settings(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(BambuError) as ei:
        cmd_go(_args())
    assert ei.value.exit_code == 5
    assert "interactive" in str(ei.value)


# ---------------------------------------------------------------------------
# CLI-level routing: `plate go` reaches the handler past the DNS/network gate,
# and `plate go --json` errors out (exit 5) even with an unconfigured printer.
# ---------------------------------------------------------------------------


def test_cli_go_json_exits_5_even_unconfigured(monkeypatch, tmp_path, capsys):
    from bambu_cli.cli import main

    monkeypatch.setattr(sys, "argv", ["plate", "--json", "go"])
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 5
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert payload["command"] == "go"
    assert payload["failed_step"] == "parse"


def test_cli_go_non_tty_exits_5(monkeypatch, tmp_path):
    from bambu_cli.cli import main

    monkeypatch.setattr(sys, "argv", ["plate", "go"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("bambu_cli.config.CONFIG_PATH", str(tmp_path / "no" / "config.json"))
    monkeypatch.setattr("bambu_cli.cli.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 5


# ---------------------------------------------------------------------------
# BLOCKER regression: a local .zip must be extracted and sliced with the user's
# preset, NOT fall to the printer-ready branch and print at PLA defaults.
# ---------------------------------------------------------------------------


def test_local_zip_extracts_and_slices_with_chosen_material(tmp_path):
    """User picks PETG + a local .zip: the SLICE namespace must carry PETG/255/70.

    Before the fix the .zip fell to the printer-ready branch and cmd_job later
    re-sliced the extracted model at the PLA argparse defaults (220/60),
    discarding the chosen material (plan section 11 Q3 printer-safety bug).
    """
    _install_ready_settings(tmp_path)
    zip_path = _make_zip(tmp_path)
    sliced = _sliced_3mf(tmp_path)

    slicer = Recorder(return_value=sliced)
    job = Recorder(return_value=sliced)
    steps = GoSteps(download=Recorder(return_value=None), slice=slicer, job=job)

    prompts = ScriptedPrompts([zip_path, True, "PETG", "standard", False, True])
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))

    # The zip was extracted and its model fed to the slicer with the PETG preset.
    assert len(slicer.calls) == 1
    slice_ns = slicer.calls[0]
    assert slice_ns.filament == "Bambu PETG Basic @base"
    assert slice_ns.nozzle_temp == 255
    assert slice_ns.bed_temp == 70
    # The sliced model member (an .stl), not the .zip, went to the slicer.
    assert slice_ns.file.endswith(".stl")
    # The job runs on the SLICED .3mf, never the raw zip.
    assert job.calls[0].source == sliced


def test_local_zip_without_model_member_aborts(tmp_path):
    """A .zip with no supported member fails cleanly before any print."""
    _install_ready_settings(tmp_path)
    p = tmp_path / "empty.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("readme.txt", "no model here")
    job = Recorder()
    steps = GoSteps(download=Recorder(return_value=None), slice=Recorder(), job=job)
    prompts = ScriptedPrompts([str(p), True, "PETG", "standard", False, True])
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 3
    assert job.calls == []


# ---------------------------------------------------------------------------
# MAJOR regression: decline-both must not relocate / overwrite a user's own
# pre-sliced file. Only files inside our temp workdir get moved into cwd.
# ---------------------------------------------------------------------------


def test_decline_both_leaves_user_presliced_file_in_place(tmp_path):
    """A user-supplied local .3mf (never in the workdir) stays exactly where it is."""
    _install_ready_settings(tmp_path)
    user_dir = tmp_path / "mystuff"
    user_dir.mkdir()
    user_file = _sliced_3mf(user_dir, name="mine.gcode.3mf")

    # Local pre-sliced source: no download, no slice (printer-ready branch).
    steps = GoSteps(download=Recorder(return_value=None), slice=Recorder(), job=Recorder())
    prompts = ScriptedPrompts([user_file, True, "PLA", "standard", False, False, False])

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    finally:
        os.chdir(old)

    # The user's file was NOT moved and no copy landed in cwd.
    assert os.path.exists(user_file)
    assert not os.path.exists(cwd / "mine.gcode.3mf")
    assert any(user_file in m for m in prompts.printed)


def test_decline_both_relocates_workdir_file_without_clobbering(tmp_path):
    """A file inside the temp workdir is moved to cwd, never overwriting a same-named file."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    # A pre-existing same-named file in cwd that must survive untouched.
    preexisting = cwd / "cube.gcode.3mf"
    preexisting.write_bytes(b"USER-ORIGINAL")

    def fake_slice(ns=None, **kwargs):
        # Produce the sliced file INSIDE the workdir (as the real slicer does).
        out = os.path.join(ns.output, "cube.gcode.3mf")
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr(
                "Metadata/slice_info.config",
                '<config><metadata key="prediction" value="600"/>'
                '<metadata key="weight" value="10"/></config>',
            )
        return out

    steps = GoSteps(download=Recorder(return_value=stl), slice=fake_slice, job=Recorder())
    prompts = ScriptedPrompts(["https://example.com/cube.stl", True, "PLA", "standard", False, False, False])

    old = os.getcwd()
    os.chdir(cwd)
    try:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    finally:
        os.chdir(old)

    # The original cwd file is untouched; the kept file landed under a -N name.
    assert preexisting.read_bytes() == b"USER-ORIGINAL"
    kept_line = next(m for m in prompts.printed if "kept at" in m)
    assert "cube.gcode-1.3mf" in kept_line
    assert os.path.exists(cwd / "cube.gcode-1.3mf")


# ---------------------------------------------------------------------------
# MINOR regression: pre-sliced source flags "material settings not applied".
# ---------------------------------------------------------------------------


def test_presliced_source_preview_notes_material_not_applied(tmp_path):
    _install_ready_settings(tmp_path)
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    steps = GoSteps(download=Recorder(return_value=None), slice=Recorder(), job=Recorder(return_value=presliced))
    prompts = ScriptedPrompts([presliced, True, "PETG", "standard", False, True])
    cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert any("pre-sliced — material settings not applied" in m for m in prompts.printed)
    # And it does NOT claim the chosen PETG applied.
    assert not any("Material   PETG" in m for m in prompts.printed)


def test_leading_dash_source_rejected_without_argparse_exit(tmp_path):
    """A local file named '-foo.stl' is rejected as a source, not detonated in argparse."""
    _install_ready_settings(tmp_path)
    # Three bad (dash) attempts -> exit 3 via the normal re-prompt path, never SystemExit.
    prompts = ScriptedPrompts(["-foo.stl", "-bar.stl", "-baz.stl"])
    steps = GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())
    with pytest.raises(BambuError) as ei:
        cmd_go(_args(), GoDeps(prompts=prompts, steps=steps))
    assert ei.value.exit_code == 3
