"""Unit tests for the shared wizard/TUI core (bambu_cli.interactive.core).

These cover the pieces both front-ends depend on directly — source validation,
AMS detection, the prepare pipeline, the preview rows, the job namespace and
workdir hygiene — without a TTY, a pilot, a printer, or a real slicer. The
wizard's own choreography stays covered by tests/test_interactive_session.py.
"""

from __future__ import annotations

import argparse
import os
import zipfile

import pytest

from bambu_cli import context as _context  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.context import RuntimeContext, Settings  # noqa: E402
from bambu_cli.errors import BambuError  # noqa: E402
from bambu_cli.interactive import core  # noqa: E402

@pytest.fixture(autouse=True)
def _reset_context():
    saved = _context.get_current()
    yield
    _context.set_current(saved)
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None

def _install_ready_settings(tmp_path, **overrides):
    from dataclasses import replace

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

def _args(**overrides):
    ns = argparse.Namespace(cmd="tui", json=False, sim=False, verbose=False)
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns

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

# ---------------------------------------------------------------------------
# validate_source
# ---------------------------------------------------------------------------

def test_validate_source_accepts_local_model(tmp_path):
    stl = _make_stl(tmp_path)
    source, error = core.validate_source(f"  {stl}  ")
    assert source == stl
    assert error is None

def test_validate_source_rejects_empty_missing_dir_and_extension(tmp_path):
    assert core.validate_source("")[1] == "Please enter a URL or file path."
    assert "File not found" in core.validate_source(str(tmp_path / "nope.stl"))[1]
    assert "directory" in core.validate_source(str(tmp_path))[1]
    bad = tmp_path / "notes.txt"
    bad.write_text("x", encoding="utf-8")
    assert "Unsupported file type" in core.validate_source(str(bad))[1]

def test_validate_source_rejects_leading_dash(tmp_path):
    source, error = core.validate_source("-foo.stl")
    assert source is None
    assert error is not None and error.startswith("Source cannot start with '-'")

def test_validate_source_accepts_http_url():
    source, error = core.validate_source("https://example.com/cube.stl")
    assert error is None
    assert source == "https://example.com/cube.stl"

# ---------------------------------------------------------------------------
# AMS detection
# ---------------------------------------------------------------------------

def test_match_material_preset_maps_known_and_unknown():
    assert core.match_material_preset("pla") == "PLA"
    assert core.match_material_preset(" abs ") == "ABS"
    assert core.match_material_preset("PLA-CF") is None
    assert core.match_material_preset(None) is None

def test_detect_ams_material_records_slot_from_callback():
    def detector(args, on_active_slot=None):
        on_active_slot(3)
        return "PETG"

    material, slot = core.detect_ams_material(detector, _args())
    assert (material, slot) == ("PETG", 3)

def test_detect_ams_material_supports_single_arg_test_seams():
    material, slot = core.detect_ams_material(lambda args: "PLA", _args())
    assert (material, slot) == ("PLA", None)

def test_detect_ams_material_drops_unknown_material_and_its_slot():
    def detector(args, on_active_slot=None):
        on_active_slot(2)
        return "PLA-CF"

    assert core.detect_ams_material(detector, _args()) == (None, None)

def test_read_loaded_ams_material_swallows_errors(tmp_path, monkeypatch):
    _install_ready_settings(tmp_path)

    class BoomPrinter:
        def status(self):
            raise RuntimeError("mqtt down")

    monkeypatch.setattr("bambu_cli.context.RuntimeContext.printer", lambda self: BoomPrinter())
    assert core.read_loaded_ams_material(_args()) is None

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def test_preflight_problem_none_when_ready(tmp_path):
    _install_ready_settings(tmp_path)
    assert core.preflight_problem(_args()) is None

def test_preflight_problem_reports_unconfigured_printer(tmp_path):
    _install_ready_settings(tmp_path, printer_ip="0.0.0.0")
    problem = core.preflight_problem(_args())
    assert problem is not None and "plate setup" in problem
    # --sim gets past the unconfigured-IP gate (the slicer checks still apply).
    assert core.preflight_problem(_args(sim=True)) is None

def test_preflight_problem_reports_missing_profiles(tmp_path):
    settings = _install_ready_settings(tmp_path)
    import shutil

    shutil.rmtree(os.path.join(settings.profiles_dir, "process"))
    problem = core.preflight_problem(_args())
    assert problem is not None
    assert "profiles" in problem.lower()

