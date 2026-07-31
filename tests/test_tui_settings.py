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

from textual.widgets import Button, Input, OptionList, Select, Static, Switch  # noqa: E402

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
    renderable = getattr(widget, "renderable", "")
    return renderable if isinstance(renderable, str) else str(renderable)


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


def test_catalog_filter_and_bucket_routing(tmp_path):
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "filament").mkdir(parents=True)
    (profiles / "process" / "p.json").write_text(
        json.dumps({"type": "process", "name": "p", "layer_height": "0.2", "flow_ratio": "1"}), encoding="utf-8"
    )
    (profiles / "filament" / "f.json").write_text(
        json.dumps({"type": "filament", "name": "f", "filament_flow_ratio": ["0.98"], "nozzle_temperature": ["220"]}),
        encoding="utf-8",
    )
    catalog = sm.load_catalog(str(profiles))
    keys = {e.key for e in catalog}
    assert {"layer_height", "flow_ratio", "filament_flow_ratio", "nozzle_temperature"} <= keys
    assert "name" not in keys and "type" not in keys  # bookkeeping keys excluded

    flow = sm.filter_catalog(catalog, "flow")
    assert {e.key for e in flow} == {"flow_ratio", "filament_flow_ratio"}
    assert sm.filter_catalog(catalog, "nothing-matches-this") == []
    assert len(sm.filter_catalog(catalog, "")) == len(catalog)

    # THE gotcha: the filament key must be routed to --set-filament, and the
    # same-looking process key to --set. The source profile decides, not the name.
    assert sm.bucket_for_key(catalog, "filament_flow_ratio") == sm.FILAMENT
    assert sm.bucket_for_key(catalog, "flow_ratio") == sm.PROCESS
    assert sm.bucket_for_key(catalog, "totally_unknown_key") == sm.PROCESS
    # An example value is shown for each entry.
    assert next(e for e in catalog if e.key == "filament_flow_ratio").example == "0.98"


def test_catalog_degrades_when_profiles_are_unavailable(tmp_path):
    assert sm.load_catalog(None) == []
    assert sm.load_catalog(str(tmp_path / "nope")) == []


def test_editor_for_infers_the_control_from_observed_values():
    """The control is derived from data, and falls back to text when unsure."""
    # 0/1 is checked before "numeric" or every OrcaSlicer toggle is a number box.
    assert sm.editor_for(("0", "1")) == sm.EDITOR_SWITCH
    assert sm.editor_for(("0",)) == sm.EDITOR_SWITCH
    assert sm.editor_for(("0.2", "0.16")) == sm.EDITOR_NUMBER
    assert sm.editor_for(("220",)) == sm.EDITOR_NUMBER
    assert sm.editor_for(("grid", "gyroid")) == sm.EDITOR_SELECT
    assert sm.editor_for(("aligned", "back", "nearest", "random")) == sm.EDITOR_SELECT
    # Nothing observed -> nothing to infer.
    assert sm.editor_for(()) == sm.EDITOR_TEXT
    # Too many to pick from, too long to be a choice, or multi-line prose
    # (a custom g-code block) all stay as free text.
    assert sm.editor_for(tuple(f"v{i}" for i in range(13))) == sm.EDITOR_TEXT
    assert sm.editor_for(("x" * 41,)) == sm.EDITOR_TEXT
    assert sm.editor_for(("G1 X0\nG1 Y0",)) == sm.EDITOR_TEXT


def test_setting_value_domains_collects_every_observed_value(tmp_path):
    from bambu_cli.slicer.options import setting_value_domains

    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "filament").mkdir(parents=True)
    (profiles / "process" / "a.json").write_text(
        json.dumps({"type": "process", "name": "a", "spiral_mode": "0", "layer_height": "0.2"}),
        encoding="utf-8",
    )
    (profiles / "process" / "b.json").write_text(
        json.dumps({"type": "process", "name": "b", "spiral_mode": "1", "layer_height": "0.2"}),
        encoding="utf-8",
    )
    (profiles / "filament" / "f.json").write_text(
        json.dumps({"type": "filament", "name": "f", "filament_flow_ratio": ["0.98", "1.0"]}),
        encoding="utf-8",
    )
    domains = setting_value_domains(str(profiles))
    # Union across profiles, sorted, de-duplicated -- not just the first seen.
    assert domains["process"]["spiral_mode"] == ("0", "1")
    assert domains["process"]["layer_height"] == ("0.2",)
    # A list-valued setting contributes each element.
    assert domains["filament"]["filament_flow_ratio"] == ("0.98", "1.0")
    # Bookkeeping fields are not settings.
    assert "name" not in domains["process"]
    assert "type" not in domains["process"]


