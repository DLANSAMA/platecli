"""Hermetic end-to-end slice tests against the fake OrcaSlicer (roadmap C.4).

These tests point ``settings.orca_slicer`` at a real launcher for
``tests/fakes/orca_stub/orca_stub.py`` and give ``cmd_slice`` a real
``profiles_dir`` and a real STL under ``tmp_path``. Nothing is mocked at the
``subprocess`` / ``os.path.exists`` layer, so the whole pipeline runs for real:

    _build_orcaslicer_cmd -> _run_orcaslicer (Popen + stdout pump)
        -> _finalize_slice (returncode + 3MF validation + JSON/error emit)

The point is to kill mutants in ``bambu_cli/slicer/output.py`` (the benign-GL
branch, empty/corrupt/missing output handling, error-line extraction) that the
mock-heavy unit tests could not reach, and to actually execute the real
``_run_orcaslicer`` process loop that unit tests skip via ``# pragma: no cover``.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from bambu_cli.errors import BambuError
from bambu_cli.slicer import cmd_slice
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl


def _last_json_object(text: str) -> dict:
    """Return the last top-level JSON object in *text*.

    ``emit_json`` / ``emit_json_error`` pretty-print with ``indent=2``, so the
    envelope spans multiple lines; scan for the last balanced ``{...}`` block.
    """
    decoder = json.JSONDecoder()
    last = None
    idx = 0
    while idx < len(text):
        brace = text.find("{", idx)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        last = obj
        idx = end
    assert last is not None, f"no JSON object found in output: {text!r}"
    return last


def _slice_args(model_path: str, outdir: str, **overrides) -> argparse.Namespace:
    """A minimal real Namespace for cmd_slice (no MagicMock truthy-attr traps)."""
    ns = argparse.Namespace(
        file=model_path,
        output=outdir,
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
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def orca_env(tmp_path, monkeypatch):
    """Real profiles + STL + fake-slicer launcher wired via settings_ctx.

    Returns a callable ``run(scenario, **stub_env)`` that installs the launcher
    as ``settings.orca_slicer`` and invokes ``cmd_slice`` with the given stub
    scenario, plus the resolved output path and model path.
    """
    # A DISPLAY makes _build_orcaslicer_cmd skip the xvfb-run prefix on Linux so
    # the launcher runs directly. Harmless on macOS/Windows.
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("ORCA_STUB_SCENARIO", raising=False)

    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()
    # cmd_slice derives outfile from the source basename: model.stl -> model_sliced.3mf
    outpath = str(outdir / "model_sliced.3mf")

    def run(scenario: str, args=None, **stub_env):
        monkeypatch.setenv("ORCA_STUB_SCENARIO", scenario)
        for k, v in stub_env.items():
            monkeypatch.setenv(k, str(v))
        ns = args if args is not None else _slice_args(model, str(outdir))
        with settings_ctx(orca_slicer=launcher, profiles_dir=profiles):
            return cmd_slice(ns)

    run.model = model
    run.outdir = str(outdir)
    run.outpath = outpath
    run.profiles = profiles
    run.launcher = launcher
    return run


# --- Success paths -----------------------------------------------------------


def test_success_writes_valid_3mf(orca_env):
    result = orca_env("success")
    assert result == orca_env.outpath
    assert os.path.exists(result)
    import zipfile

    assert zipfile.is_zipfile(result)
    names = set(zipfile.ZipFile(result).namelist())
    assert "[Content_Types].xml" in names
    assert "Metadata/plate_1.gcode" in names


def test_success_emits_json_envelope(orca_env, capsys):
    args = _slice_args(orca_env.model, orca_env.outdir, json=True)
    result = orca_env("success", args=args)
    assert result == orca_env.outpath
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["status"] == "sliced"
    assert payload["command"] == "slice"
    assert payload["path"] == orca_env.outpath
    assert payload["filename"] == "model_sliced.3mf"
    assert payload["bytes"] > 0
    assert payload["step_converted"] is False


def test_progress_scenario_still_succeeds(orca_env):
    # Drives the stdout progress-line parsing in _run_orcaslicer with a
    # human-facing (non-json) run so the Rich/progress branch executes.
    result = orca_env("progress")
    assert os.path.exists(result)


def test_garbage_stdout_does_not_break_pump(orca_env):
    # Null bytes / stray \r / no-newline tail must not crash the stdout handler.
    result = orca_env("garbage_stdout")
    assert os.path.exists(result)


# --- The benign non-zero (headless GL) branch --------------------------------


def test_benign_gl_nonzero_exit_is_treated_as_success(orca_env):
    # returncode != 0 + valid .3mf + GL/thumbnail noise + no real error text
    # => _finalize_slice continues (the hardest-to-mock branch).
    result = orca_env("benign_gl")
    assert result == orca_env.outpath
    assert os.path.exists(result)


def test_real_error_with_gl_noise_is_not_benign(orca_env):
    # GL noise present BUT a real "slicing error" line => must still fail even
    # though a valid .3mf was written. Guards the `not _real_err` condition.
    with pytest.raises(BambuError):
        orca_env("fail_real_gl")


# --- Failure / bad-output paths ----------------------------------------------


def test_nonzero_exit_failure_aborts(orca_env):
    with pytest.raises(BambuError):
        orca_env("fail")


def test_nonzero_exit_failure_json_envelope(orca_env, capsys):
    args = _slice_args(orca_env.model, orca_env.outdir, json=True)
    with pytest.raises(BambuError):
        orca_env("fail", args=args)
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["command"] == "slice"
    assert payload["failed_step"] == "slicer"
    assert payload["returncode"] == 1


def test_empty_output_file_aborts_and_is_removed(orca_env):
    with pytest.raises(BambuError):
        orca_env("empty_output")
    # _finalize_slice removes the partial/empty file.
    assert not os.path.exists(orca_env.outpath)


def test_corrupt_output_file_aborts_and_is_removed(orca_env):
    with pytest.raises(BambuError):
        orca_env("corrupt_output")
    assert not os.path.exists(orca_env.outpath)


def test_missing_output_file_aborts(orca_env):
    with pytest.raises(BambuError):
        orca_env("missing_output")
    assert not os.path.exists(orca_env.outpath)


# --- Timeout -----------------------------------------------------------------


def test_hang_hits_slice_timeout(orca_env, monkeypatch):
    # get_slicer_timeout reads args.slicer_timeout; pin it tiny so the stub's
    # sleep trips the real subprocess.TimeoutExpired -> EXIT_TIMEOUT path.
    args = _slice_args(orca_env.model, orca_env.outdir, slicer_timeout=1)
    with pytest.raises(BambuError):
        orca_env("hang", args=args, ORCA_STUB_SLEEP=30)


# --- The DI/launcher seam actually reached the fake binary -------------------


def test_stub_binary_was_actually_invoked(orca_env, tmp_path):
    marker = tmp_path / "invoked.log"
    result = orca_env("success", ORCA_STUB_MARKER=str(marker))
    assert os.path.exists(result)
    assert marker.exists()
    assert marker.read_text().strip() == "success"
