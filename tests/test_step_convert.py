"""Hermetic unit tests for STEP → STL conversion via gmsh."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from bambu_cli.slicer import step_convert


def _make_step_file(tmp_path: Path, name: str = "part.step") -> Path:
    step_file = tmp_path / name
    step_file.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    return step_file


def _track_mkdtemp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def _fake_mkdtemp(*args: object, **kwargs: object) -> str:
        kwargs["dir"] = str(tmp_path)
        path_str = real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(path_str))
        return path_str

    monkeypatch.setattr(step_convert.tempfile, "mkdtemp", _fake_mkdtemp)
    return created


def test_convert_step_to_stl_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    step_file = _make_step_file(tmp_path)
    created = _track_mkdtemp(monkeypatch, tmp_path)
    monkeypatch.setattr(step_convert.shutil, "which", lambda _name: None)

    expected_stl = b"solid part\nendsolid part\n"

    def _fake_run(cmd_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd_args[0] == "gmsh"
        out_path = Path(cmd_args[cmd_args.index("-o") + 1])
        out_path.write_bytes(expected_stl)
        return subprocess.CompletedProcess(cmd_args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(step_convert.subprocess, "run", _fake_run)

    stl_path, ok = step_convert._convert_step_to_stl(str(step_file))
    try:
        assert ok is True
        assert stl_path is not None
        assert Path(stl_path).exists()
        assert Path(stl_path).read_bytes() == expected_stl
        assert len(created) == 1
        assert created[0].exists()
    finally:
        if stl_path is not None:
            shutil.rmtree(os.path.dirname(stl_path), ignore_errors=True)


def test_convert_step_to_stl_rejects_empty_stl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    step_file = _make_step_file(tmp_path)
    created = _track_mkdtemp(monkeypatch, tmp_path)
    monkeypatch.setattr(step_convert.shutil, "which", lambda _name: "/usr/bin/nice")

    def _fake_run(cmd_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        out_path = Path(cmd_args[cmd_args.index("-o") + 1])
        out_path.write_bytes(b"")
        return subprocess.CompletedProcess(cmd_args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(step_convert.subprocess, "run", _fake_run)

    stl_path, ok = step_convert._convert_step_to_stl(str(step_file))
    assert (stl_path, ok) == (None, False)
    assert len(created) == 1
    assert created[0].name.startswith("bambu_step_")
    assert not created[0].exists()


def test_convert_step_to_stl_nonzero_exit_cleans_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    step_file = _make_step_file(tmp_path)
    created = _track_mkdtemp(monkeypatch, tmp_path)
    monkeypatch.setattr(step_convert.shutil, "which", lambda _name: None)

    def _fake_run(cmd_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd_args,
            returncode=1,
            stdout="gmsh stdout diagnostics",
            stderr="gmsh stderr failure",
        )

    monkeypatch.setattr(step_convert.subprocess, "run", _fake_run)

    stl_path, ok = step_convert._convert_step_to_stl(str(step_file))
    assert (stl_path, ok) == (None, False)
    assert len(created) == 1
    assert not created[0].exists()


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("gmsh not found"),
        subprocess.TimeoutExpired(cmd=["gmsh"], timeout=60),
    ],
)
def test_convert_step_to_stl_subprocess_errors_clean_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exc: BaseException,
) -> None:
    step_file = _make_step_file(tmp_path)
    created = _track_mkdtemp(monkeypatch, tmp_path)
    monkeypatch.setattr(step_convert.shutil, "which", lambda _name: None)

    def _fake_run(_cmd_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise exc

    monkeypatch.setattr(step_convert.subprocess, "run", _fake_run)

    stl_path, ok = step_convert._convert_step_to_stl(str(step_file))
    assert (stl_path, ok) == (None, False)
    assert len(created) == 1
    assert not created[0].exists()