def test_setting_value_domains_degrades_on_unreadable_profiles(tmp_path):
    from bambu_cli.slicer.options import setting_value_domains

    profiles = tmp_path / "empty"
    (profiles / "process").mkdir(parents=True)
    (profiles / "process" / "bad.json").write_text("{not json", encoding="utf-8")
    domains = setting_value_domains(str(profiles))
    assert domains == {"process": {}, "filament": {}}


def test_catalog_entries_carry_their_domain_and_editor(tmp_path):
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "process" / "a.json").write_text(
        json.dumps({"type": "process", "name": "a", "spiral_mode": "0"}), encoding="utf-8"
    )
    (profiles / "process" / "b.json").write_text(
        json.dumps({"type": "process", "name": "b", "spiral_mode": "1"}), encoding="utf-8"
    )
    entry = next(e for e in sm.load_catalog(str(profiles)) if e.key == "spiral_mode")
    assert entry.values == ("0", "1")
    assert entry.editor == sm.EDITOR_SWITCH


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


def _profiles_with_domains(tmp_path):
    """Profiles whose keys span all three inferred editors.

    Two process profiles so a key can be *observed* holding more than one value:
    that is what turns ``sparse_infill_pattern`` into a dropdown and
    ``spiral_mode`` into a toggle, without anyone hand-listing slicer vocabulary.
    """
    profiles = tmp_path / "profiles"
    (profiles / "process").mkdir(parents=True)
    (profiles / "filament").mkdir(parents=True)
    (profiles / "process" / "p1.json").write_text(
        json.dumps(
            {
                "type": "process",
                "name": "p1",
                "layer_height": "0.2",
                "sparse_infill_pattern": "grid",
                "spiral_mode": "0",
            }
        ),
        encoding="utf-8",
    )
    (profiles / "process" / "p2.json").write_text(
        json.dumps(
            {
                "type": "process",
                "name": "p2",
                "layer_height": "0.16",
                "sparse_infill_pattern": "gyroid",
                "spiral_mode": "1",
            }
        ),
        encoding="utf-8",
    )
    (profiles / "filament" / "f.json").write_text(
        json.dumps({"type": "filament", "name": "f", "filament_flow_ratio": ["0.98"]}), encoding="utf-8"
    )
    return profiles


def _put_value(settings, value):
    """Write a value into whichever editor control is currently visible.

    A value the installed profiles never showed goes through the dropdown's
    "custom" entry — the escape hatch that keeps every value the CLI accepts
    reachable, not just the ones the local profiles happen to use.
    """
    from bambu_cli.tui.screens.settings import _CUSTOM_VALUE

    select = settings.query_one("#override-select", Select)
    if select.display:
        if value in settings._select_values:
            select.value = value
        else:
            select.value = _CUSTOM_VALUE
            settings.query_one("#override-value", Input).value = str(value)
        return
    switch = settings.query_one("#override-switch", Switch)
    if switch.display:
        switch.value = value in ("1", 1, True)
        return
    settings.query_one("#override-value", Input).value = str(value)


async def _add_override(pilot, settings, key, value, bucket=None):
    """Drive the real add path: name the key, let it configure, set the value."""
    settings.query_one("#override-key", Input).value = key
    await pilot.pause()  # Input.Changed configures bucket + editor
    if bucket is not None:
        settings.query_one("#override-bucket", Select).value = bucket
    _put_value(settings, value)
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


