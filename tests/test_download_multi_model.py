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
