"""Tests for bambu_cli/job/: the job/send download->slice->upload->print
orchestrator. Covers docs/test-backlog.md P1.

Ground rules (docs/test-backlog.md): never touch a real printer or the
network; inject fake step callables via JobSteps instead of monkeypatching
bambu.cmd_* where injection suffices; assert full JSON payload shapes, not
just exit codes.
"""

import contextlib
import json
import logging
import os
import sys
import zipfile
from unittest.mock import MagicMock

import pytest


@contextlib.contextmanager
def _capture_bambu_warnings():
    """Collect WARNING+ records emitted on the 'bambu' logger."""
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    log = logging.getLogger("bambu")
    log.addHandler(handler)
    try:
        yield records
    finally:
        log.removeHandler(handler)


_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli import job  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.cli import build_parser  # noqa: E402
from bambu_cli.paths import display_path as _display_path  # noqa: E402
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR  # noqa: E402
from bambu_cli.context import RuntimeContext  # noqa: E402
from bambu_cli.job import JobSteps, _run_job  # noqa: E402
from bambu_cli.errors import BambuError


def _parse(argv):
    return build_parser().parse_args(argv)


def _ctx():
    return RuntimeContext()


def _read_json(capsys):
    out = capsys.readouterr().out
    return json.loads(out)


def fake_download(path):
    def _run(args):
        return path

    return _run


def fake_slice(path):
    def _run(args):
        return path

    return _run


def fake_upload(remote_name):
    def _run(args):
        return remote_name

    return _run


def fake_print():
    def _run(args):
        return None

    return _run


def failing_step(command, exit_code, error, **extra):
    """Build a fake step that mimics a real cmd_* failure: records the
    legacy last-error payload, then raises BambuError like domain handlers do.
    """
    from bambu_cli.errors import abort

    def _run(args):
        utils.emit_json_error(args, command, exit_code, error, **extra)
        abort(error, exit_code=exit_code)

    return _run


@pytest.fixture(autouse=True)
def _reset_last_error():
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None
    yield
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None


# ---------------------------------------------------------------------------
# Delegated-step failure payloads
# ---------------------------------------------------------------------------


def test_download_failure_detail_flows_through(tmp_path, capsys):
    url = "https://example.com/model.stl"
    args = _parse(["job", url, "--json"])
    steps = JobSteps(download=failing_step("download", 2, "Could not connect", failed_step="http", url=url))
    ctx = _ctx()
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(ctx, args, steps)
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == 2
    payload = _read_json(capsys)
    assert payload["status"] == "error"
    assert payload["failed_step"] == "download"
    assert payload["exit_code"] == 2
    assert payload["download_error"]["command"] == "download"
    assert payload["download_error"]["failed_step"] == "http"
    assert payload["download_error"]["error"] == "Could not connect"
    # Dual-write onto ctx.last_error.
    assert ctx.last_error["command"] == "download"


def test_slice_failure_detail_flows_through(tmp_path):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    args = _parse(["job", str(stl), "--json"])
    steps = JobSteps(slice=failing_step("slice", 3, "Slicer crashed", failed_step="orca"))
    ctx = _ctx()
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        import io

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            _run_job(ctx, args, steps)
        finally:
            captured = sys.stdout.getvalue()
            sys.stdout = old_stdout
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == 3
    payload = json.loads(captured)
    assert payload["failed_step"] == "slice"
    assert payload["slice_error"]["error"] == "Slicer crashed"
    assert payload["slice_error"]["failed_step"] == "orca"
    assert ctx.last_error["command"] == "slice"


