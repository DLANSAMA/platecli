"""Tests for typed pipeline results, retired global side channels, and ZIP/orientation seams."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bambu_cli import context as _context
from bambu_cli import utils
from bambu_cli.cliparse import build_parser
from bambu_cli.constants import EXIT_FILE_ERROR
from bambu_cli.context import RuntimeContext, Settings
from bambu_cli.download import DownloadResult, _cmd_download
from bambu_cli.errors import BambuError
from bambu_cli.interactive import core
from bambu_cli.job import JobSteps, _last_error_for, _run_job
from bambu_cli.slicer import SliceResult, _finalize_slice
from bambu_cli.slicer.orientation import parse_orientation_summary


def _parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        settings=Settings(printer_ip="192.168.1.23", printer_model="P1S", nozzle_size="0.4"),
        simulation=True,
    )


def _sliced_3mf_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    return buf.getvalue()


class _FakeHTTPResponse:
    def __init__(self, body: bytes, *, url: str, content_type: str = "application/octet-stream"):
        self._buf = io.BytesIO(body)
        self._url = url
        self._headers = {"Content-Type": content_type, "Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def getheader(self, name: str, default=None):
        return self._headers.get(name, default)

    def geturl(self) -> str:
        return self._url


class _FakeOpener:
    def __init__(self, response: _FakeHTTPResponse):
        self._response = response

    def open(self, _req, timeout=None):
        return self._response


def test_cmd_download_returns_typed_download_result_for_direct_and_zip(tmp_path):
    # Direct STL download
    direct_args = argparse.Namespace(
        url="https://example.com/cube.stl",
        output=str(tmp_path),
        name=None,
        max_download_mb=10,
        json=False,
        progress=False,
    )
    direct = _cmd_download(
        direct_args,
        opener_factory=lambda: _FakeOpener(_FakeHTTPResponse(b"solid x\nendsolid x\n", url=direct_args.url)),
    )
    assert isinstance(direct, DownloadResult)
    assert isinstance(direct, str)
    assert direct.archive_entry is None
    assert direct.extracted_path is None
    assert Path(direct).is_file()

    # ZIP download with extracted model
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("nested/widget.stl", b"solid w\nendsolid w\n")
    zip_args = argparse.Namespace(
        url="https://example.com/bundle.zip",
        output=str(tmp_path),
        name=None,
        max_download_mb=10,
        json=False,
        progress=False,
    )
    extracted = _cmd_download(
        zip_args,
        opener_factory=lambda: _FakeOpener(
            _FakeHTTPResponse(zip_buf.getvalue(), url=zip_args.url, content_type="application/zip")
        ),
    )
    assert isinstance(extracted, DownloadResult)
    assert extracted.archive_entry == "widget.stl"
    assert extracted.extracted_path == str(extracted)
    assert Path(extracted).is_file()


def test_job_ignores_stale_last_download_payload_global(tmp_path, capsys):
    downloaded = tmp_path / "model.stl"
    downloaded.write_bytes(b"solid x\nendsolid x\n")
    out = tmp_path / "out"
    sliced = out / "model.3mf"
    out.mkdir()
    sliced.write_bytes(_sliced_3mf_bytes())

    # Pollute the legacy global before _run_job runs: orchestrator must ignore it.
    utils._LAST_DOWNLOAD_PAYLOAD = {"path": "/stale/path.stl", "archive_entry": "stale.stl"}
    try:
        args = _parse(["job", "https://example.com/model.stl", "--json", "--output", str(out)])
        steps = JobSteps(
            download=lambda _a: DownloadResult(str(downloaded)),
            slice=lambda _a: SliceResult(str(sliced), orientation={"mode": "auto", "face": 1}),
            upload=lambda _a: "model.3mf",
        )
        _run_job(_ctx(), args, steps)
    finally:
        utils._LAST_DOWNLOAD_PAYLOAD = None

    payload = json.loads(capsys.readouterr().out)
    assert payload["would_extract"] is False
    assert payload["archive_entry"] is None
    assert payload["extracted_path"] is None
    assert payload["orientation"] == {"mode": "auto", "face": 1}


def test_finalize_slice_returns_typed_slice_result(tmp_path):
    outpath = tmp_path / "part.3mf"
    outpath.write_bytes(_sliced_3mf_bytes())
    summary = {"mode": "face", "changed": True, "face": 2, "rotations": [], "height_mm": 4.5}
    proc = subprocess.CompletedProcess(args=["orca"], returncode=0, stdout="", stderr="")
    args = argparse.Namespace(file=str(tmp_path / "part.stl"), json=False)

    res = _finalize_slice(proc, str(outpath), args, str(tmp_path / "part.stl"), False, orientation=summary)
    assert isinstance(res, SliceResult)
    assert isinstance(res, str)
    assert res == str(outpath)
    assert res.orientation == summary


def test_job_and_interactive_read_slice_result_not_mutated_namespace(tmp_path, capsys):
    stl = tmp_path / "part.stl"
    stl.write_bytes(b"solid p\nendsolid p\n")
    sliced = tmp_path / "part_sliced.3mf"
    sliced.write_bytes(_sliced_3mf_bytes())

    real_orient = {"mode": "auto", "changed": True, "face": 1, "rotations": [], "height_mm": 5.0}
    stale_ns_orient = {"mode": "keep", "changed": False, "face": 9}

    def fake_slice(ns):
        ns.orientation_result = stale_ns_orient
        return SliceResult(str(sliced), orientation=real_orient)

    args = _parse(["job", str(stl), "--json", "--output", str(tmp_path)])
    steps = JobSteps(slice=fake_slice, upload=lambda _a: "part_sliced.3mf")
    _run_job(_ctx(), args, steps)
    payload = json.loads(capsys.readouterr().out)
    assert payload["orientation"] == real_orient

    saved = _context.get_current()
    _context.set_current(_ctx())
    try:
        state = core.WizardState(source=str(stl))
        core.slice_fetched_model(core.GoSteps(slice=fake_slice), state, str(tmp_path), str(stl))
        assert state.orientation_summary == real_orient
        assert state.printable_path == str(sliced)
        assert type(state.printable_path) is str
    finally:
        _context.set_current(saved)


def test_last_error_for_ignores_global_side_channel():
    ctx = _ctx()
    utils._LAST_ERROR_PAYLOAD = {"command": "download", "error": "stale global"}
    try:
        assert _last_error_for("download", ctx=ctx, exc=None) is None
        assert ctx.last_error is None

        exc = BambuError("boom", failed_step="http", exit_code=2)
        payload = _last_error_for("download", ctx=ctx, exc=exc)
        assert payload is not None
        assert payload["command"] == "download"
        assert payload["error"] == "boom"
        assert payload["failed_step"] == "http"
        assert ctx.last_error == payload
    finally:
        utils._LAST_ERROR_PAYLOAD = None


def test_fetch_model_aborts_on_corrupt_local_zip(tmp_path):
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_bytes(b"not a zip archive")
    state = core.WizardState(source=str(bad_zip))
    with pytest.raises(BambuError) as exc_info:
        core.fetch_model(core.GoSteps(), state, str(tmp_path))
    assert exc_info.value.exit_code == EXIT_FILE_ERROR
    assert exc_info.value.failed_step == "extract"


@pytest.mark.parametrize(
    "raised",
    [
        zipfile.BadZipFile("bad crc"),
        PermissionError("permission denied"),
    ],
)
def test_fetch_model_catches_bad_zip_and_os_error_from_extractor(tmp_path, raised):
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    state = core.WizardState(source=str(archive))
    with patch("bambu_cli.download._extract_zip_model", side_effect=raised), pytest.raises(BambuError) as exc_info:
        core.fetch_model(core.GoSteps(), state, str(tmp_path))
    assert exc_info.value.exit_code == EXIT_FILE_ERROR
    assert exc_info.value.failed_step == "extract"


def test_parse_orientation_summary_shared_contract():
    assert parse_orientation_summary(None) is None
    assert parse_orientation_summary({"mode": "keep", "presliced": True}) is None

    auto_failed = parse_orientation_summary({"mode": "keep", "warning": "unreadable"})
    assert auto_failed is not None
    assert auto_failed.auto_failed is True
    assert auto_failed.unstable_keep is False
    assert auto_failed.tips_over is False

    unstable_keep = parse_orientation_summary(
        {"mode": "keep", "stable": False, "warning": "rests on point", "height_mm": True}
    )
    assert unstable_keep is not None
    assert unstable_keep.auto_failed is False
    assert unstable_keep.unstable_keep is True
    assert unstable_keep.tips_over is True
    assert unstable_keep.height_mm is None

    turned = parse_orientation_summary(
        {"mode": "face", "face": 2, "rotations": ["z=15"], "turned": "15° about Z", "height_mm": 12.34, "stable": True}
    )
    assert turned is not None
    assert turned.mode == "face"
    assert turned.face == 2
    assert turned.rotations == ("z=15",)
    assert turned.has_turned is True
    assert turned.turned == "15° about Z"
    assert turned.height_mm == 12.34
    assert turned.tips_over is False
