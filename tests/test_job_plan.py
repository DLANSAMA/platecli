"""Plan fingerprint: ``job``/``send --dry-run`` reports it, ``--expect-plan`` enforces it.

Ground rules as in tests/test_job.py: no printer, no network, injected steps.
"""

import json

import pytest

from bambu_cli.cli import build_parser
from bambu_cli.constants import EXIT_COMMAND_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.errors import BambuError
from bambu_cli.job import JobSteps, _run_job
from bambu_cli.job.plan import PLAN_VERSION, plan_fingerprint
from tests.json_contract_base import sliced_3mf_bytes



def _args(argv):
    return build_parser().parse_args(argv)


def _fingerprint(argv):
    args = _args(argv)
    return plan_fingerprint(RuntimeContext(), args, args.source)


@pytest.fixture
def ready(tmp_path):
    path = tmp_path / "part.3mf"
    path.write_bytes(sliced_3mf_bytes())
    return path


def _dry_run(capsys, argv):
    _run_job(RuntimeContext(), _args(["job", *argv, "--dry-run", "--json"]), JobSteps())
    return json.loads(capsys.readouterr().out)


def test_dry_run_reports_a_stable_64_hex_fingerprint(ready, capsys):
    first = _dry_run(capsys, [str(ready)])
    second = _dry_run(capsys, [str(ready)])
    assert len(first["plan_fingerprint"]) == 64
    int(first["plan_fingerprint"], 16)
    assert first["plan_fingerprint"] == second["plan_fingerprint"]


def test_send_and_job_share_one_plan(ready):
    assert _fingerprint(["job", str(ready)]) == _fingerprint(["send", str(ready)])


@pytest.mark.parametrize(
    "extra",
    [
        ["--use-ams", "--ams-mapping", "1"],
        ["--timelapse"],
        ["--skip-bed-leveling"],
        ["--skip-flow-cali"],
        ["--plate", "2"],
        ["--upload-only"],
        ["--filament", "PETG"],
        ["--nozzle-temp", "230"],
        ["--bed-temp", "70"],
        ["--infill", "30"],
        ["--copies", "2"],
        ["--quality", "high"],
    ],
)
def test_every_option_that_changes_the_result_changes_the_plan(ready, extra):
    assert _fingerprint(["job", str(ready)]) != _fingerprint(["job", str(ready), *extra])


@pytest.mark.parametrize("extra", [["--confirm"], ["--dry-run"], ["--json"], ["--threads", "2"]])
def test_flags_that_do_not_change_the_result_leave_the_plan_alone(ready, extra):
    assert _fingerprint(["job", str(ready)]) == _fingerprint(["job", str(ready), *extra])


def test_editing_the_local_file_changes_the_plan(ready):
    before = _fingerprint(["job", str(ready)])
    ready.write_bytes(sliced_3mf_bytes() + b"\0")
    assert _fingerprint(["job", str(ready)]) != before


def test_the_printer_is_part_of_the_plan(ready):
    args = _args(["job", str(ready)])
    other = RuntimeContext()
    other = RuntimeContext(settings=other.settings.__class__(**{**other.settings.__dict__, "serial": "OTHER"}))
    assert plan_fingerprint(RuntimeContext(), args, args.source) != plan_fingerprint(other, args, args.source)


def test_url_plan_is_bound_to_the_url_not_the_bytes():
    url = "https://example.com/a.stl"
    assert _fingerprint(["job", url]) != _fingerprint(["job", "https://example.com/b.stl"])


def test_plan_version_is_named():
    assert PLAN_VERSION == "plate-plan/1"


def test_matching_expect_plan_runs(ready, capsys):
    fp = _dry_run(capsys, [str(ready)])["plan_fingerprint"]
    payload = _dry_run(capsys, [str(ready), "--expect-plan", fp.upper()])
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["plan_fingerprint"] == fp


def test_a_changed_plan_is_refused_before_any_step_runs(ready, capsys):
    fp = _dry_run(capsys, [str(ready)])["plan_fingerprint"]
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("a step ran after the plan was refused")

    steps = JobSteps(download=boom, slice=boom, upload=boom, print_=boom)
    args = _args(["job", str(ready), "--use-ams", "--ams-mapping", "0", "--confirm", "--json", "--expect-plan", fp])
    with pytest.raises(BambuError) as caught:
        _run_job(RuntimeContext(), args, steps)
    assert caught.value.exit_code == EXIT_COMMAND_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["failed_step"] == "validate"
    assert payload["exit_code"] == EXIT_COMMAND_ERROR
    assert payload["plan_fingerprint"] != fp
    assert "plan changed" in payload["error"]
    assert calls == []


def test_a_wrong_fingerprint_text_is_refused_too(ready):
    args = _args(["job", str(ready), "--json", "--expect-plan", "nope"])
    with pytest.raises(BambuError):
        _run_job(RuntimeContext(), args, JobSteps())