async def test_browser_search_filters_and_routes_filament_keys(tmp_path):
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
        assert "settings found" in _text(settings.query_one("#browser-status", Static))

        search = settings.query_one("#browser-search", Input)
        search.value = "flow"
        await pilot.pause()
        options = settings.query_one("#browser-list", OptionList)
        assert options.option_count == 1  # only filament_flow_ratio matches
        search.value = "layer"
        await pilot.pause()
        assert settings.query_one("#browser-list", OptionList).option_count == 1

        # One filament key and one process key -- no KEY=VALUE string anywhere.
        await _add_override(pilot, settings, "filament_flow_ratio", "0.95")
        await _add_override(pilot, settings, "sparse_infill_pattern", "gyroid")
        listed = _pending(settings)
        assert "[filament] filament_flow_ratio=0.95" in listed
        assert "[process] sparse_infill_pattern=gyroid" in listed
        settings.action_apply()
        await _settle(pilot)
        await _prepare_with(pilot, app, prepare, stl)

    ns = slicer.calls[0]
    # THE gotcha, end to end: the filament key rode --set-filament, not --set.
    assert ns.set_filament == ["filament_flow_ratio=0.95"]
    assert ns.set_process == ["sparse_infill_pattern=gyroid"]


async def test_selecting_a_browser_row_fills_key_bucket_and_value(tmp_path):
    """Picking a row fills the name, pins the bucket, and seeds the value."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_keys(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#browser-search", Input).value = "filament_flow"
        await pilot.pause()
        options = settings.query_one("#browser-list", OptionList)
        options.focus()
        options.highlighted = 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert settings.query_one("#override-key", Input).value == "filament_flow_ratio"
        # The bucket comes from the profile the key lives in, not from a guess.
        assert settings.query_one("#override-bucket", Select).value == sm.FILAMENT
        assert "0.98" in _text(settings.query_one("#override-default", Static))
        # A lone numeric value infers a number box, seeded with the profile's own.
        assert settings.query_one("#override-value", Input).display is True
        assert settings.query_one("#override-value", Input).value == "0.98"
        assert settings.query_one("#override-select", Select).display is False
        assert settings.query_one("#override-switch", Switch).display is False


async def test_toggle_and_dropdown_editors_come_from_the_profiles(tmp_path):
    """0/1 keys get a switch; a short observed value set gets a dropdown."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)

        # spiral_mode is only ever 0 or 1 across the installed profiles.
        settings.query_one("#override-key", Input).value = "spiral_mode"
        await pilot.pause()
        assert settings.query_one("#override-switch", Switch).display is True
        assert settings.query_one("#override-value", Input).display is False
        settings.query_one("#override-switch", Switch).value = True
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "[process] spiral_mode=1" in _pending(settings)

        # sparse_infill_pattern holds two distinct words -> a dropdown of both.
        settings.query_one("#override-key", Input).value = "sparse_infill_pattern"
        await pilot.pause()
        dropdown = settings.query_one("#override-select", Select)
        assert dropdown.display is True
        assert settings._select_values == ("grid", "gyroid")
        dropdown.value = "gyroid"
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "[process] sparse_infill_pattern=gyroid" in _pending(settings)

        settings.action_apply()
        await _settle(pilot)
    assert prepare.overrides.process == {"spiral_mode": "1", "sparse_infill_pattern": "gyroid"}


