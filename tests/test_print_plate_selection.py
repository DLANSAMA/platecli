"""The print command names the plate that is actually sliced in the 3MF.

Before the 2026-09-22 fix the payload always said ``Metadata/plate_1.gcode``.
A pre-sliced project whose only sliced plate is plate 2 (common for
multi-plate Printables/Bambu Studio downloads) failed on the printer, and a 3MF
with no sliced plate at all was uploaded and sent to print anyway.
"""

from __future__ import annotations

import argparse
import json
import zipfile

import pytest

from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import BambuError


def _three_mf(path, plates, predictions=None):
    predictions = predictions or {}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
        for n in plates:
            zf.writestr(f"Metadata/plate_{n}.gcode", f"; plate {n}\nG28\n")
        if predictions:
            body = "".join(
                f'<plate><metadata key="index" value="{n}"/><metadata key="prediction" value="{secs}"/></plate>'
                for n, secs in predictions.items()
            )
            zf.writestr("Metadata/slice_info.config", f'<?xml version="1.0"?><config>{body}</config>')
    return str(path)


# --- payload / parser ---------------------------------------------------------


def test_payload_names_the_requested_plate():
    from bambu_cli.job.payload import generate_print_payload

    assert json.loads(generate_print_payload("a.3mf"))["print"]["param"] == "Metadata/plate_1.gcode"
    assert json.loads(generate_print_payload("a.3mf", plate=3))["print"]["param"] == "Metadata/plate_3.gcode"


@pytest.mark.parametrize("argv", [["print", "a.3mf"], ["job", "a.3mf"], ["send", "a.3mf"]])
def test_plate_flag_parses(argv):
    from bambu_cli.cliparse import build_parser

    parser = build_parser()
    assert parser.parse_args(argv).plate is None
    assert parser.parse_args([*argv, "--plate", "2"]).plate == 2


@pytest.mark.parametrize("value", ["0", "-1", "two"])
def test_plate_flag_must_be_positive(value):
    from bambu_cli.cliparse import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["print", "a.3mf", "--plate", value])


def test_print_command_sends_the_plate(monkeypatch):
    from bambu_cli.commands import print_cmd

    sent = {}

    def fake_execute(printer, payload, basename, dry_run=False):
        sent["payload"] = json.loads(payload)

    monkeypatch.setattr("bambu_cli.protocols.mqtt.execute_print_command", fake_execute)
    args = argparse.Namespace(file="a.3mf", confirm=True, dry_run=False, json=False, plate=2)
    print_cmd.cmd_print(args, ctx=argparse.Namespace(printer=lambda: object()))
    assert sent["payload"]["print"]["param"] == "Metadata/plate_2.gcode"


# --- 3MF inspection -------------------------------------------------------------


def test_sliced_plates_lists_plate_numbers(tmp_path):
    from bambu_cli.slicer.estimate import sliced_plates

    assert sliced_plates(_three_mf(tmp_path / "a.3mf", [5, 2])) == [2, 5]
    assert sliced_plates(_three_mf(tmp_path / "b.3mf", [])) == []
    assert sliced_plates(str(tmp_path / "missing.3mf")) == []


def test_estimate_is_for_the_first_or_requested_plate(tmp_path):
    from bambu_cli.slicer.estimate import read_3mf_estimate

    path = _three_mf(tmp_path / "a.3mf", [1, 2], predictions={1: 600, 2: 3600})
    assert read_3mf_estimate(path).seconds == 600
    assert read_3mf_estimate(path, plate=2).seconds == 3600


# --- job ----------------------------------------------------------------------


def _job(source, *extra):
    from bambu_cli.cliparse import build_parser
    from bambu_cli.job.orchestrate import _run_job
    from bambu_cli.job.steps import JobSteps
    from tests.bambu_test_base import settings_ctx

    calls = {}

    def upload(ns):
        calls["upload"] = ns
        return "model.3mf"

    def print_(ns):
        calls["print"] = ns

    args = build_parser().parse_args(["--json", "job", source, "--confirm", *extra])
    from bambu_cli.context import RuntimeContext

    with settings_ctx():
        _run_job(RuntimeContext.for_request(args), args, JobSteps(upload=upload, print_=print_))
    return calls


def test_job_prints_the_only_sliced_plate(tmp_path, capsys):
    calls = _job(_three_mf(tmp_path / "model.3mf", [2]))
    assert calls["print"].plate == 2
    assert json.loads(capsys.readouterr().out)["plate"] == 2


def test_job_prefers_plate_one_and_honours_plate_flag(tmp_path, capsys):
    source = _three_mf(tmp_path / "model.3mf", [1, 3])
    assert _job(source)["print"].plate == 1
    capsys.readouterr()
    assert _job(source, "--plate", "3")["print"].plate == 3


def test_job_refuses_a_plate_that_is_not_sliced(tmp_path, capsys):
    with pytest.raises(BambuError) as caught:
        _job(_three_mf(tmp_path / "model.3mf", [1, 2]), "--plate", "4")
    assert caught.value.exit_code == EXIT_COMMAND_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed_step"] == "validate" and "1, 2" in payload["error"]


def test_job_refuses_an_unsliced_3mf_before_uploading(tmp_path, capsys):
    with pytest.raises(BambuError) as caught:
        _job(_three_mf(tmp_path / "model.3mf", []))
    assert caught.value.exit_code == EXIT_FILE_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert "not sliced" in payload["error"] and payload["uploaded"] is False


def test_job_rejects_plate_for_a_model_it_slices(tmp_path, capsys):
    stl = tmp_path / "cube.stl"
    stl.write_text("solid c\nendsolid c\n", encoding="utf-8")
    with pytest.raises(BambuError) as caught:
        _job(str(stl), "--plate", "2")
    assert caught.value.exit_code == EXIT_COMMAND_ERROR
    assert "--plate" in json.loads(capsys.readouterr().out)["error"]


def test_upload_only_next_command_carries_a_non_default_plate(tmp_path, capsys):
    from bambu_cli.cliparse import build_parser
    from bambu_cli.context import RuntimeContext
    from bambu_cli.job.orchestrate import _run_job
    from bambu_cli.job.steps import JobSteps
    from tests.bambu_test_base import settings_ctx

    source = _three_mf(tmp_path / "model.3mf", [2])
    args = build_parser().parse_args(["--json", "job", source, "--upload-only"])
    with settings_ctx():
        _run_job(RuntimeContext.for_request(args), args, JobSteps(upload=lambda ns: "model.3mf"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_command"] == ["print", "model.3mf", "--confirm", "--json", "--plate", "2"]
