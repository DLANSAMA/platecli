"""Phase 5: advanced slice settings — model, screen, plumbing, and read-back.

Three layers:

* pure unit tests over ``tui/settings_model.py`` and ``core.SliceOverrides`` /
  ``apply_overrides`` (no pilot, no slicer);
* pilot tests driving the real ``SettingsScreen`` through ``PrepareScreen``
  with a fake ``GoSteps`` — asserting what the *slice namespace* receives;
* one hermetic end-to-end run against ``tests/fakes/orca_stub`` that reads the
  overrides back out of the temp profiles OrcaSlicer was actually handed (the
  read-back lesson: never assert only on the request).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

pytest.importorskip("textual")

from textual.widgets import Button, Input, OptionList, Select, Static  # noqa: E402

from bambu_cli import context as _context  # noqa: E402
from bambu_cli.interactive.core import SliceOverrides, apply_overrides, overrides_problem  # noqa: E402
from bambu_cli.tui import settings_model as sm  # noqa: E402
from bambu_cli.tui.app import PlateApp  # noqa: E402
from bambu_cli.tui.deps import TuiDeps  # noqa: E402
from bambu_cli.tui.screens.prepare import PrepareScreen  # noqa: E402
from bambu_cli.tui.screens.settings import SettingsScreen  # noqa: E402
from bambu_cli.tui.services import StatusSnapshot  # noqa: E402

_IDLE = StatusSnapshot(ok=True, raw={"gcode_state": "IDLE", "mc_percent": 0}, ams={"units": []})


class ScriptedStatus:
    def __init__(self):
        self.calls = 0

    def fetch(self, args):
        self.calls += 1
        return _IDLE


class Recorder:
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
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _reset_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)


def _install_ready_settings(tmp_path, profiles=None):
    from bambu_cli.context import RuntimeContext, Settings

    orca = tmp_path / "orca"
    orca.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(orca, 0o755)
    if profiles is None:
        profiles = tmp_path / "profiles"
        (profiles / "process").mkdir(parents=True)
    settings = Settings(
        printer_ip="192.168.1.23",
        printer_model="P1S",
        nozzle_size="0.4",
        orca_slicer=str(orca),
        profiles_dir=str(profiles),
    )
    _context.set_current(RuntimeContext(settings=settings))
    return settings


def _args(**kwargs):
    base = {"cmd": "tui", "sim": False, "json": False, "verbose": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _sliced_3mf(path, name="cube.gcode.3mf"):
    p = Path(path) / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config><metadata key="prediction" value="6120"/>'
            '<metadata key="weight" value="13.05"/></config>',
        )
    return str(p)


def _slicer_into_workdir(ns=None, **kwargs):
    return _sliced_3mf(ns.output)


async def _settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _text(widget) -> str:
    """Plain text of a widget's renderable — Rich grids included.

    ``str(table)`` is a repr, not the rendered rows, so a grid-backed panel
    (the preview) has to go through a Console to be asserted on at all.
    """
    renderable = getattr(widget, "renderable", "")
    if isinstance(renderable, str):
        return renderable
    from rich.console import Console

    console = Console(width=200)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


# ---------------------------------------------------------------------------
# Pure: SliceOverrides / apply_overrides
# ---------------------------------------------------------------------------


def _slice_ns():
    from bambu_cli.interactive.presets import preset_to_job_args
    from bambu_cli.job.predict import _slice_args_for_job

    preset = preset_to_job_args("PLA", "standard", False, "cube.stl")
    return _slice_args_for_job("cube.stl", preset, "/tmp/out")


def test_empty_overrides_leave_the_namespace_byte_identical():
    """The wizard guarantee: no overrides ⇒ nothing about the slice changes."""
    ns = _slice_ns()
    before = dict(vars(ns))
    result = apply_overrides(ns, SliceOverrides())
    assert result is ns  # same object: nothing was copied or decorated
    assert vars(result) == before
    # None (the pipeline default) behaves the same way.
    assert apply_overrides(ns, None) is ns
    assert vars(ns) == before


def test_apply_overrides_sets_the_same_dests_the_cli_parser_would():
    from bambu_cli.cli import build_parser

    ns = _slice_ns()
    overrides = SliceOverrides(
        fields={"layer_height": 0.16, "walls": 4, "seam_position": "aligned"},
        process={"sparse_infill_pattern": "gyroid"},
        filament={"filament_flow_ratio": "0.95"},
    )
    decorated = apply_overrides(ns, overrides)
    # The caller's namespace is untouched (a copy is decorated).
    assert getattr(ns, "layer_height", None) is None

    cli = build_parser().parse_args(
        [
            "slice",
            "cube.stl",
            "--layer-height",
            "0.16",
            "--walls",
            "4",
            "--seam-position",
            "aligned",
            "--set",
            "sparse_infill_pattern=gyroid",
            "--set-filament",
            "filament_flow_ratio=0.95",
        ]
    )
    for dest in ("layer_height", "walls", "seam_position", "set_process", "set_filament"):
        assert getattr(decorated, dest) == getattr(cli, dest), dest


def test_apply_overrides_appends_to_existing_generic_overrides():
    ns = _slice_ns()
    ns.set_process = ["already=1"]
    decorated = apply_overrides(ns, SliceOverrides(process={"top_shell_layers": "5"}))
    assert decorated.set_process == ["already=1", "top_shell_layers=5"]
    assert ns.set_process == ["already=1"]  # source list not mutated


def test_overrides_summary_and_counts():
    empty = SliceOverrides()
    assert empty.is_empty() and empty.count() == 0 and empty.summary() == ""
    many = SliceOverrides(fields={"walls": 3, "brim": 2.0}, process={"a": "1"}, filament={"b": "2"})
    assert many.count() == 4
    summary = many.summary()
    assert summary.startswith("4 set (")
    assert "+1" in summary


def test_overrides_problem_uses_the_slice_safety_bounds():
    assert overrides_problem(SliceOverrides()) is None
    assert overrides_problem(SliceOverrides(fields={"nozzle_temp": 220})) is None
    problem = overrides_problem(SliceOverrides(fields={"nozzle_temp": 999}))
    assert problem is not None and "--nozzle-temp must be between" in problem
    # A temperature smuggled in as a generic filament override is caught too.
    assert overrides_problem(SliceOverrides(filament={"nozzle_temperature": "999"})) is not None
    assert "empty setting name" in (overrides_problem(SliceOverrides(process={"": "x"})) or "")


# ---------------------------------------------------------------------------
# Pure: settings model
# ---------------------------------------------------------------------------


def test_every_field_maps_onto_a_real_slice_parser_dest():
    """No invented vocabulary: each field is a dest the CLI slice parser has."""
    from bambu_cli.cli import build_parser

    cli = build_parser().parse_args(["slice", "cube.stl"])
    for field in sm.SETTING_FIELDS:
        assert hasattr(cli, field.dest), f"{field.dest} is not a slice parser dest"


def _slice_parser_actions():
    """The real ``slice`` subparser actions, keyed by dest (never hand-copied)."""
    from bambu_cli.cli import build_parser

    for action in build_parser()._subparsers._group_actions:  # noqa: SLF001 -- introspection in a test
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "slice" in choices:
            return {a.dest: a for a in choices["slice"]._actions}  # noqa: SLF001
    raise AssertionError("slice subparser not found")


def test_choice_fields_never_offer_what_the_cli_would_reject():
    """Every choice the form offers must be accepted by the slice parser."""
    actions = _slice_parser_actions()
    checked = 0
    for field in sm.SETTING_FIELDS:
        if field.kind != "choice":
            continue
        parser_choices = getattr(actions[field.dest], "choices", None)
        assert parser_choices, f"{field.dest} is a choice field but the parser has no choices"
        assert field.choices, f"{field.dest} declares no choices"
        assert set(field.choices) <= set(parser_choices), (
            f"{field.dest} offers {sorted(set(field.choices) - set(parser_choices))} which the CLI rejects"
        )
        checked += 1
    assert checked >= 4  # wall_type, support_type, seam_position, ironing

    # seam_position / ironing mirror the parser exactly; wall_type deliberately
    # omits the deprecated "archaic" alias rather than offering it in a form.
    assert set(sm.field_for("seam_position").choices) == set(actions["seam_position"].choices)
    assert set(sm.field_for("ironing").choices) == set(actions["ironing"].choices)
    assert "archaic" not in sm.field_for("wall_type").choices


def test_seam_and_ironing_reject_values_the_cli_would_refuse():
    seam = sm.field_for("seam_position")
    value, error = sm.parse_field_value(seam, "rear")  # a plausible-but-invalid guess
    assert value is None and "expected one of" in error
    assert sm.parse_field_value(seam, "aligned") == ("aligned", None)
    ironing = sm.field_for("ironing")
    value, error = sm.parse_field_value(ironing, "sometimes")
    assert value is None and "expected one of" in error
    assert sm.parse_field_value(ironing, "topmost") == ("topmost", None)


def test_field_groups_cover_every_field_once():
    grouped = [f.dest for _group, fields in sm.fields_by_group() for f in fields]
    assert sorted(grouped) == sorted(f.dest for f in sm.SETTING_FIELDS)
    assert len(grouped) == len(set(grouped))


def test_parse_field_value_types_and_blanks():
    layer = sm.field_for("layer_height")
    walls = sm.field_for("walls")
    wall_type = sm.field_for("wall_type")
    assert sm.parse_field_value(layer, "") == (None, None)
    assert sm.parse_field_value(layer, "  ") == (None, None)
    assert sm.parse_field_value(layer, "0.16") == (0.16, None)
    assert sm.parse_field_value(walls, "4") == (4, None)
    value, error = sm.parse_field_value(walls, "four")
    assert value is None and "whole number" in error
    value, error = sm.parse_field_value(layer, "thick")
    assert value is None and "expected a number" in error
    assert sm.parse_field_value(wall_type, "classic") == ("classic", None)
    value, error = sm.parse_field_value(wall_type, "spiral")
    assert value is None and "expected one of" in error
    # Range is NOT the model's business — safety bounds live in one place.
    assert sm.parse_field_value(sm.field_for("nozzle_temp"), "999") == (999, None)


def test_collect_field_overrides_reports_every_bad_field():
    parsed, errors = sm.collect_field_overrides({"walls": "4", "infill": "abc", "layer_height": "x"})
    assert parsed == {"walls": 4}
    assert len(errors) == 2














# ---------------------------------------------------------------------------
# Pilot: the screen and the plumbing
# ---------------------------------------------------------------------------


def _profiles_with_keys(tmp_path):
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "filament").mkdir(parents=True)
    (profiles / "process" / "p.json").write_text(
        json.dumps({"type": "process", "name": "p", "layer_height": "0.2", "sparse_infill_pattern": "grid"}),
        encoding="utf-8",
    )
    (profiles / "filament" / "f.json").write_text(
        json.dumps({"type": "filament", "name": "f", "filament_flow_ratio": ["0.98"]}), encoding="utf-8"
    )
    return profiles




async def _add_override(pilot, settings, key, value, bucket=None):
    """Drive the real add path: name the key, pick a bucket, set the value."""
    settings.query_one("#override-key", Input).value = key
    await pilot.pause()  # let Input.Changed reset the bucket before we pick one
    if bucket is not None:
        settings.query_one("#override-bucket", Select).value = bucket
    settings.query_one("#override-value", Input).value = str(value)
    settings.query_one("#override-add", Button).press()
    await pilot.pause()


def _pending(settings):
    option_list = settings.query_one("#override-current", OptionList)
    return [str(option_list.get_option_at_index(i).prompt) for i in range(option_list.option_count)]


def _deps(steps, **kwargs):
    kwargs.setdefault("status_provider", ScriptedStatus())
    kwargs.setdefault("ams_detector", lambda args: None)
    return TuiDeps(steps=steps, **kwargs)


async def _open_settings(pilot, app, stl=None):
    await pilot.press("n")
    await _settle(pilot)
    prepare = app.screen
    assert isinstance(prepare, PrepareScreen)
    prepare.action_settings()
    await _settle(pilot)
    settings = app.screen
    assert isinstance(settings, SettingsScreen)
    return prepare, settings


async def _prepare_with(pilot, app, prepare, source):
    prepare.query_one("#source-input", Input).value = str(source)
    prepare.query_one("#source-input", Input).focus()
    await pilot.press("enter")
    await _settle(pilot)


async def test_form_values_land_on_the_slice_namespace(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    slicer = Recorder(return_value=None)

    def capture(ns=None, **kwargs):
        slicer.calls.append(ns)
        return _slicer_into_workdir(ns)

    steps = GoSteps(download=Recorder(), slice=capture, job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#set-layer-height", Input).value = "0.16"
        settings.query_one("#set-walls", Input).value = "4"
        settings.query_one("#set-copies", Input).value = "2"
        settings.action_apply()
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert "Overrides:" in _text(prepare.query_one("#settings-summary", Static))
        await _prepare_with(pilot, app, prepare, stl)
        preview = _text(prepare.query_one("#preview", Static))

    ns = slicer.calls[0]
    assert ns.layer_height == 0.16
    assert ns.walls == 4
    assert ns.copies == 2
    # Untouched fields keep the preset value, exactly as an unset flag would.
    assert ns.infill == 15
    assert "Overrides" in preview and "3 set" in preview










async def test_non_numeric_field_is_refused_inline(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#set-walls", Input).value = "four"
        settings.action_apply()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)  # still open
        assert "whole number" in _text(settings.query_one("#settings-error", Static))
        assert prepare.overrides.is_empty()


async def test_unsafe_temperature_is_refused_inline(tmp_path):
    """nozzle 999 °C: the slice safety bounds refuse it before anything runs."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    slicer = Recorder()
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slicer, job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#set-nozzle-temp", Input).value = "999"
        settings.action_apply()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        message = _text(settings.query_one("#settings-error", Static))
        assert "--nozzle-temp must be between" in message
        assert prepare.overrides.is_empty()
        # A safe value applies and closes the screen.
        settings.query_one("#set-nozzle-temp", Input).value = "215"
        settings.action_apply()
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert prepare.overrides.fields == {"nozzle_temp": 215}
    assert slicer.calls == []