async def test_browser_degrades_to_typed_key_and_bucket_picker(tmp_path):
    """No readable profiles: type the name, pick the bucket, still no syntax."""
    from bambu_cli.interactive.core import GoSteps

    # A profiles dir that exists (so preflight passes) but holds no profile
    # JSONs -- the shape of a machine where discovery cannot see anything.
    _install_ready_settings(tmp_path)
    app = PlateApp(_args(sim=True), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        prepare = app.screen
        assert isinstance(prepare, PrepareScreen)
        prepare.action_settings()
        await _settle(pilot)
        settings = app.screen
        assert "No profiles readable here" in _text(settings.query_one("#browser-status", Static))
        assert settings.query_one("#browser-list", OptionList).option_count == 0

        # An unclassifiable key says so, and falls back to free text.
        settings.query_one("#override-key", Input).value = "some_unknown_key"
        await pilot.pause()
        assert "not in the installed profiles" in _text(settings.query_one("#override-default", Static))
        assert settings.query_one("#override-value", Input).display is True
        await _add_override(pilot, settings, "some_unknown_key", "7")
        settings.action_apply()
        await _settle(pilot)
        # Unknown keys are warn-but-pass in the CLI, so they are accepted here.
        assert prepare.overrides.process == {"some_unknown_key": "7"}


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
        assert "Pick a setting" in _text(settings.query_one("#settings-error", Static))
        assert len(_pending(settings)) == 1

        # Clear drops every browsed override but leaves the form fields alone.
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

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "layer_height", "0.24")
        await _add_override(pilot, settings, "filament_flow_ratio", "0.95")
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


def test_load_catalog_survives_a_broken_discovery(monkeypatch, tmp_path):
    """Discovery is a nicety: any failure degrades to free-form entry."""
    import bambu_cli.slicer.options as options_mod

    def boom(_profiles_dir):
        raise RuntimeError("profiles on fire")

    monkeypatch.setattr(options_mod, "setting_catalog", boom)
    assert sm.load_catalog(str(tmp_path)) == []


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


async def test_bucket_picker_routes_a_key_in_degraded_mode(tmp_path):
    """With no profiles to classify against, the bucket dropdown is the routing."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path)  # profiles dir exists but holds no JSONs
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
        assert "No profiles readable here" in _text(settings.query_one("#browser-status", Static))
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


async def test_dropdown_offers_a_custom_value_escape(tmp_path):
    """A picker must never cap what the CLI can express.

    The installed profiles only ever show ``grid`` and ``gyroid``; OrcaSlicer
    accepts far more. The dropdown is a shortcut over what was observed, so it
    carries a "custom" entry that reveals the text box — otherwise inferring the
    control would quietly remove capability the CLI has.
    """
    from bambu_cli.interactive.core import GoSteps
    from bambu_cli.tui.screens.settings import _CUSTOM_VALUE

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#override-key", Input).value = "sparse_infill_pattern"
        await pilot.pause()
        dropdown = settings.query_one("#override-select", Select)
        assert dropdown.display is True
        # The text box is hidden until "custom" is chosen...
        assert settings.query_one("#override-value", Input).display is False
        dropdown.value = _CUSTOM_VALUE
        await pilot.pause()
        # ...and revealed by it.
        assert settings.query_one("#override-value", Input).display is True
        settings.query_one("#override-value", Input).value = "honeycomb"
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "[process] sparse_infill_pattern=honeycomb" in _pending(settings)
        settings.action_apply()
        await _settle(pilot)
    assert prepare.overrides.process == {"sparse_infill_pattern": "honeycomb"}


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


async def test_picking_a_row_survives_the_async_changed(tmp_path):
    """Assigning Input.value posts Changed *later*; it must not undo the prefill.

    Setting the key programmatically fires ``Input.Changed`` asynchronously,
    after the prefill has already run. Without the same-key guard that handler
    reconfigures the editor and wipes the seeded value.

    Deliberately exercised on a *dropdown* key: re-running ``set_options`` is
    what resets a Select back to blank, so a number-valued key would pass this
    test with the guard deleted and prove nothing.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#browser-search", Input).value = "sparse_infill_pattern"
        await pilot.pause()
        options = settings.query_one("#browser-list", OptionList)
        options.focus()
        options.highlighted = 0
        await pilot.pause()
        await pilot.press("enter")
        # Extra settle: give every queued Changed message a chance to land.
        await _settle(pilot)
        await pilot.pause()
        dropdown = settings.query_one("#override-select", Select)
        assert dropdown.display is True
        # Still holding the profile's own value, not reset to blank.
        assert dropdown.value == "grid"
        assert settings.query_one("#override-key", Input).value == "sparse_infill_pattern"


