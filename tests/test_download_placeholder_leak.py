"""Regression tests for two download-path defects (deep audit):

1. ``partial_path`` left ``None`` when a ``Content-Disposition`` header upgrades a
   resolved-name (stl_name) download to an archive — the transfer body then did
   ``open(None, "wb")`` and crashed with a TypeError reported as a network error.
2. 0-byte placeholder files (reserved by ``_noncolliding_path`` via O_CREAT|O_EXCL)
   left on disk on failure/retarget paths.

These run against a REAL temp directory (real ``_noncolliding_path`` and real
``open``) so the placeholder-creation and archive-temp behaviour is exercised for
real; only the HTTP opener and the Printables resolver are faked.
"""

from __future__ import annotations

import argparse
import io
import os
import urllib.error
import zipfile

import pytest

from bambu_cli.commands import cmd_download
from bambu_cli.errors import BambuError


class _FakeResp:
    def __init__(self, chunks, headers=None, final_url=None):
        self._chunks = list(chunks)
        self._headers = headers or {}
        self._final_url = final_url

    def read(self, n=None):
        return self._chunks.pop(0) if self._chunks else b""

    def getheader(self, name):
        return self._headers.get(name)

    def geturl(self):
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Returns a queued response, or raises a queued exception, per ``open`` call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _args(url, outdir, name=None):
    return argparse.Namespace(
        url=url,
        output=str(outdir),
        name=name,
        json=True,
        progress=False,
        max_download_mb=1024,
    )


def _zip_bytes(member="model.stl", data=b"solid x\nendsolid x\n"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, data)
    return buf.getvalue()


def _run(args, opener, resolve):
    return cmd_download(
        args,
        opener_factory=lambda: opener,
        resolve_printables=lambda url: resolve,
    )


# --- Finding 1: Content-Disposition archive upgrade of a resolved-name download ---


def test_content_disposition_archive_upgrade_with_stl_name(tmp_path):
    """A resolved-name (stl_name) download whose server sends a non-.zip URL,
    ambiguous content-type, but ``Content-Disposition: filename="pack.zip"`` must
    NOT crash: the archive temp is created and the zip is extracted. Before the
    fix, ``partial_path`` stayed None → ``open(None, "wb")`` TypeError.
    """
    payload = _zip_bytes()
    resp = _FakeResp(
        [payload, b""],
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="pack.zip"',
        },
    )
    opener = _FakeOpener([resp])
    # stl_name set (as if from a Printables/HTML hint), no --name, non-archive URL.
    args = _args("https://cdn.example.com/download?id=42", tmp_path, name=None)
    result = _run(args, opener, resolve=("https://cdn.example.com/download?id=42", "widget.stl"))

    assert result is not None
    assert os.path.exists(result)
    assert result.endswith(".stl")
    # The archive temp must not survive.
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".bambu-download-")]
    assert leftovers == [], f"archive temp leaked: {leftovers}"


# --- Finding 2: 0-byte placeholder cleanup on failure ---


def test_no_placeholder_left_on_http_error(tmp_path):
    """A failed request must not leave a 0-byte file with the resolved name."""
    opener = _FakeOpener([urllib.error.HTTPError("http://x/f.stl", 404, "Not Found", {}, None)])
    args = _args("https://cdn.example.com/f.stl", tmp_path)
    with pytest.raises(BambuError):
        _run(args, opener, resolve=(None, None))
    survivors = os.listdir(tmp_path)
    assert survivors == [], f"placeholder(s) leaked after failure: {survivors}"


def test_no_placeholder_left_on_archive_upgrade(tmp_path):
    """When a resolved-name download upgrades to an archive, the reserved
    resolved-name placeholder must be cleaned up (not orphaned as 0 bytes)."""
    payload = _zip_bytes()
    resp = _FakeResp(
        [payload, b""],
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="pack.zip"',
        },
    )
    opener = _FakeOpener([resp])
    args = _args("https://cdn.example.com/download?id=7", tmp_path, name=None)
    result = _run(args, opener, resolve=("https://cdn.example.com/download?id=7", "gadget.stl"))
    assert os.path.exists(result)
    # Only the one extracted model file should remain; no 0-byte placeholder,
    # no archive temp.
    entries = sorted(os.listdir(tmp_path))
    assert entries == [os.path.basename(result)], f"unexpected leftovers: {entries}"
    assert os.path.getsize(result) > 0


# --- Finding 4: encrypted ZIP member -> ValueError (extract failure), not RuntimeError ---


def test_encrypted_zip_member_raises_valueerror(tmp_path, monkeypatch):
    """``zipfile`` raises RuntimeError for an encrypted/password-protected member.
    ``_extract_zip_model`` must map it to ValueError so the download reports an
    extract failure (EXIT_FILE_ERROR) rather than letting the RuntimeError escape
    to the generic handler as a spurious network error."""
    from bambu_cli.download.extract import _extract_zip_model

    zip_path = tmp_path / "enc.zip"
    zip_path.write_bytes(_zip_bytes(member="model.stl"))

    real_open = zipfile.ZipFile.open

    def _fake_open(self, name, mode="r", *a, **kw):
        # Only the member extraction call passes a ZipInfo; simulate the stdlib
        # RuntimeError it raises for an encrypted member.
        if isinstance(name, zipfile.ZipInfo):
            raise RuntimeError(
                f"File {name.filename} is encrypted, password required for extraction"
            )
        return real_open(self, name, mode, *a, **kw)

    monkeypatch.setattr(zipfile.ZipFile, "open", _fake_open)

    args = argparse.Namespace(name=None, max_download_mb=1024)
    with pytest.raises(ValueError) as ei:
        _extract_zip_model(str(zip_path), str(tmp_path), args)
    assert "encrypted" in str(ei.value).lower()
    # The member partial must be cleaned up (no leftover .part files).
    leftovers = [p for p in os.listdir(tmp_path) if p != "enc.zip"]
    assert leftovers == [], f"leftover files after encrypted-member failure: {leftovers}"
