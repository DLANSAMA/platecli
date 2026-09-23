"""A ZIP whose compressed data is corrupt is an extract failure, not a crash.

``zipfile`` raises ``zlib.error`` (not ``BadZipFile``) when a deflate stream is
damaged but the headers are intact. Before the 2026-09-22 fix that escaped the
extract handlers: ``plate job`` ended in "Unexpected error" with a traceback
(exit 5) and lost the structured job summary the extract step is built to
report.
"""

from __future__ import annotations

import argparse
import io
import zipfile

import pytest

from bambu_cli.constants import EXIT_FILE_ERROR
from bambu_cli.errors import BambuError


def _corrupt_zip(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("part.stl", b"solid x\n" + b"facet normal 0 0 0\n" * 5000 + b"endsolid x\n")
    data = bytearray(buf.getvalue())
    start = 30 + len("part.stl")  # local file header + name: compressed data follows
    for i in range(start + 10, start + 60):
        data[i] ^= 0xFF
    path.write_bytes(bytes(data))
    return str(path)


def test_extract_reports_corrupt_member_as_value_error(tmp_path):
    from bambu_cli.download.extract import _extract_zip_model

    archive = _corrupt_zip(tmp_path / "bad.zip")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match="corrupt"):
        _extract_zip_model(archive, str(out), argparse.Namespace(name=None, max_download_mb=100))
    assert list(out.iterdir()) == [], "no partial or 0-byte model may be left behind"


def test_job_keeps_its_structured_failure_for_a_corrupt_zip(tmp_path, capsys):
    import json

    from bambu_cli.cliparse import build_parser
    from bambu_cli.commands import cmd_job

    archive = _corrupt_zip(tmp_path / "bad.zip")
    args = build_parser().parse_args(["--json", "job", archive, "--output", str(tmp_path / "work")])
    with pytest.raises(BambuError) as caught:
        cmd_job(args)
    assert caught.value.exit_code == EXIT_FILE_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed_step"] == "extract"
    assert payload["command"] == "job"
    assert "corrupt" in payload["error"]