async def test_cancel_keeps_previous_overrides(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#set-walls", Input).value = "5"
        settings.action_apply()
        await _settle(pilot)
        assert prepare.overrides.fields == {"walls": 5}
        prepare.action_settings()
        await _settle(pilot)
        settings = app.screen
        # The form opens pre-filled with what is in effect.
        assert settings.query_one("#set-walls", Input).value == "5"
        settings.query_one("#set-walls", Input).value = "9"
        await pilot.press("escape")
        await _settle(pilot)
        assert prepare.overrides.fields == {"walls": 5}  # cancel changed nothing


async def test_changing_settings_after_a_preview_forces_a_re_prepare(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        await _prepare_with(pilot, app, prepare, stl)
        assert prepare.result is not None
        workdir = prepare.result.state.workdir
        prepare.action_settings()
        await _settle(pilot)
        settings = app.screen
        settings.query_one("#set-walls", Input).value = "6"
        settings.action_apply()
        await _settle(pilot)
        assert prepare.result is None
        assert prepare.query_one("#print-button", Button).disabled is True
        assert "prepare again" in _text(prepare.query_one("#prepare-status", Static))
    assert not os.path.exists(workdir)


async def test_presliced_source_disables_the_settings_button(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    steps = GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        await _prepare_with(pilot, app, prepare, presliced)
        assert prepare.query_one("#settings-button", Button).disabled is True
        summary = _text(prepare.query_one("#settings-summary", Static))
        assert "pre-sliced" in summary
        preview = _text(prepare.query_one("#preview", Static))
        assert "material settings not applied" in preview
        assert "Overrides" not in preview


async def test_settings_screen_at_80x24(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        assert settings.container_size.width <= 80
        body = settings.query_one("#settings-body")
        assert body.outer_size.width <= 80
        # The form is taller than the terminal, so it must scroll rather than clip.
        assert body.virtual_size.height > body.container_size.height
        settings.query_one("#set-walls", Input).value = "3"
        settings.action_apply()
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)


async def test_s_key_opens_settings_from_the_prepare_screen(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        # Move focus off the source Input, which would otherwise type the "s".
        prepare.query_one("#material-set").focus()
        await pilot.press("s")
        await _settle(pilot)
        assert isinstance(app.screen, SettingsScreen)


# ---------------------------------------------------------------------------
# Read-back: overrides in the temp profiles OrcaSlicer was handed
# ---------------------------------------------------------------------------


def test_overrides_reach_the_temp_profiles_the_slicer_receives(tmp_path, monkeypatch):
    """End-to-end against the fake slicer: read the values back out of the files.

    Asserting on the namespace only proves intent. This runs the real
    ``cmd_slice`` pipeline (profile generation included) with the stub as the
    binary, and reads the *generated temp profiles* the stub was pointed at —
    one process key and one filament key, proving the filament routing that the
    ``filament_flow_ratio`` gotcha is about.
    """
    from bambu_cli.interactive.core import SliceOverrides, apply_overrides
    from bambu_cli.slicer import cmd_slice
    from tests.bambu_test_base import settings_ctx
    from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ORCA_STUB_SCENARIO", "success")
    dump = tmp_path / "profiles_seen.json"
    monkeypatch.setenv("ORCA_STUB_PROFILE_DUMP", str(dump))

    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    base = argparse.Namespace(
        file=model,
        output=str(outdir),
        quality="standard",
        copies=1,
        infill=15,
        pattern="3dhoneycomb",
        supports=False,
        nozzle_temp=220,
        bed_temp=60,
        filament="PLA Basic",
        json=False,
        threads=None,
        list_settings=False,
    )
    overrides = SliceOverrides(
        fields={"layer_height": 0.16},
        process={"sparse_infill_pattern": "gyroid"},
        filament={"filament_flow_ratio": "0.93"},
    )
    ns = apply_overrides(base, overrides)

    with settings_ctx(orca_slicer=launcher, profiles_dir=profiles):
        result = cmd_slice(ns)

    assert os.path.exists(result)
    seen = json.loads(dump.read_text(encoding="utf-8"))
    process_profile = seen["process"]
    filament_profile = seen["filament"]
    # Named flag -> process profile.
    assert process_profile["layer_height"] == "0.16"
    # Generic --set -> process profile.
    assert process_profile["sparse_infill_pattern"] == "gyroid"
    # Generic --set-filament -> FILAMENT profile (never the process one). The
    # value is written as a scalar here because the stub's base filament profile
    # has no filament_flow_ratio key to copy a list shape from; what this test
    # pins is the ROUTING, which is the filament_flow_ratio gotcha.
    assert filament_profile["filament_flow_ratio"] == "0.93"
    assert "filament_flow_ratio" not in process_profile
    assert "sparse_infill_pattern" not in filament_profile


async def test_override_buttons_and_enter_key(tmp_path):
    """The buttons and the Enter key drive the same paths the actions do."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        prepare.query_one("#settings-button", Button).press()  # the button, not the action
        await _settle(pilot)
        settings = app.screen
        assert isinstance(settings, SettingsScreen)

        key_input = settings.query_one("#override-key", Input)
        key_input.value = "top_shell_layers"
        await pilot.pause()
        settings.query_one("#override-value", Input).value = "5"
        key_input.focus()
        await pilot.press("enter")  # submit instead of clicking Add
        await pilot.pause()
        assert "[process] top_shell_layers=5" in _pending(settings)

        # A nameless override is refused inline and adds nothing.
        key_input.value = ""
        await pilot.pause()
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "Type the name" in _text(settings.query_one("#settings-error", Static))
        assert len(_pending(settings)) == 1

        # Clear drops every KEY=VALUE override but leaves the form fields alone.
        settings.query_one("#set-walls", Input).value = "4"
        settings.query_one("#override-clear", Button).press()
        await pilot.pause()
        assert _pending(settings) == []
        settings.query_one("#settings-apply", Button).press()
        await _settle(pilot)
        assert prepare.overrides.fields == {"walls": 4}
        assert prepare.overrides.process == {}


async def test_pending_override_can_be_reloaded_and_removed(tmp_path):
    """The pending list is editable: click to load it back, remove one at a time."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "layer_height", "0.24")
        await _add_override(pilot, settings, "filament_flow_ratio", "0.95", bucket=sm.FILAMENT)
        assert len(_pending(settings)) == 2

        # Clicking a pending row loads it back into the editor for a fix-up.
        current = settings.query_one("#override-current", OptionList)
        current.focus()
        current.highlighted = 0  # [filament] sorts after [process]; index 0 = layer_height
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert settings.query_one("#override-key", Input).value == "layer_height"
        assert settings.query_one("#override-value", Input).value == "0.24"

        # Re-adding the same key updates in place rather than duplicating.
        settings.query_one("#override-value", Input).value = "0.28"
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "[process] layer_height=0.28" in _pending(settings)
        assert len(_pending(settings)) == 2

        current.highlighted = 0
        settings.query_one("#override-remove", Button).press()
        await pilot.pause()
        assert _pending(settings) == ["[filament] filament_flow_ratio=0.95"]

        settings.action_apply()
        await _settle(pilot)
    assert prepare.overrides.process == {}
    assert prepare.overrides.filament == {"filament_flow_ratio": "0.95"}


async def test_cancel_button_discards(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#set-walls", Input).value = "7"
        settings.query_one("#settings-cancel", Button).press()
        await _settle(pilot)
        assert isinstance(app.screen, PrepareScreen)
        assert prepare.overrides.is_empty()


async def test_settings_is_refused_while_a_prepare_is_running(tmp_path):
    import threading

    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    gate = threading.Event()
    started = threading.Event()

    def slow_slice(ns=None, **kwargs):
        started.set()
        gate.wait(10)
        return _slicer_into_workdir(ns)

    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=slow_slice, job=Recorder())))
    try:
        async with app.run_test() as pilot:
            await _settle(pilot)
            await pilot.press("n")
            await _settle(pilot)
            prepare = app.screen
            prepare.query_one("#source-input", Input).value = str(stl)
            prepare.query_one("#source-input", Input).focus()
            await pilot.press("enter")
            while not started.is_set():
                await pilot.pause()
            # Mid-slice: opening the settings screen now would let the user edit
            # settings that the running slice has already consumed.
            prepare.action_settings()
            await pilot.pause()
            assert isinstance(app.screen, PrepareScreen)
            gate.set()
            await _settle(pilot)
            assert prepare.result is not None
    finally:
        gate.set()


def test_field_for_unknown_dest_is_none():
    assert sm.field_for("not_a_real_dest") is None




async def test_s_key_cannot_bypass_the_pre_sliced_settings_gate(tmp_path):
    """The key path is gated exactly like the button, not just the button.

    A pre-sliced .3mf is printed as-is — no slicer runs — so slice overrides
    could never apply. Opening the screen anyway would discard a ready preview
    and demand a re-prepare that still ignores the settings.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    steps = GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        await _prepare_with(pilot, app, prepare, presliced)
        assert prepare.result is not None
        workdir = prepare.result.state.workdir
        assert prepare.query_one("#settings-button", Button).disabled is True

        # The KEY path (focus off the source box, as a keyboard user would be).
        prepare.query_one("#material-set").focus()
        await pilot.press("s")
        await _settle(pilot)

        assert isinstance(app.screen, PrepareScreen), "the s key bypassed the pre-sliced gate"
        assert prepare.result is not None, "the ready preview was discarded"
        assert os.path.isdir(workdir), "the ready-to-print workdir was thrown away"
        assert prepare.query_one("#print-button", Button).disabled is False
        assert "pre-sliced" in _text(prepare.query_one("#settings-summary", Static))
        assert prepare.settings_lock_reason() is not None


async def test_settings_lock_reason_is_clear_once_a_sliced_result_exists(tmp_path):
    """A normally sliced model keeps the settings screen reachable by key."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    steps = GoSteps(download=Recorder(), slice=_slicer_into_workdir, job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        await _prepare_with(pilot, app, prepare, stl)
        assert prepare.settings_lock_reason() is None
        prepare.query_one("#material-set").focus()
        await pilot.press("s")
        await _settle(pilot)
        assert isinstance(app.screen, SettingsScreen)


async def test_bucket_picker_routes_a_filament_key(tmp_path):
    """THE gotcha, end to end: the bucket dropdown is what routes the override.

    OrcaSlicer silently ignores a filament setting sent as a process override, so
    ``--set`` vs ``--set-filament`` is the whole point of the picker. Nothing
    infers it — the user's choice is the routing, exactly as on the command line.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    stl = tmp_path / "cube.stl"
    stl.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    slicer = Recorder()

    def capture(ns=None, **kwargs):
        slicer.calls.append(ns)
        return _slicer_into_workdir(ns)

    steps = GoSteps(download=Recorder(), slice=capture, job=Recorder())
    app = PlateApp(_args(), _deps(steps))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "filament_flow_ratio", "0.9", bucket=sm.FILAMENT)
        # Left alone, the picker stays on process -- what a bare --set does.
        await _add_override(pilot, settings, "top_shell_layers", "5")
        listed = _pending(settings)
        assert "[filament] filament_flow_ratio=0.9" in listed
        assert "[process] top_shell_layers=5" in listed
        settings.action_apply()
        await _settle(pilot)
        await _prepare_with(pilot, app, prepare, stl)

    ns = slicer.calls[0]
    assert ns.set_filament == ["filament_flow_ratio=0.9"]
    assert ns.set_process == ["top_shell_layers=5"]




async def test_named_choice_fields_are_dropdowns(tmp_path):
    """The closed-option flags are picked, not typed — nothing to mistype."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        for dest in ("wall_type", "support_type", "seam_position", "ironing"):
            field = sm.field_for(dest)
            assert field is not None and field.kind == "choice"
            widget = settings.query_one(f"#{field.widget_id}")
            assert isinstance(widget, Select), dest
            # Untouched means "no override", exactly like an unset CLI flag.
            assert widget.value is Select.BLANK, dest

        settings.query_one("#set-seam-position", Select).value = "aligned"
        settings.action_apply()
        await _settle(pilot)
    # Only the field that was picked is set; the other three stay absent.
    assert prepare.overrides.fields == {"seam_position": "aligned"}






async def test_remove_with_nothing_selected_says_so(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        # Nothing added yet, so nothing is highlighted.
        settings.query_one("#override-remove", Button).press()
        await pilot.pause()
        assert "Select a pending override" in _text(settings.query_one("#settings-error", Static))


async def test_pending_values_round_trip_back_into_the_editor(tmp_path):
    """Reloading a pending override restores its key, bucket and value verbatim.

    The bucket has to come back with it: reloading a filament override and
    re-adding it must not silently demote it to a process override, which is the
    silent no-op this screen exists to keep reachable.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "sparse_infill_pattern", "gyroid")
        await _add_override(pilot, settings, "filament_flow_ratio", "0.95", bucket=sm.FILAMENT)
        current = settings.query_one("#override-current", OptionList)

        def reload(key):
            for i in range(current.option_count):
                if key in str(current.get_option_at_index(i).prompt):
                    settings._load_pending(current.get_option_at_index(i).id or "")
                    return
            raise AssertionError(f"{key} not pending")

        reload("sparse_infill_pattern")
        await pilot.pause()
        assert settings.query_one("#override-key", Input).value == "sparse_infill_pattern"
        assert settings.query_one("#override-bucket", Select).value == sm.PROCESS
        assert settings.query_one("#override-value", Input).value == "gyroid"

        reload("filament_flow_ratio")
        await pilot.pause()
        assert settings.query_one("#override-bucket", Select).value == sm.FILAMENT
        assert settings.query_one("#override-value", Input).value == "0.95"
        # Re-adding it unchanged keeps it on the filament side.
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert _pending(settings) == [
            "[process] sparse_infill_pattern=gyroid",
            "[filament] filament_flow_ratio=0.95",
        ]

        # A malformed id is ignored rather than raising.
        settings._load_pending("")
        await pilot.pause()


async def test_an_empty_value_is_a_real_override(tmp_path):
    """Clearing a setting is legitimate — ``--set key=`` does exactly this."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "machine_start_gcode", "")
        assert _pending(settings) == ["[process] machine_start_gcode="]
        settings.action_apply()
        await _settle(pilot)
    assert prepare.overrides.process == {"machine_start_gcode": ""}




async def test_settings_screen_fits_80x24(tmp_path):
    """The screen gained controls; it still has to work on the smallest terminal.

    Phase 4 proved every other screen at 80x24 but not this one. The editor is
    the widest part — a name box, a bucket dropdown, a value control and a
    pending list — so nothing here may push the layout past 80 columns.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        assert app.size.width == 80 and app.size.height == 24
        for widget_id in (
            "#override-key",
            "#override-bucket",
            "#override-current",
            "#settings-buttons",
        ):
            assert settings.query_one(widget_id).outer_size.width <= 80, widget_id
        assert settings.container_size.width <= 80

        # And it is still usable, not merely rendered.
        await _add_override(pilot, settings, "spiral_mode", "1")
        assert "[process] spiral_mode=1" in _pending(settings)
        settings.action_apply()
        await _settle(pilot)
    assert prepare.overrides.process == {"spiral_mode": "1"}


async def test_option_prompts_bypass_rich_markup(tmp_path):
    """Bucket tags must survive rendering, not just exist in the string.

    A ``str`` prompt is parsed as Rich markup, so "[filament] key=0.98" renders as
    " key=0.98" — the bucket tag silently eaten, which is precisely the fact the
    pending list exists to show. Asserting on the prompt string cannot see this
    (the string is intact); passing ``Text`` is what stops it. The same bug would
    corrupt any bracketed *value*, e.g. a list-valued "[0.98]".
    """
    from rich.text import Text

    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "spiral_mode", "1")
        await _add_override(pilot, settings, "filament_flow_ratio", "[0.98]", bucket=sm.FILAMENT)
        pending = settings.query_one("#override-current", OptionList)
        prompts = [pending.get_option_at_index(i).prompt for i in range(pending.option_count)]
        assert all(isinstance(p, Text) for p in prompts), "pending prompts must not be markup-parsed"
        assert [str(p) for p in prompts] == [
            "[process] spiral_mode=1",
            "[filament] filament_flow_ratio=[0.98]",
        ]










