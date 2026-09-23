"""Tests for preserving multi-part model ZIP archives during download."""

import argparse
import io
import zipfile
from unittest.mock import patch

from bambu_cli.commands import cmd_download


class FakeResp:
    def __init__(self, data):
        self._bio = io.BytesIO(data)

    def read(self, n=None):
        return self._bio.read(n)

    def getheader(self, name):
        return None

    def geturl(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_preserves_archive_with_multiple_models(tmp_path):
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("part1.stl", b"solid part1\nendsolid part1\n")
        zf.writestr("part2.stl", b"solid part2\nendsolid part2\n")
    zip_data = zip_bytes.getvalue()

    fake_response = FakeResp(zip_data)

    outdir = tmp_path / "models"
    outdir.mkdir()

    args = argparse.Namespace(
        url="https://example.com/multi_part.zip",
        output=str(outdir),
        name=None,
        max_download_mb=100,
        allow_private_ips=False,
        json=False,
    )

    with patch("bambu_cli.download.downloader.polite_open", return_value=fake_response):
        cmd_download(args)

    # Primary extracted model
    assert (outdir / "part1.stl").exists()
    # Archive itself MUST be preserved because it contained multiple model files
    assert (outdir / "multi_part.zip").exists()


def _run_download(tmp_path, url):
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("part1.stl", b"solid part1\nendsolid part1\n")
        zf.writestr("part2.stl", b"solid part2\nendsolid part2\n")

    outdir = tmp_path / "models"
    outdir.mkdir()
    args = argparse.Namespace(
        url=url,
        output=str(outdir),
        name=None,
        max_download_mb=100,
        allow_private_ips=False,
        json=False,
    )
    with patch("bambu_cli.download.downloader.polite_open", return_value=FakeResp(zip_bytes.getvalue())):
        cmd_download(args)
    return outdir


def test_preserved_archive_name_is_length_capped(tmp_path):
    """An over-long URL-derived name used to make os.replace fail with
    ENAMETOOLONG, which was swallowed -- stranding the archive under its hidden
    .bambu-download-* temp name while the log claimed it was preserved."""
    outdir = _run_download(tmp_path, "https://example.com/" + "a" * 300 + ".zip")
    archives = [p for p in outdir.iterdir() if p.suffix == ".zip"]
    assert len(archives) == 1
    assert len(archives[0].name) <= 160
    assert not archives[0].name.startswith(".bambu-download-")


def test_preserved_archive_name_escapes_windows_reserved_stems(tmp_path):
    outdir = _run_download(tmp_path, "https://example.com/con.zip")
    assert (outdir / "_con.zip").exists()
    assert not (outdir / "con.zip").exists()


def test_preserved_archive_name_strips_control_characters(tmp_path):
    outdir = _run_download(tmp_path, "https://example.com/we%00ird%0aname.zip")
    archives = [p for p in outdir.iterdir() if p.suffix == ".zip"]
    assert len(archives) == 1
    assert not any(ord(ch) < 0x20 or ch == "\x00" for ch in archives[0].name)


class ZipCtypeResp(FakeResp):
    """Archive detected by Content-Type rather than by URL extension."""

    def getheader(self, name):
        return "application/zip" if name.lower() == "content-type" else None


def test_preserved_archive_name_falls_back_when_url_path_has_no_basename(tmp_path):
    """Archive recognised by Content-Type on a URL with no filename in the path:
    the name has nothing to derive from, so it must fall back, not end up empty."""
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("part1.stl", b"solid part1\nendsolid part1\n")
        zf.writestr("part2.stl", b"solid part2\nendsolid part2\n")

    outdir = tmp_path / "models"
    outdir.mkdir()
    args = argparse.Namespace(
        url="https://example.com/",
        output=str(outdir),
        name=None,
        max_download_mb=100,
        allow_private_ips=False,
        json=False,
    )
    with patch(
        "bambu_cli.download.downloader.polite_open",
        return_value=ZipCtypeResp(zip_bytes.getvalue()),
    ):
        cmd_download(args)

    assert (outdir / "archive.zip").exists()
