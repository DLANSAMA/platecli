"""Targeted regression tests for download, printables, slicer, job, and path fixes."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import urllib.request
import zipfile

import pytest

from bambu_cli import utils
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.context import RuntimeContext
from bambu_cli.download.downloader import _cmd_download
from bambu_cli.download.extract import _extract_zip_model
from bambu_cli.download.html_links import _resolve_html_model_link
from bambu_cli.download.naming import (
    _download_filename_with_extension,
    _download_target_filename,
    _safe_remote_name,
    _within_name_budget,
)
from bambu_cli.errors import BambuError, FileError
from bambu_cli.job import JobSteps, _run_job
from bambu_cli.job.predict import _predicted_url_remote_name
from bambu_cli.paths import exception_for_message
from bambu_cli.printables import client as printables_client
from bambu_cli.slicer import cmd_slice
from bambu_cli.slicer.options import _is_finite_number, _validate_slice_options
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, write_stl
from tests.json_contract_base import sliced_3mf_bytes


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, url: str = ""):
        self._buf = io.BytesIO(body)
        self._headers = headers or {}
        self._url = url

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, handler):
        self._handler = handler

    def open(self, req: urllib.request.Request, timeout: float | None = None):
        return self._handler(req, timeout)


def _valid_3mf_bytes() -> bytes:
    return sliced_3mf_bytes(plates=(1,))


# ---------------------------------------------------------------------------
# 1. naming._download_filename_with_extension budget enforcement
# ---------------------------------------------------------------------------


def test_download_filename_with_extension_truncates_near_budget():
    long_stem = "a" * 158
    result = _download_filename_with_extension(f"{long_stem}.3mf", "https://example.com/model.gcode")
    assert _within_name_budget(result)
    assert _safe_remote_name(result) == result
    assert result.endswith(".gcode")

    no_ext = _download_filename_with_extension("b" * 159, "https://example.com/part.stl")
    assert _within_name_budget(no_ext)
    assert _safe_remote_name(no_ext) == no_ext
    assert no_ext.endswith(".stl")


def test_download_filename_with_extension_utf8_and_trailing_dots():
    utf8_name = "あ" * 53  # 159 bytes
    result = _download_filename_with_extension(utf8_name, "https://example.com/part.stl")
    assert _within_name_budget(result)
    assert _safe_remote_name(result) == result
    assert result.endswith(".stl")

    dotty = "." * 158
    fallback = _download_filename_with_extension(dotty, "https://example.com/part.stl")
    assert fallback == "model.stl"

    args = argparse.Namespace(name="c" * 160)
    target = _download_target_filename(args, "https://example.com/dl?id=1")
    assert _within_name_budget(target)
    assert _safe_remote_name(target) == target
    assert target.endswith(".stl")


# ---------------------------------------------------------------------------
# 2. html_links._NON_MODEL_WEB_EXTENSIONS
# ---------------------------------------------------------------------------


def test_html_links_ignores_hint_on_non_model_web_extensions():
    html = b'<a href="/page.html" data-download-url="/dl?id=1" data-filename="cube.stl">dl</a>'
    url, name = _resolve_html_model_link(html, "https://example.com/models/1")
    assert url == "https://example.com/dl?id=1"
    assert name == "cube.stl"


def test_html_links_ignores_hint_on_css_js_json_but_keeps_php_and_extensionless():
    html = (
        b'<a href="/app.js" data-filename="bad.stl">js</a>'
        b'<a href="/style.css" data-filename="bad.stl">css</a>'
        b'<a href="/meta.json" data-filename="bad.stl">json</a>'
        b'<a href="/download.php?id=9" data-filename="good.stl">php</a>'
    )
    url, name = _resolve_html_model_link(html, "https://example.com/")
    assert url == "https://example.com/download.php?id=9"
    assert name == "good.stl"


# ---------------------------------------------------------------------------
# 3. extract._extract_zip_model cleanup on os.replace OSError / KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_extract_zip_model_oserror_on_replace_cleans_up_reservation(tmp_path, monkeypatch):
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("cube.stl", b"solid cube\nendsolid cube\n")
    # Existing cube.stl forces _noncolliding to reserve cube-1.stl and replace_on_success=True
    (tmp_path / "cube.stl").write_bytes(b"existing")

    def _fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("bambu_cli.download.extract.os.replace", _fail_replace)
    args = argparse.Namespace(name=None, max_download_mb=100)
    with pytest.raises(OSError, match="disk full"):
        _extract_zip_model(str(zpath), str(tmp_path), args)

    leftovers = [p.name for p in tmp_path.iterdir()]
    assert sorted(leftovers) == ["cube.stl", "model.zip"]


def test_extract_zip_model_keyboard_interrupt_cleans_up_reservation(tmp_path, monkeypatch):
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("cube.stl", b"solid cube\nendsolid cube\n")
    (tmp_path / "cube.stl").write_bytes(b"existing")

    def _interrupt_replace(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr("bambu_cli.download.extract.os.replace", _interrupt_replace)
    args = argparse.Namespace(name=None, max_download_mb=100)
    with pytest.raises(KeyboardInterrupt):
        _extract_zip_model(str(zpath), str(tmp_path), args)

    leftovers = [p.name for p in tmp_path.iterdir()]
    assert sorted(leftovers) == ["cube.stl", "model.zip"]


# ---------------------------------------------------------------------------
# 4. downloader.py preserve_archive OSError + KeyboardInterrupt cleanup
# ---------------------------------------------------------------------------


def test_download_preserve_archive_oserror_removes_target_placeholder(tmp_path, monkeypatch):
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("a.stl", b"solid a\nendsolid a\n")
        zf.writestr("b.stl", b"solid b\nendsolid b\n")
    zip_bytes = zbuf.getvalue()

    def _handler(req, timeout):
        return _FakeResponse(
            zip_bytes,
            headers={"Content-Type": "application/zip", "Content-Length": str(len(zip_bytes))},
            url=req.full_url,
        )

    real_replace = os.replace

    def _selective_fail_replace(src, dst):
        if os.path.basename(dst).startswith("bundle"):
            raise OSError("rename failed")
        return real_replace(src, dst)

    monkeypatch.setattr("bambu_cli.download.downloader.os.replace", _selective_fail_replace)
    args = argparse.Namespace(
        url="https://example.com/bundle.zip",
        output=str(tmp_path),
        name=None,
        max_download_mb=100,
        json=False,
        progress=False,
    )
    extracted = _cmd_download(
        args,
        opener_factory=lambda: _FakeOpener(_handler),
        resolve_printables=lambda u: (None, None),
    )
    assert os.path.exists(extracted)
    assert not (tmp_path / "bundle.zip").exists()


def test_cmd_download_keyboard_interrupt_cleans_partial_and_reserved(tmp_path):
    class _InterruptResp(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise KeyboardInterrupt

    def _handler(req, timeout):
        return _InterruptResp(b"", headers={"Content-Type": "model/stl"}, url=req.full_url)

    args = argparse.Namespace(
        url="https://example.com/cube.stl",
        output=str(tmp_path),
        name=None,
        max_download_mb=100,
        json=False,
        progress=False,
    )
    with pytest.raises(KeyboardInterrupt):
        _cmd_download(
            args,
            opener_factory=lambda: _FakeOpener(_handler),
            resolve_printables=lambda u: (None, None),
        )
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# 5. printables/client.py _entry_file_size null fileSize safety
# ---------------------------------------------------------------------------


def test_entry_file_size_handles_null_and_invalid_values():
    assert printables_client._entry_file_size({}) == 0
    assert printables_client._entry_file_size({"fileSize": None}) == 0
    assert printables_client._entry_file_size({"fileSize": True}) == 0
    assert printables_client._entry_file_size({"fileSize": -5}) == 0
    assert printables_client._entry_file_size({"fileSize": "123"}) == 0
    assert printables_client._entry_file_size({"fileSize": float("nan")}) == 0
    assert printables_client._entry_file_size({"fileSize": float("inf")}) == 0
    assert printables_client._entry_file_size({"fileSize": 2048.9}) == 2048


def test_printables_get_file_info_tolerates_null_filesize(monkeypatch):
    payload_stls = {
        "data": {
            "print": {
                "name": "Null Size Model",
                "stls": [
                    {"id": "1", "name": "a.stl", "fileSize": None},
                    {"id": "2", "name": "b.stl", "fileSize": 4096},
                ],
                "gcodes": [],
            }
        }
    }
    monkeypatch.setattr(printables_client, "_post_graphql", lambda *a, **k: payload_stls)
    fid, ftype, fname = printables_client.get_file_info("99", {}, None)
    assert (fid, ftype, fname) == ("2", "stl", "b.stl")

    payload_3mf = {
        "data": {
            "print": {
                "name": "Null 3MF Model",
                "stls": [],
                "gcodes": [
                    {"id": "3", "name": "plate.3mf", "fileSize": None},
                    {"id": "4", "name": "plate2.3mf", "fileSize": 8192},
                ],
            }
        }
    }
    monkeypatch.setattr(printables_client, "_post_graphql", lambda *a, **k: payload_3mf)
    fid, ftype, fname = printables_client.get_file_info("99", {}, None)
    assert (fid, ftype, fname) == ("4", "gcode", "plate2.3mf")


# ---------------------------------------------------------------------------
# 6. slicer/cmd.py: STEP source_filepath, deterministic JSON filament, preserve outpath
# ---------------------------------------------------------------------------


def _make_slice_args(filepath: str, outdir: str, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        file=filepath,
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


def test_slice_step_error_reports_original_step_filepath(tmp_path, monkeypatch):
    step_file = tmp_path / "bracket.step"
    step_file.write_text("ISO-10303-21;\n", encoding="utf-8")
    temp_dir = tmp_path / "bambu_step_tmp"
    temp_dir.mkdir()
    converted_stl = write_stl(str(temp_dir / "bracket.stl"))

    fake_orca = tmp_path / "orca-slicer"
    fake_orca.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_orca.chmod(0o755)
    profiles = build_profiles_dir(str(tmp_path))
    outdir = tmp_path / "out"
    outdir.mkdir()

    monkeypatch.setattr("bambu_cli.slicer.cmd._convert_step_to_stl", lambda p: (converted_stl, True))
    monkeypatch.setattr(
        "bambu_cli.slicer.cmd._run_orcaslicer",
        lambda *a, **k: subprocess.CompletedProcess(["orca"], 1, stdout="[error] bad mesh", stderr=""),
    )

    with settings_ctx(orca_slicer=str(fake_orca), profiles_dir=profiles):
        with pytest.raises(BambuError) as ei:
            cmd_slice(_make_slice_args(str(step_file), str(outdir)))
    assert ei.value.extra["file"] == str(step_file)
    assert not temp_dir.exists()


def test_slice_filament_matching_skips_dirs_and_non_json_deterministically(tmp_path, monkeypatch):
    profiles = build_profiles_dir(str(tmp_path))
    fil_dir = os.path.join(profiles, "filament")
    # Distractor directory and non-JSON file that both contain "pla basic" and "@base"
    os.makedirs(os.path.join(fil_dir, "00_PLA Basic @base"))
    with open(os.path.join(fil_dir, "00_PLA Basic @base.txt"), "w", encoding="utf-8") as f:
        f.write("not json")
    with open(os.path.join(fil_dir, "AAA Bambu PLA Basic @base.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "AAA Bambu PLA Basic @base"}, f)

    fake_orca = tmp_path / "orca-slicer"
    fake_orca.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_orca.chmod(0o755)
    model = write_stl(str(tmp_path / "cube.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    captured_cmd: list[str] = []

    def _fake_run(cmd, *a, **k):
        captured_cmd.extend(cmd)
        out_idx = cmd.index("--outputdir") + 1
        out_dir = cmd[out_idx]
        with open(os.path.join(out_dir, "cube_sliced.3mf"), "wb") as fh:
            fh.write(_valid_3mf_bytes())
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("bambu_cli.slicer.cmd._run_orcaslicer", _fake_run)
    with settings_ctx(orca_slicer=str(fake_orca), profiles_dir=profiles):
        out = cmd_slice(_make_slice_args(model, str(outdir)))
    assert os.path.exists(out)


@pytest.mark.parametrize("bad_bytes", [b"", b"not-a-valid-3mf"])
def test_slice_preserves_existing_outpath_when_produced_is_empty_or_corrupt(tmp_path, monkeypatch, bad_bytes):
    fake_orca = tmp_path / "orca-slicer"
    fake_orca.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_orca.chmod(0o755)
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()
    existing = outdir / "model_sliced.3mf"
    original_bytes = _valid_3mf_bytes()
    existing.write_bytes(original_bytes)

    def _fake_run(cmd, *a, **k):
        out_idx = cmd.index("--outputdir") + 1
        with open(os.path.join(cmd[out_idx], "model_sliced.3mf"), "wb") as fh:
            fh.write(bad_bytes)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("bambu_cli.slicer.cmd._run_orcaslicer", _fake_run)
    with settings_ctx(orca_slicer=str(fake_orca), profiles_dir=profiles):
        with pytest.raises(FileError) as ei:
            cmd_slice(_make_slice_args(model, str(outdir)))

    assert ei.value.exit_code == EXIT_FILE_ERROR
    assert ei.value.failed_step == "slicer"
    assert ei.value.extra["bytes"] == len(bad_bytes)
    assert ei.value.extra["output"] == str(existing)
    assert existing.read_bytes() == original_bytes
    assert [p.name for p in outdir.iterdir()] == ["model_sliced.3mf"]


# ---------------------------------------------------------------------------
# 7. slicer/options.py finite numbers & new bounds
# ---------------------------------------------------------------------------


def test_is_finite_number():
    assert _is_finite_number(0) is True
    assert _is_finite_number(1.5) is True
    assert _is_finite_number(True) is False
    assert _is_finite_number(False) is False
    assert _is_finite_number(float("nan")) is False
    assert _is_finite_number(float("inf")) is False
    assert _is_finite_number(float("-inf")) is False
    assert _is_finite_number("1") is False


@pytest.mark.parametrize(
    "field",
    [
        "layer_height",
        "brim",
        "speed",
        "first_layer_height",
        "support_threshold",
        "fan_speed",
        "flow_ratio",
        "support_interface_density",
        "infill",
        "nozzle_temp",
        "bed_temp",
        "copies",
        "threads",
        "walls",
        "top_layers",
        "bottom_layers",
        "accel_wall",
        "accel_wall_outer",
        "accel_infill",
        "accel_travel",
        "accel_first_layer",
    ],
)
@pytest.mark.parametrize("bad_val", [float("nan"), float("inf"), float("-inf")])
def test_validate_slice_options_rejects_non_finite_values(field, bad_val):
    args = argparse.Namespace(**{field: bad_val})
    err = _validate_slice_options(args)
    assert err is not None


def test_validate_slice_options_bounds_for_threads_layers_density_and_accel():
    assert _validate_slice_options(argparse.Namespace(threads=0)) is not None
    assert _validate_slice_options(argparse.Namespace(threads=1)) is None

    for attr in ("walls", "top_layers", "bottom_layers"):
        assert _validate_slice_options(argparse.Namespace(**{attr: -1})) is not None
        assert _validate_slice_options(argparse.Namespace(**{attr: 0})) is None

    assert _validate_slice_options(argparse.Namespace(support_interface_density=-0.1)) is not None
    assert _validate_slice_options(argparse.Namespace(support_interface_density=100.1)) is not None
    assert _validate_slice_options(argparse.Namespace(support_interface_density=0.0)) is None
    assert _validate_slice_options(argparse.Namespace(support_interface_density=100.0)) is None

    for attr in ("accel_wall", "accel_wall_outer", "accel_infill", "accel_travel", "accel_first_layer"):
        assert _validate_slice_options(argparse.Namespace(**{attr: 0})) is not None
        assert _validate_slice_options(argparse.Namespace(**{attr: -10})) is not None
        assert _validate_slice_options(argparse.Namespace(**{attr: 1})) is None


# ---------------------------------------------------------------------------
# 8. job/orchestrate.py & job/predict.py
# ---------------------------------------------------------------------------


def test_job_url_dry_run_rejects_plate_gt_1_when_slicing(caplog):
    args = argparse.Namespace(
        source="https://example.com/cube.stl",
        dry_run=True,
        plate=2,
        json=False,
        confirm=False,
        upload_only=False,
    )
    with caplog.at_level(logging.ERROR, logger="bambu"):
        with pytest.raises(BambuError) as ei:
            _run_job(RuntimeContext(), args, steps=JobSteps())
    assert ei.value.exit_code == EXIT_COMMAND_ERROR
    assert "--plate only applies to a pre-sliced 3MF" in caplog.text


def test_job_local_zip_with_output_does_not_warn_output_ignored(tmp_path, caplog):
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("ready.3mf", _valid_3mf_bytes())
    outdir = tmp_path / "extracted"

    steps = JobSteps(
        upload=lambda a: os.path.basename(a.file),
    )
    args = argparse.Namespace(
        source=str(zpath),
        output=str(outdir),
        dry_run=False,
        confirm=False,
        upload_only=True,
        json=False,
        copies=1,
    )
    with caplog.at_level(logging.WARNING, logger="bambu"):
        _run_job(RuntimeContext(), args, steps=steps)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("--output is only used" in w for w in warnings), warnings


def test_predicted_url_remote_name_extensionless_with_3mf_name():
    args = argparse.Namespace(name="foo.3mf", copies=1)
    assert _predicted_url_remote_name("https://example.com/dl?id=1", args) == "foo_sliced.3mf"


# ---------------------------------------------------------------------------
# 9. paths.py & utils.py Windows/home compaction
# ---------------------------------------------------------------------------


def test_exception_for_message_uses_forward_slashes_on_windows(monkeypatch):
    import bambu_cli.paths as paths

    monkeypatch.setattr(paths.os, "sep", "\\")
    monkeypatch.setattr(paths, "_HOME_DIR", "C:\\Users\\alice")
    monkeypatch.setattr(paths, "_NORM_HOME_DIR", None)
    monkeypatch.setattr(paths.os.path, "isabs", lambda p: p.startswith("C:\\") or p.startswith("/"))
    monkeypatch.setattr(paths.os.path, "abspath", lambda p: p)
    monkeypatch.setattr(paths.os.path, "normcase", lambda p: p.lower())
    monkeypatch.setattr(paths.os.path, "relpath", lambda p, h: p[len(h) + 1 :])

    class _PathExc(OSError):
        def __str__(self):
            return f"cannot open {self.filename}"

    exc = _PathExc()
    exc.filename = "C:\\Users\\alice\\models\\cube.stl"
    msg = exception_for_message(exc)
    assert msg == "cannot open ~/models/cube.stl"


def test_utils_display_path_windows_case_insensitive_and_separators(monkeypatch):
    monkeypatch.setattr(utils, "_HOME_DIR", "C:\\Users\\Alice")
    assert utils._display_path("c:\\users\\alice\\models\\cube.stl") == "~\\models\\cube.stl"
    assert utils._display_path("c:/users/alice/models/cube.stl") == "~/models/cube.stl"
    assert utils._display_path("C:\\Users\\alice") == "~"
    assert utils._display_path("C:\\Users\\alice2\\cube.stl") == "C:\\Users\\alice2\\cube.stl"
    assert utils._display_path("c:/users/alice2/cube.stl") == "c:/users/alice2/cube.stl"


def test_utils_compact_all_strings_normalizes_compacted_windows_paths(monkeypatch):
    import bambu_cli.paths as paths

    monkeypatch.setattr(paths.os, "sep", "\\")
    monkeypatch.setattr(utils, "_HOME_DIR", "C:\\Users\\Alice")
    payload = utils._compact_all_strings(
        {
            "path": "c:\\users\\alice\\models\\cube.stl",
            "note": "plain\\text\\unchanged",
            "url": "https://user:pass" + "@" + "example.com/cube.stl",
        }
    )
    assert payload["path"] == "~/models/cube.stl"
    assert payload["note"] == "plain\\text\\unchanged"
    assert payload["url"] == "https://example.com/cube.stl"