# ---------------------------------------------------------------------------
# run_prepare_pipeline
# ---------------------------------------------------------------------------

def test_run_prepare_pipeline_downloads_then_slices(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    download = Recorder(return_value=stl)
    slicer = Recorder(return_value=sliced)
    steps = core.GoSteps(download=download, slice=slicer)

    state = core.WizardState(source="https://example.com/cube.stl", material="PETG", quality="fine")
    workdir = str(tmp_path / "work")
    os.makedirs(workdir)
    model_path = core.run_prepare_pipeline(steps, state, workdir)

    assert model_path == stl
    assert download.calls[0].url == "https://example.com/cube.stl"
    assert download.calls[0].output == workdir
    # the "fine" preset maps onto OrcaSlicer's "high" quality profile
    assert slicer.calls[0].quality == "high"
    assert state.printable_path == sliced
    assert state.sliced is True

def test_run_prepare_pipeline_local_presliced_skips_slicer(tmp_path):
    _install_ready_settings(tmp_path)
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    slicer = Recorder()
    steps = core.GoSteps(download=Recorder(), slice=slicer)

    state = core.WizardState(source=presliced)
    model_path = core.run_prepare_pipeline(steps, state, str(tmp_path))

    assert model_path == presliced
    assert slicer.calls == []
    assert state.sliced is False
    assert state.printable_path == presliced

def test_run_prepare_pipeline_extracts_local_zip_then_slices(tmp_path):
    _install_ready_settings(tmp_path)
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("model.stl", "solid cube\nendsolid cube\n")
    sliced = _sliced_3mf(tmp_path)
    slicer = Recorder(return_value=sliced)
    steps = core.GoSteps(download=Recorder(), slice=slicer)

    workdir = str(tmp_path / "work")
    os.makedirs(workdir)
    state = core.WizardState(source=str(bundle))
    model_path = core.run_prepare_pipeline(steps, state, workdir)

    assert model_path.endswith("model.stl")
    assert state.sliced is True
    assert len(slicer.calls) == 1

def test_run_prepare_pipeline_aborts_when_download_returns_nothing(tmp_path):
    _install_ready_settings(tmp_path)
    steps = core.GoSteps(download=Recorder(return_value=None), slice=Recorder())
    state = core.WizardState(source="https://example.com/cube.stl")
    with pytest.raises(BambuError) as ei:
        core.run_prepare_pipeline(steps, state, str(tmp_path))
    assert ei.value.exit_code == 3

def test_run_prepare_pipeline_annotates_slicer_failure_with_next_command(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    steps = core.GoSteps(
        download=Recorder(return_value=stl),
        slice=Recorder(raises=BambuError("slicer exploded", exit_code=4)),
    )
    state = core.WizardState(source="https://example.com/cube.stl")
    with pytest.raises(BambuError) as ei:
        core.run_prepare_pipeline(steps, state, str(tmp_path))
    assert ei.value.next_command == ["slice", stl, "-v"]

# ---------------------------------------------------------------------------
# preview_rows
# ---------------------------------------------------------------------------

def test_preview_rows_for_sliced_model(tmp_path):
    _install_ready_settings(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    state = core.WizardState(material="PETG", quality="fine", supports=True, printable_path=sliced, sliced=True)
    rows = dict(core.preview_rows(state, str(tmp_path / "cube.stl")))
    assert rows["Model"] == "cube.stl"
    assert "0.4mm nozzle" in rows["Printer"]
    assert rows["Material"].startswith("PETG")
    assert "0.12mm" in rows["Material"]
    assert "Supports: yes" in rows["Material"]
    assert "1h" in rows["Estimate"] or "min" in rows["Estimate"]

def test_preview_rows_presliced_flags_material_not_applied(tmp_path):
    _install_ready_settings(tmp_path)
    presliced = _sliced_3mf(tmp_path, name="ready.gcode.3mf")
    state = core.WizardState(material="PETG", printable_path=presliced, sliced=False)
    rows = dict(core.preview_rows(state, presliced))
    assert rows["Material"] == core.PRESLICED_MATERIAL_LINE
    assert "PETG" not in rows["Material"]

def test_preview_rows_gcode_has_no_estimate(tmp_path):
    _install_ready_settings(tmp_path)
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n", encoding="utf-8")
    state = core.WizardState(printable_path=str(gcode), sliced=False)
    rows = dict(core.preview_rows(state, str(gcode)))
    assert rows["Estimate"] == "estimate unavailable (pre-sliced file)"

def test_preview_rows_unreadable_estimate(tmp_path):
    _install_ready_settings(tmp_path)
    empty = tmp_path / "empty.gcode.3mf"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("noise.txt", "nothing useful")
    state = core.WizardState(printable_path=str(empty), sliced=True)
    rows = dict(core.preview_rows(state, str(empty)))
    assert "Couldn't read a time estimate" in rows["Estimate"]

# ---------------------------------------------------------------------------
# build_job_namespace — the only place confirm=True can be set
# ---------------------------------------------------------------------------

def test_build_job_namespace_carries_confirm_and_flags(tmp_path):
    _install_ready_settings(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    state = core.WizardState(printable_path=sliced)
    ns = core.build_job_namespace(state, _args(sim=True, verbose=True), confirm=True)
    assert ns.source == sliced
    assert ns.confirm is True
    assert ns.sim is True
    assert ns.verbose is True

def test_build_job_namespace_sets_ams_only_when_detected_material_kept(tmp_path):
    _install_ready_settings(tmp_path)
    sliced = _sliced_3mf(tmp_path)

    kept = core.WizardState(printable_path=sliced, material="PLA", detected_ams_material="PLA", detected_ams_slot=2)
    ns = core.build_job_namespace(kept, _args(), confirm=False)
    assert ns.use_ams is True
    assert ns.ams_mapping == "2"

    # Different material chosen -> conservative external-spool default.
    changed = core.WizardState(printable_path=sliced, material="PETG", detected_ams_material="PLA", detected_ams_slot=2)
    ns = core.build_job_namespace(changed, _args(), confirm=False)
    assert not getattr(ns, "use_ams", False)

    # Detected material but no firm active slot -> no AMS feed either.
    slotless = core.WizardState(printable_path=sliced, material="PLA", detected_ams_material="PLA")
    ns = core.build_job_namespace(slotless, _args(), confirm=False)
    assert not getattr(ns, "use_ams", False)

# ---------------------------------------------------------------------------
# workdir hygiene
# ---------------------------------------------------------------------------

def test_under_workdir():
    assert core.under_workdir(os.path.join("/tmp", "wd", "a.stl"), os.path.join("/tmp", "wd"))
    assert core.under_workdir(os.path.join("/tmp", "wd"), os.path.join("/tmp", "wd"))
    assert not core.under_workdir(os.path.join("/tmp", "other", "a.stl"), os.path.join("/tmp", "wd"))
    assert not core.under_workdir("/tmp/a.stl", None)

def test_make_workdir_and_cleanup_workdir(tmp_path):
    workdir = core.make_workdir(prefix="bambu-core-test-")
    assert os.path.isdir(workdir)
    state = core.WizardState(workdir=workdir)
    core.cleanup_workdir(state)
    assert not os.path.exists(workdir)
    # Idempotent: a second cleanup is a no-op, not an error.
    core.cleanup_workdir(state)

def test_cleanup_workdir_keeps_a_preserved_file(tmp_path, monkeypatch):
    workdir = core.make_workdir(prefix="bambu-core-test-")
    kept = os.path.join(workdir, "keep.3mf")
    with open(kept, "w", encoding="utf-8") as fh:
        fh.write("x")
    state = core.WizardState(workdir=workdir, kept_path=kept)
    core.cleanup_workdir(state)
    assert os.path.exists(kept)
    core.cleanup_workdir(core.WizardState(workdir=workdir))
    assert not os.path.exists(workdir)

def test_cleanup_workdir_honors_keep_env(monkeypatch):
    workdir = core.make_workdir(prefix="bambu-core-test-")
    monkeypatch.setenv("BAMBU_KEEP_WORKDIR", "1")
    core.cleanup_workdir(core.WizardState(workdir=workdir))
    assert os.path.isdir(workdir)
    monkeypatch.delenv("BAMBU_KEEP_WORKDIR")
    core.cleanup_workdir(core.WizardState(workdir=workdir))

def test_preserve_printable_leaves_user_file_in_place(tmp_path):
    presliced = _sliced_3mf(tmp_path, name="mine.gcode.3mf")
    workdir = str(tmp_path / "work")
    os.makedirs(workdir)
    state = core.WizardState(printable_path=presliced, workdir=workdir)
    assert core.preserve_printable(state) == presliced
    assert os.path.exists(presliced)

def test_preserve_printable_moves_workdir_file_into_cwd(tmp_path):
    workdir = str(tmp_path / "work")
    os.makedirs(workdir)
    printable = _sliced_3mf(tmp_path / "work", name="cube.gcode.3mf")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state = core.WizardState(printable_path=printable, workdir=workdir)
    old = os.getcwd()
    os.chdir(cwd)
    try:
        kept = core.preserve_printable(state)
    finally:
        os.chdir(old)
    assert kept is not None
    assert os.path.dirname(os.path.abspath(kept)) == str(cwd)
    assert not os.path.exists(printable)

def test_preserve_printable_returns_none_without_a_file(tmp_path):
    assert core.preserve_printable(core.WizardState()) is None
    assert core.preserve_printable(core.WizardState(printable_path=str(tmp_path / "gone.3mf"))) is None

# ---------------------------------------------------------------------------
# GoSteps defaults resolve to the real commands
# ---------------------------------------------------------------------------

def test_gosteps_defaults_resolve_to_real_collaborators():
    from bambu_cli import commands

    steps = core.GoSteps()
    assert steps.get_download() is commands.cmd_download
    assert steps.get_slice() is commands.cmd_slice
    assert steps.get_job() is commands.cmd_job
    assert steps.get_setup() is commands.cmd_setup
    assert steps.get_ams_material() is core.read_loaded_ams_material

def test_gosteps_injection_wins():
    sentinel = object()
    steps = core.GoSteps(download=sentinel, slice=sentinel, job=sentinel, setup=sentinel, ams_material=sentinel)
    assert steps.get_download() is sentinel
    assert steps.get_ams_material() is sentinel

# ---------------------------------------------------------------------------
# SliceOverrides — the wizard must be untouched by their existence
# ---------------------------------------------------------------------------

def test_wizard_state_starts_with_no_overrides():
    assert core.WizardState().overrides.is_empty()

def test_run_prepare_pipeline_passes_the_untouched_namespace_when_no_overrides(tmp_path):
    """`plate go` byte-identity: the slice namespace is what it always was."""
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    seen = {}

    def capture(ns=None, **kwargs):
        seen["vars"] = dict(vars(ns))
        return sliced

    steps = core.GoSteps(download=Recorder(return_value=stl), slice=capture)
    state = core.WizardState(source="https://example.com/cube.stl", material="PLA", quality="standard")
    core.run_prepare_pipeline(steps, state, str(tmp_path))

    from bambu_cli.interactive.presets import preset_to_job_args
    from bambu_cli.job.predict import _slice_args_for_job

    expected = _slice_args_for_job(stl, preset_to_job_args("PLA", "standard", False, state.source), str(tmp_path))
    assert seen["vars"] == vars(expected)

def test_run_prepare_pipeline_applies_overrides_when_present(tmp_path):
    _install_ready_settings(tmp_path)
    stl = _make_stl(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    slicer = Recorder(return_value=sliced)
    steps = core.GoSteps(download=Recorder(return_value=stl), slice=slicer)
    state = core.WizardState(source="https://example.com/cube.stl")
    state.overrides = core.SliceOverrides(fields={"walls": 5}, filament={"filament_flow_ratio": "0.9"})
    core.run_prepare_pipeline(steps, state, str(tmp_path))
    assert slicer.calls[0].walls == 5
    assert slicer.calls[0].set_filament == ["filament_flow_ratio=0.9"]

def test_preview_rows_gain_an_overrides_line_only_when_set(tmp_path):
    _install_ready_settings(tmp_path)
    sliced = _sliced_3mf(tmp_path)
    plain = core.WizardState(printable_path=sliced, sliced=True)
    assert [label for label, _ in core.preview_rows(plain, sliced)] == ["Model", "Printer", "Material", "Estimate"]

    tuned = core.WizardState(printable_path=sliced, sliced=True)
    tuned.overrides = core.SliceOverrides(fields={"walls": 5})
    rows = dict(core.preview_rows(tuned, sliced))
    assert rows["Overrides"] == "1 set (walls)"

    # A pre-sliced file was never sliced by us, so overrides are not claimed.
    presliced = core.WizardState(printable_path=sliced, sliced=False)
    presliced.overrides = core.SliceOverrides(fields={"walls": 5})
    assert "Overrides" not in dict(core.preview_rows(presliced, sliced))