async def test_browsing_to_a_boolean_key_prefills_the_switch(tmp_path):
    """Picking a 0/1 key seeds the toggle from the profile's own value."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#browser-search", Input).value = "spiral_mode"
        await pilot.pause()
        options = settings.query_one("#browser-list", OptionList)
        options.focus()
        options.highlighted = 0
        await pilot.pause()
        await pilot.press("enter")
        await _settle(pilot)
        switch = settings.query_one("#override-switch", Switch)
        assert switch.display is True
        # p1.json is read first and says "0", so the toggle starts off.
        assert switch.value is False


async def test_remove_with_nothing_selected_says_so(tmp_path):
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        # Nothing added yet, so nothing is highlighted.
        settings.query_one("#override-remove", Button).press()
        await pilot.pause()
        assert "Select a pending override" in _text(settings.query_one("#settings-error", Static))


async def test_pending_values_round_trip_back_into_every_editor(tmp_path):
    """Reloading a pending override restores it into the right control.

    Covers all three: a dropdown value the profiles know, a custom value they do
    not, and a toggle. A custom value must come back through the custom entry or
    editing it would silently reset to blank.
    """
    from bambu_cli.interactive.core import GoSteps
    from bambu_cli.tui.screens.settings import _CUSTOM_VALUE

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        await _add_override(pilot, settings, "sparse_infill_pattern", "gyroid")  # in-domain
        await _add_override(pilot, settings, "spiral_mode", "1")  # toggle
        current = settings.query_one("#override-current", OptionList)

        def reload(key):
            for i in range(current.option_count):
                if key in str(current.get_option_at_index(i).prompt):
                    settings._load_pending(current.get_option_at_index(i).id or "")
                    return
            raise AssertionError(f"{key} not pending")

        reload("sparse_infill_pattern")
        await pilot.pause()
        assert settings.query_one("#override-select", Select).value == "gyroid"

        reload("spiral_mode")
        await pilot.pause()
        assert settings.query_one("#override-switch", Switch).value is True

        # A value outside the observed domain returns through "custom".
        settings._process["sparse_infill_pattern"] = "honeycomb"
        reload("sparse_infill_pattern")
        await pilot.pause()
        assert settings.query_one("#override-select", Select).value == _CUSTOM_VALUE
        assert settings.query_one("#override-value", Input).value == "honeycomb"

        # A malformed id is ignored rather than raising.
        settings._load_pending("")
        await pilot.pause()


async def test_unchosen_dropdown_is_refused_rather_than_sent_empty(tmp_path):
    """A blank dropdown means "not chosen yet", not "set this to empty"."""
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test() as pilot:
        await _settle(pilot)
        _prepare, settings = await _open_settings(pilot, app)
        settings.query_one("#override-key", Input).value = "sparse_infill_pattern"
        await pilot.pause()
        assert settings.query_one("#override-select", Select).value is Select.BLANK
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert "Choose a value for sparse_infill_pattern" in _text(settings.query_one("#settings-error", Static))
        assert _pending(settings) == []

        # An empty *text* value is still allowed: clearing a setting is valid.
        settings.query_one("#override-key", Input).value = "unknown_free_text_key"
        await pilot.pause()
        settings.query_one("#override-value", Input).value = ""
        settings.query_one("#override-add", Button).press()
        await pilot.pause()
        assert _pending(settings) == ["[process] unknown_free_text_key="]


async def test_settings_screen_fits_80x24(tmp_path):
    """The screen gained controls; it still has to work on the smallest terminal.

    Phase 4 proved every other screen at 80x24 but not this one. The editor is
    the widest part — a name box, a bucket dropdown, a value control and a
    pending list — so nothing here may push the layout past 80 columns.
    """
    from bambu_cli.interactive.core import GoSteps

    _install_ready_settings(tmp_path, profiles=_profiles_with_domains(tmp_path))
    app = PlateApp(_args(), _deps(GoSteps(download=Recorder(), slice=Recorder(), job=Recorder())))
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(pilot)
        prepare, settings = await _open_settings(pilot, app)
        assert app.size.width == 80 and app.size.height == 24
        for widget_id in (
            "#browser-list",
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