def test_upload_failure_detail_flows_through(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--json"])
    steps = JobSteps(upload=failing_step("upload", 2, "FTPS connection refused", failed_step="ftps"))
    ctx = _ctx()
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(ctx, args, steps)
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == 2
    payload = _read_json(capsys)
    assert payload["failed_step"] == "upload"
    assert payload["upload_error"]["error"] == "FTPS connection refused"
    assert ctx.last_error["command"] == "upload"


def test_print_failure_detail_flows_through(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--json"])
    steps = JobSteps(
        upload=fake_upload("model.3mf"),
        print_=failing_step("print", 4, "Printer busy", failed_step="mqtt"),
    )
    ctx = _ctx()
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(ctx, args, steps)
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == 4
    payload = _read_json(capsys)
    assert payload["failed_step"] == "print"
    assert payload["print_error"]["error"] == "Printer busy"
    assert payload["next_command"] == ["status", "--json"]
    assert "recovery_hint" in payload
    assert ctx.last_error["command"] == "print"


# ---------------------------------------------------------------------------
# next_command payloads
# ---------------------------------------------------------------------------


def test_uploaded_only_next_command(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json"])
    steps = JobSteps(upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["status"] == "uploaded"
    assert payload["uploaded"] is True
    assert payload["printed"] is False
    assert payload["next_command"] == ["print", "model.3mf", "--confirm", "--json"]


def test_uploaded_not_printed_next_command(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--json"])
    steps = JobSteps(upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["status"] == "uploaded_not_printed"
    assert payload["next_command"] == ["print", "model.3mf", "--confirm", "--json"]


def test_uploaded_next_command_includes_ams_and_flags(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(
        [
            "job",
            str(ready),
            "--upload-only",
            "--json",
            "--use-ams",
            "--ams-mapping",
            "0,1",
            "--timelapse",
            "--skip-bed-leveling",
            "--skip-flow-cali",
        ]
    )
    steps = JobSteps(upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["next_command"] == [
        "print",
        "model.3mf",
        "--confirm",
        "--json",
        "--use-ams",
        "--ams-mapping",
        "0,1",
        "--timelapse",
        "--skip-bed-leveling",
        "--skip-flow-cali",
    ]


def _bad_ams_argv(source, *extra):
    """--ams-mapping without --use-ams: rejected by `print`, so job must reject it too."""
    return ["job", str(source), "--json", "--ams-mapping", "0,1", *extra]


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(("--dry-run",), id="dry-run"),
        pytest.param(("--upload-only",), id="upload-only"),
        pytest.param((), id="plain"),
    ],
)
def test_print_options_validated_without_confirm(tmp_path, capsys, extra):
    """Print options were only validated under --confirm, so the paths agents
    actually use as a pre-check reported success for flags that later fail.

    --dry-run is documented as no-side-effect validation, and --upload-only hands
    back a next_command carrying these same flags.
    """
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(_bad_ams_argv(ready, *extra))
    steps = JobSteps(upload=fake_upload("model.3mf"))
    with contextlib.suppress(SystemExit, BambuError):
        _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["status"] == "error"
    assert payload["failed_step"] == "validate"
    assert payload["error"] == "--ams-mapping requires --use-ams"
    # Nothing may have happened before the rejection.
    assert payload["uploaded"] is False
    assert payload["printed"] is False
    assert payload["next_command"] is None


def test_upload_only_next_command_is_itself_valid(tmp_path, capsys):
    """Whatever next_command we hand back must survive `print`'s own validation.

    The upload-only path previously emitted `--ams-mapping` with no `--use-ams`,
    which `print` rejects with exit 5 — a guaranteed failure after a real upload.
    """
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json", "--use-ams", "--ams-mapping", "0,1"])
    steps = JobSteps(upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    next_command = _read_json(capsys)["next_command"]

    # Re-parse the emitted command and run it through the same validator `print` uses.
    from bambu_cli.job import _parse_print_options

    replayed = _parse([c for c in next_command if c != "--json"] + ["--json"])
    _, error = _parse_print_options(replayed)
    assert error is None, f"job emitted a next_command that print rejects: {error}"


def test_valid_and_absent_print_options_still_pass(tmp_path, capsys):
    """The new validation must not reject the ordinary cases."""
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    for argv in (
        ["job", str(ready), "--dry-run", "--json"],
        ["job", str(ready), "--dry-run", "--json", "--use-ams", "--ams-mapping", "0"],
    ):
        _run_job(_ctx(), _parse(argv), JobSteps(upload=fake_upload("model.3mf")))
        assert _read_json(capsys)["status"] == "dry_run_local_skipped"


def test_printed_success(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--json"])
    steps = JobSteps(upload=fake_upload("model.3mf"), print_=fake_print())
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["status"] == "printed"
    assert payload["printed"] is True
    assert payload["uploaded"] is True


# ---------------------------------------------------------------------------
# Dry-run matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,would_slice,would_extract",
    [
        ("model.stl", True, False),
        ("model.step", True, False),
        ("model.obj", True, False),
        ("model.zip", False, True),
        ("model.3mf", False, False),
        # ".gcode.3mf" is this project's primary printer-ready format; the
        # last-suffix extension is ".3mf", so it must NOT be predicted as
        # sliceable — the real run treats it as print-ready and skips slicing.
        ("plate.gcode.3mf", False, False),
    ],
)
def test_dry_run_direct_url(filename, would_slice, would_extract, capsys):
    url = f"https://example.com/{filename}"
    args = _parse(["job", url, "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_url_skipped"
    assert payload["would_download"] is True
    assert payload["would_slice"] is would_slice
    assert payload["would_extract"] is would_extract
    assert payload["would_upload"] is True
    assert payload["would_print"] is False
    if not would_extract:
        assert payload["remote_name"] is not None


def test_dry_run_printables_model_url_predicts_slice(capsys):
    """Regression: a Printables model page dry-run must report would_slice=True.

    The predictor cannot resolve the page offline, but the real run downloads a
    model file (STL/STEP preferred) and slices it. The shared predicate now
    falls back to ".stl" — the same fallback the downloader lands — so the
    dry-run prediction matches the real run instead of defaulting to False.
    """
    url = "https://printables.com/model/12345-agent-smoke"
    args = _parse(["job", url, "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_url_skipped"
    assert payload["would_download"] is True
    assert payload["would_slice"] is True
    assert payload["would_extract"] is False
    assert payload["would_upload"] is True
    # Remote name stays unpredictable offline for a Printables page; would_slice
    # being True must not fabricate one.
    assert payload["remote_name"] is None


def test_predictor_and_doer_share_slice_predicate():
    """The dry-run URL prediction routes through the same predicate as the real run.

    For any URL, the predicted-download extension fed to _ext_would_slice must
    equal the decision the doer would make for that same extension. Guards
    against the two paths drifting apart again (the original bug: predictor said
    would_slice=False for sources the doer sliced).
    """
    from bambu_cli.download import _file_extension
    from bambu_cli.job import _ext_would_slice, _predicted_download_slice_extension

    cases = {
        "https://printables.com/model/12345-foo": True,
        "https://example.com/model.stl": True,
        "https://example.com/model.step": True,
        "https://example.com/model.obj": True,
        "https://example.com/model.3mf": False,
        "https://example.com/plate.gcode.3mf": False,
        "https://example.com/bundle.zip": False,
        "https://example.com/download?id=1": True,
    }
    for url, expected in cases.items():
        args = _parse(["job", url, "--dry-run", "--json"])
        predicted_ext = _predicted_download_slice_extension(url, args)
        # Predictor's decision.
        assert _ext_would_slice(predicted_ext) is expected, url
        # Doer applies the identical predicate to the file it actually handles;
        # simulate by feeding the predicted extension back through it.
        assert _ext_would_slice(_file_extension(f"x{predicted_ext}")) is expected, url


def test_dry_run_extensionless_url_predicts_slice(capsys):
    """An extension-less direct link predicts slicing, matching the doer's fallback.

    The real download resolves the filename via Content-Disposition/content and
    falls back to ".stl" when nothing determines it, then slices. The dry-run
    prediction must agree.
    """
    url = "https://example.com/download?id=1"
    args = _parse(["job", url, "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_url_skipped"
    assert payload["would_slice"] is True
    assert payload["would_extract"] is False


def test_dry_run_local_model_file(tmp_path, capsys):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    args = _parse(["job", str(stl), "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["would_slice"] is True
    assert payload["would_upload"] is True
    assert payload["would_download"] is False
    assert payload["remote_name"]


def test_dry_run_local_zip(tmp_path, capsys):
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"solid x")
    args = _parse(["job", str(zpath), "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["archive_entry"] == "model.stl"
    assert payload["would_slice"] is True
    assert payload["would_upload"] is True


def test_dry_run_local_printer_ready_file(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["would_upload"] is True
    assert payload["would_slice"] is False
    assert payload["printable_path"] == _display_path(str(ready))


def test_dry_run_local_gcode_3mf_is_print_ready_not_sliced(tmp_path, capsys):
    """A local .gcode.3mf is print-ready: dry-run must report would_slice=False."""
    ready = tmp_path / "plate.gcode.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["would_slice"] is False
    assert payload["would_upload"] is True
    assert payload["remote_name"] == "plate.gcode.3mf"


@pytest.mark.parametrize(
    "member,would_slice",
    [
        ("model.stl", True),
        ("model.3mf", False),
        # Last-suffix extension of a ".gcode.3mf" member is ".3mf": print-ready.
        ("plate.gcode.3mf", False),
    ],
)
def test_dry_run_local_zip_member_slice_matches_predicate(tmp_path, member, would_slice, capsys):
    """ZIP dry-run reports would_slice per the selected member's extension.

    Uses the same _ext_would_slice predicate as the real run, so a .3mf or
    .gcode.3mf member is reported print-ready while an .stl member is sliceable.
    """
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(member, b"content")
    args = _parse(["job", str(zpath), "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["archive_entry"] == member
    assert payload["would_slice"] is would_slice
    assert payload["would_upload"] is True


def test_dry_run_local_printer_ready_empty_file_fails(tmp_path):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"")
    args = _parse(["job", str(ready), "--dry-run", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_dry_run_would_create_output_dir(tmp_path, capsys):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    missing_out = tmp_path / "does_not_exist_yet"
    args = _parse(["job", str(stl), "--dry-run", "--json", "--output", str(missing_out)])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["would_create_output_dir"] is True
    assert not missing_out.exists()


# ---------------------------------------------------------------------------
# ZIP paths
# ---------------------------------------------------------------------------


def test_zip_bad_archive_fails(tmp_path):
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip")
    args = _parse(["job", str(bad_zip), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_zip_no_supported_member_fails(tmp_path):
    zpath = tmp_path / "empty.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("readme.txt", b"hello")
    args = _parse(["job", str(zpath), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_zip_oversized_member_fails_in_dry_run(tmp_path, capsys):
    zpath = tmp_path / "big.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"x" * (2 * 1024 * 1024))
    args = _parse(["job", str(zpath), "--dry-run", "--json", "--max-download-mb", "1"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_zip_unsafe_member_filename_fails(tmp_path, capsys):
    # The sanitized member name is short/safe on its own, but the predicted
    # sliced output name (stem + "_sliced.3mf") pushes it past
    # MAX_DOWNLOAD_FILENAME_LENGTH, so it must be rejected before extraction.
    zpath = tmp_path / "unsafe.zip"
    stem = "a" * 150
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{stem}.stl", b"solid x")
    args = _parse(["job", str(zpath), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "unsafe printer filename" in payload["error"].lower()
    assert payload["archive_entry"] is None


def test_zip_archive_entry_propagates_to_summary(tmp_path, capsys):
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("part.stl", b"solid x")
    out_dir = tmp_path / "out"
    args = _parse(["job", str(zpath), "--json", "--output", str(out_dir)])
    steps = JobSteps(
        slice=fake_slice(str(out_dir / "part.3mf")),
        upload=fake_upload("part.3mf"),
    )
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["archive_entry"] == "part.stl"
    assert payload["would_extract"] is True
    assert payload["extracted_path"] is not None
    assert payload["uploaded"] is True


# ---------------------------------------------------------------------------
# --output handling
# ---------------------------------------------------------------------------


def test_output_created_when_needed(tmp_path, capsys):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    out_dir = tmp_path / "fresh_out"
    args = _parse(["job", str(stl), "--json", "--output", str(out_dir)])
    steps = JobSteps(
        slice=fake_slice(str(out_dir / "model.3mf")),
        upload=fake_upload("model.3mf"),
    )
    _run_job(_ctx(), args, steps)
    assert out_dir.is_dir()
    payload = _read_json(capsys)
    assert payload["workdir"] == _display_path(str(out_dir))
    assert payload["uploaded"] is True


def test_output_ignored_for_printer_ready_local_file(tmp_path, capsys, caplog):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    out_dir = tmp_path / "unused_out"
    args = _parse(["job", str(ready), "--json", "--output", str(out_dir)])
    steps = JobSteps(upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    assert not out_dir.exists()
    payload = _read_json(capsys)
    assert payload["uploaded"] is True


def test_output_invalid_dash_prefixed_value_fails(tmp_path):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    args = _parse(["job", str(stl), "--json", "--output=--not-a-dir"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR


def test_temp_workdir_cleanup_when_no_output_given(tmp_path):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    args = _parse(["job", str(stl), "--json"])
    captured_workdir = {}

    def _slice(slice_args):
        captured_workdir["workdir"] = slice_args.output
        assert os.path.isdir(slice_args.output)
        return os.path.join(slice_args.output, "model.3mf")

    steps = JobSteps(slice=_slice, upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)
    assert captured_workdir["workdir"]
    assert not os.path.exists(captured_workdir["workdir"])


# ---------------------------------------------------------------------------
# Late-binding default JobSteps still resolve real command handlers.
# ---------------------------------------------------------------------------


def test_url_job_reuses_download_workdir_and_cleans_up(tmp_path):
    # Regression test: a URL-sourced job that slices used to allocate a
    # *second* temp dir for slicing (leaking the first, which held the
    # downloaded model). The download workdir must be reused for slicing and
    # removed when the job finishes.
    captured = {}

    def _download(download_args):
        captured["download_workdir"] = download_args.output
        model_path = os.path.join(download_args.output, "model.stl")
        with open(model_path, "wb") as fh:
            fh.write(b"solid x")
        return model_path

    def _slice(slice_args):
        captured["slice_workdir"] = slice_args.output
        return os.path.join(slice_args.output, "model.3mf")

    args = _parse(["job", "https://example.com/model.stl", "--json"])
    steps = JobSteps(download=_download, slice=_slice, upload=fake_upload("model.3mf"))
    _run_job(_ctx(), args, steps)

    assert captured["download_workdir"]
    assert captured["slice_workdir"] == captured["download_workdir"]
    assert not os.path.exists(captured["download_workdir"])


def test_default_job_steps_delegate_through_commands(tmp_path, capsys, monkeypatch):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json"])

    monkeypatch.setattr("bambu_cli.commands.cmd_upload", lambda ns: "model.3mf")
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "uploaded"
    assert payload["remote_name"] == "model.3mf"


def test_cmd_job_shim_builds_context_and_default_steps(tmp_path, capsys, monkeypatch):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json"])
    monkeypatch.setattr("bambu_cli.commands.cmd_upload", lambda ns: "model.3mf")
    job._cmd_job(args)
    payload = _read_json(capsys)
    assert payload["status"] == "uploaded"


# ---------------------------------------------------------------------------
# Source-validation failures (fail before any step runs, so no steps needed)
# ---------------------------------------------------------------------------


def test_non_http_url_scheme_rejected(capsys):
    args = _parse(["job", "ftp://example.com/model.stl", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "invalid url source" in payload["error"].lower()


def test_http_url_with_embedded_credentials_rejected_and_redacted(capsys):
    # Username-only + IP host: still trips the embedded-credentials rejection,
    # but avoids the repo privacy-smoke's email / user:pass@host literal patterns.
    args = _parse(["job", "http://user@127.0.0.1/model.stl", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    # Userinfo must be stripped from the machine-readable failure.
    assert "user@" not in json.dumps(payload)


def test_local_file_not_found_fails(tmp_path, capsys):
    args = _parse(["job", str(tmp_path / "missing.stl"), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "file not found" in payload["error"].lower()


def test_directory_source_fails(tmp_path, capsys):
    args = _parse(["job", str(tmp_path), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    assert _read_json(capsys)["failed_step"] == "validate"


def test_unsupported_local_file_type_fails(tmp_path, capsys):
    junk = tmp_path / "notes.txt"
    junk.write_text("hello", encoding="utf-8")
    args = _parse(["job", str(junk), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "unsupported source file type" in payload["error"].lower()


def test_unsafe_sliced_local_name_rejected_before_slicing(tmp_path, capsys):
    # A 150-char stem is fine on its own, but the predicted "<stem>_sliced.3mf"
    # exceeds MAX_DOWNLOAD_FILENAME_LENGTH and must be rejected before slicing.
    stl = tmp_path / (("a" * 150) + ".stl")
    stl.write_bytes(b"solid x")
    sliced_called = {"n": 0}

    def _slice(_a):
        sliced_called["n"] += 1
        return "x"

    args = _parse(["job", str(stl), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps(slice=_slice))
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    assert sliced_called["n"] == 0
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "unsafe printer filename" in payload["error"].lower()


def test_unsafe_printer_ready_local_name_rejected_before_upload(tmp_path, capsys):
    ready = tmp_path / (("a" * 200) + ".3mf")
    ready.write_bytes(b"x" * 10)
    upload_called = {"n": 0}

    def _upload(_a):
        upload_called["n"] += 1
        return "x"

    args = _parse(["job", str(ready), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps(upload=_upload))
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    assert upload_called["n"] == 0
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "unsafe name" in payload["error"].lower()


# ---------------------------------------------------------------------------
# Slice- and print-option validation
# ---------------------------------------------------------------------------


def test_invalid_slice_option_fails(tmp_path, capsys):
    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    args = _parse(["job", str(stl), "--copies", "0", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "--copies" in payload["error"]


def test_ams_mapping_without_use_ams_fails(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--ams-mapping", "0", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "--ams-mapping requires --use-ams" in payload["error"]


def test_ams_mapping_non_integer_fails(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--use-ams", "--ams-mapping", "a,b", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    assert "Invalid AMS mapping format" in _read_json(capsys)["error"]


def test_ams_mapping_negative_slot_fails(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--use-ams", "--ams-mapping=-1", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    assert "zero or positive" in _read_json(capsys)["error"].lower()


def test_ams_mapping_slot_too_high_fails(tmp_path, capsys):
    """AMS has 4 slots/unit; reject indexes beyond a realistic multi-AMS max."""
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--use-ams", "--ams-mapping", "100", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "100" in payload["error"] or "slot" in payload["error"].lower()


def test_use_ams_without_mapping_fails(tmp_path, capsys):
    """--use-ams with no mapping must not silently omit ams_mapping for firmware defaults."""
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--confirm", "--use-ams", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "--use-ams" in payload["error"] and "--ams-mapping" in payload["error"]


# ---------------------------------------------------------------------------
# --name is URL-only; warn and ignore for a local source
# ---------------------------------------------------------------------------


def test_name_ignored_for_local_file_warns(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--name", "renamed.3mf", "--upload-only", "--json"])
    steps = JobSteps(upload=fake_upload("model.3mf"))
    with _capture_bambu_warnings() as records:
        _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["status"] == "uploaded"
    assert any("--name is only used for URL downloads" in r.getMessage() for r in records), [
        r.getMessage() for r in records
    ]
    # The remote name comes from the file, not --name.
    assert payload["remote_name"] == "model.3mf"


# ---------------------------------------------------------------------------
# Successful URL download -> continue (the archive-detection branch)
# ---------------------------------------------------------------------------


def test_url_download_success_flows_into_slice_and_upload(tmp_path, capsys):
    downloaded = tmp_path / "model.stl"
    downloaded.write_bytes(b"solid x")
    out = tmp_path / "out"

    def _download(_a):
        return str(downloaded)

    args = _parse(["job", "https://example.com/model.stl", "--json", "--output", str(out)])
    steps = JobSteps(
        download=_download,
        slice=fake_slice(str(out / "model.3mf")),
        upload=fake_upload("model.3mf"),
    )
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["would_download"] is True
    assert payload["downloaded_path"] == _display_path(str(downloaded))
    assert payload["uploaded"] is True
    assert payload["remote_name"] == "model.3mf"


def test_url_download_reports_extracted_archive_member(tmp_path, capsys):
    # Simulate cmd_download having transparently extracted a ZIP: it records a
    # _LAST_DOWNLOAD_PAYLOAD with an archive_entry, which job/send surfaces.
    extracted = tmp_path / "part.stl"
    extracted.write_bytes(b"solid x")
    out = tmp_path / "out"

    def _download(_a):
        utils._LAST_DOWNLOAD_PAYLOAD = {
            "path": str(extracted),
            "archive_entry": "part.stl",
        }
        return str(extracted)

    args = _parse(["job", "https://example.com/bundle.zip", "--json", "--output", str(out)])
    steps = JobSteps(
        download=_download,
        slice=fake_slice(str(out / "part.3mf")),
        upload=fake_upload("part.3mf"),
    )
    _run_job(_ctx(), args, steps)
    payload = _read_json(capsys)
    assert payload["would_extract"] is True
    assert payload["archive_entry"] == "part.stl"
    assert payload["extracted_path"] == _display_path(str(extracted))
    assert payload["uploaded"] is True


def test_url_invalid_max_download_mb_fails(capsys):
    args = _parse(["job", "https://example.com/model.stl", "--json", "--max-download-mb", "0"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
    assert "--max-download-mb must be a positive integer" in payload["error"]


def test_run_job_defaults_steps_when_omitted(tmp_path, capsys, monkeypatch):
    # _run_job(ctx, args) with no steps arg builds default JobSteps() that
    # late-bind to bambu_cli.commands handlers.
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json"])
    monkeypatch.setattr("bambu_cli.commands.cmd_upload", lambda ns: "model.3mf")
    _run_job(_ctx(), args)
    assert _read_json(capsys)["status"] == "uploaded"


# ---------------------------------------------------------------------------
# generate_print_payload
# ---------------------------------------------------------------------------


def test_generate_print_payload_includes_ams_mapping():
    payload = json.loads(job.generate_print_payload("m.3mf", use_ams=True, ams_mapping=[0, 1]))
    assert payload["print"]["use_ams"] is True
    assert payload["print"]["ams_mapping"] == [0, 1]
    assert payload["print"]["param"] == "Metadata/plate_1.gcode"
    assert "m.3mf" in payload["print"]["url"]
    assert payload["print"]["bed_leveling"] is True
    assert payload["print"]["flow_cali"] is True


def test_generate_print_payload_flags_and_url_encoding():
    payload = json.loads(
        job.generate_print_payload(
            "part name.3mf",
            use_ams=False,
            timelapse=True,
            bed_leveling=False,
            flow_cali=False,
        )
    )
    print_cmd = payload["print"]
    assert print_cmd["timelapse"] is True
    assert print_cmd["bed_leveling"] is False
    assert print_cmd["flow_cali"] is False
    assert " " not in print_cmd["url"]  # basename is percent-encoded
    assert print_cmd["subtask_name"] == "part name.3mf"


def test_generate_print_payload_omits_ams_mapping_without_use_ams():
    payload = json.loads(job.generate_print_payload("m.3mf", use_ams=False, ams_mapping=[0, 1]))
    assert payload["print"]["use_ams"] is False
    assert "ams_mapping" not in payload["print"]


def test_parse_print_options_requires_use_ams_pairing():
    from argparse import Namespace

    from bambu_cli.constants import MAX_AMS_SLOT_INDEX

    mapping, err = job._parse_print_options(Namespace(use_ams=True, ams_mapping=None))
    assert mapping is None and err is not None and "ams-mapping" in err.lower()

    mapping, err = job._parse_print_options(Namespace(use_ams=False, ams_mapping="0,1"))
    assert mapping is None and err is not None and "use-ams" in err.lower()

    mapping, err = job._parse_print_options(Namespace(use_ams=True, ams_mapping="0,1,2"))
    assert err is None and mapping == [0, 1, 2]

    mapping, err = job._parse_print_options(Namespace(use_ams=True, ams_mapping=f"{MAX_AMS_SLOT_INDEX + 1}"))
    assert mapping is None and err is not None

    mapping, err = job._parse_print_options(Namespace(use_ams=True, ams_mapping="-1"))
    assert mapping is None and err is not None

    mapping, err = job._parse_print_options(Namespace(use_ams=True, ams_mapping="nope"))
    assert mapping is None and err is not None


def test_predicted_sliced_remote_name_copies():
    name = job._predicted_sliced_remote_name("model.stl", copies=1)
    assert name.endswith("_sliced.3mf")
    assert "model" in name
    name3 = job._predicted_sliced_remote_name("/tmp/foo.stl", copies=3)
    assert "x3" in name3 or "foo" in name3


# ---------------------------------------------------------------------------
# Deep-audit regressions: job/orchestrate.py + job/support.py
# ---------------------------------------------------------------------------


def test_copies_ignored_warns_for_printer_ready(tmp_path, capsys):
    """--copies only multiplies models during slicing; a printer-ready file must
    warn (and flag copies_ignored) instead of silently printing one copy."""
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--copies", "3", "--upload-only", "--json"])
    with _capture_bambu_warnings() as records:
        _run_job(_ctx(), args, JobSteps(upload=fake_upload("model.3mf")))
    payload = _read_json(capsys)
    assert payload.get("copies_ignored") is True
    assert any("--copies only applies" in r.getMessage() for r in records)


def test_copies_ignored_flagged_in_dry_run(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--copies", "2", "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload.get("copies_ignored") is True


def test_copies_one_does_not_flag_printer_ready(tmp_path, capsys):
    ready = tmp_path / "model.3mf"
    ready.write_bytes(b"x" * 10)
    args = _parse(["job", str(ready), "--upload-only", "--json"])
    _run_job(_ctx(), args, JobSteps(upload=fake_upload("model.3mf")))
    payload = _read_json(capsys)
    assert "copies_ignored" not in payload


def test_zip_oserror_routes_through_structured_failure(tmp_path, capsys, monkeypatch):
    """An OSError opening the ZIP must emit the structured job-failure summary
    (failed_step='extract'), not escape as a bare traceback."""
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"solid x")

    real_zipfile = zipfile.ZipFile

    def _boom(path, *a, **kw):
        raise PermissionError("permission denied reading archive")

    monkeypatch.setattr(zipfile, "ZipFile", _boom)
    args = _parse(["job", str(zpath), "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "extract"
    assert payload["status"] == "error"


def test_dry_run_empty_model_file_fails(tmp_path):
    """Symmetric with the printer-ready branch: a 0-byte sliceable model must
    fail dry-run instead of reporting would_slice success."""
    stl = tmp_path / "empty.stl"
    stl.write_bytes(b"")
    args = _parse(["job", str(stl), "--dry-run", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_zip_printer_ready_member_copies_ignored_flagged(tmp_path, capsys):
    """Symmetry: a printer-ready ZIP member with --copies must warn + flag
    copies_ignored, matching the non-ZIP printer-ready branch."""
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("plate.gcode.3mf", b"x" * 20)
    args = _parse(["job", str(zpath), "--copies", "3", "--dry-run", "--json"])
    with _capture_bambu_warnings() as records:
        _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["archive_entry"] == "plate.gcode.3mf"
    assert payload["would_slice"] is False
    assert payload.get("copies_ignored") is True
    assert any("--copies only applies" in r.getMessage() for r in records)


def test_zip_sliceable_member_copies_not_flagged(tmp_path, capsys):
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"solid x")
    args = _parse(["job", str(zpath), "--copies", "3", "--dry-run", "--json"])
    _run_job(_ctx(), args, JobSteps())
    payload = _read_json(capsys)
    assert payload["status"] == "dry_run_local_skipped"
    assert payload["would_slice"] is True
    assert "copies_ignored" not in payload


def test_zip_only_empty_member_fails_dry_run(tmp_path):
    """A ZIP whose only model member is 0 bytes must fail dry-run (knowable
    offline), not report success — symmetric with the empty-file checks."""
    zpath = tmp_path / "empty_member.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"")
    args = _parse(["job", str(zpath), "--dry-run", "--json"])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR


def test_dry_run_output_dir_writability_uses_real_probe(tmp_path, capsys, monkeypatch):
    """The dry-run output-dir writability check must use a real create-probe
    (tempfile.mkstemp), NOT os.access(W_OK) which ignores Windows ACLs. Model an
    ACL-denied ancestor by making the create-probe raise PermissionError while
    os.access still reports writable: the dry-run must fail."""
    import tempfile as _tempfile

    stl = tmp_path / "model.stl"
    stl.write_bytes(b"solid x")
    missing_out = tmp_path / "child_dir"  # nearest existing ancestor is tmp_path

    real_mkstemp = _tempfile.mkstemp

    def _probe_denies_in_tmp(*a, **kw):
        probe_dir = kw.get("dir") or (a[-1] if a else None)
        if probe_dir and os.path.abspath(str(probe_dir)) == os.path.abspath(str(tmp_path)):
            raise PermissionError("ACL denied")
        return real_mkstemp(*a, **kw)

    # os.access would say tmp_path is writable (it is), but the create-probe is
    # denied — the fix must trust the probe, not os.access.
    monkeypatch.setattr(_tempfile, "mkstemp", _probe_denies_in_tmp)
    assert os.access(str(tmp_path), os.W_OK) is True  # the trap the old code fell into

    args = _parse(["job", str(stl), "--dry-run", "--json", "--output", str(missing_out)])
    with pytest.raises((SystemExit, BambuError)) as excinfo:
        _run_job(_ctx(), args, JobSteps())
    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_FILE_ERROR
    payload = _read_json(capsys)
    assert payload["failed_step"] == "validate"
