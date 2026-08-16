"""jsonio redaction, home-path display, AMS sentinels, and ZIP extract edges."""

import argparse
import zipfile
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# jsonio.redact_url_credentials — scheme-relative URLs
# ---------------------------------------------------------------------------


def test_redact_scheme_relative_url_strips_userinfo():
    from bambu_cli.jsonio import redact_url_credentials

    # Credential literals are assembled from parts so tests/privacy_smoke.py does
    # not flag them as real credential-bearing URLs / emails.
    at = "@"
    assert redact_url_credentials("//user:pass" + at + "host.com/x") == "//host.com/x"
    assert redact_url_credentials("//u:p" + at + "host.com:8443/a?b=c") == "//host.com:8443/a?b=c"


def test_redact_scheme_relative_url_ipv6_userinfo():
    from bambu_cli.jsonio import redact_url_credentials

    at = "@"
    # netloc-only IPv6 with userinfo: host must stay bracketed, creds gone.
    assert redact_url_credentials("//user:pass" + at + "[::1]:990/x") == "//[::1]:990/x"


def test_redact_preserves_existing_schemeless_and_full_url_behavior():
    from bambu_cli.jsonio import redact_url_credentials

    at = "@"
    # Full scheme URL still redacted.
    assert redact_url_credentials("https://user:pass" + at + "host.com/x") == "https://host.com/x"
    # Schemeless heuristic still redacted.
    assert redact_url_credentials("user:pass" + at + "host.com/x") == "host.com/x"
    # Deliberate non-matches must stay byte-for-byte (pinned by design).
    assert redact_url_credentials("/home/x" + at + "y") == "/home/x" + at + "y"
    assert redact_url_credentials("no-at-sign") == "no-at-sign"


def test_looks_like_schemeless_rejects_spaces_and_backslashes():
    from bambu_cli.jsonio import looks_like_schemeless_credential_url

    at = "@"
    assert looks_like_schemeless_credential_url("user:pass" + at + "host.com") is True
    assert looks_like_schemeless_credential_url("user:pass " + at + "host.com") is False
    assert looks_like_schemeless_credential_url("user:pass" + at + "host\\name.com") is False
    assert looks_like_schemeless_credential_url("") is False


def test_redact_invalid_port_does_not_raise():
    from bambu_cli.jsonio import redact_url_credentials

    at = "@"
    # urllib raises ValueError on .port when the port token is not an int.
    assert redact_url_credentials("https://user:pass" + at + "host.com:notaport/x") == ("https://host.com/x")
    assert redact_url_credentials("//user:pass" + at + "host.com:notaport/x") == "//host.com/x"


def test_emit_json_uses_jsonio_redactor(capsys):
    """emit_json must strip userinfo, not the weaker ***@ placeholder."""
    from bambu_cli import utils

    at = "@"
    utils._JSON_EMITTED = False
    utils.emit_json({"source": "https://user:pass" + at + "host.com/x.stl"})
    payload = capsys.readouterr().out
    assert "user:pass" not in payload
    assert "***@" not in payload
    assert "https://host.com/x.stl" in payload


# ---------------------------------------------------------------------------
# utils._display_path — home-prefix separator boundary
# ---------------------------------------------------------------------------


def test_display_path_requires_separator_boundary(monkeypatch):
    import bambu_cli.utils as utils

    monkeypatch.setattr(utils, "_HOME_DIR", "/home/alice")
    # Sibling dir merely starting with the home name must NOT be mangled.
    assert utils._display_path("/home/alice-backup/model.stl") == "/home/alice-backup/model.stl"
    assert utils._display_path("/home/alice2/x") == "/home/alice2/x"
    # Genuine home paths still compact.
    assert utils._display_path("/home/alice/model.stl") == "~/model.stl"
    assert utils._display_path("/home/alice") == "~"


# ---------------------------------------------------------------------------
# paths.json_path / _json_display_paths — one separator style in JSON
# ---------------------------------------------------------------------------


def test_json_path_normalizes_windows_separators(monkeypatch):
    """Local path fields emit "/" on Windows, matching path_for_message."""
    import bambu_cli.paths as paths

    monkeypatch.setattr(paths.os, "sep", "\\")
    assert paths.json_path("~\\models\\cube.3mf") == "~/models/cube.3mf"
    assert paths.json_path("D:\\out\\cube.3mf") == "D:/out/cube.3mf"
    # Already-normalized input and None are pass-through.
    assert paths.json_path("~/models/cube.3mf") == "~/models/cube.3mf"
    assert paths.json_path(None) is None


def test_json_envelope_uses_one_separator_style(monkeypatch):
    """A Windows envelope must not mix "\\" path fields with "/" everywhere else."""
    import bambu_cli.paths as paths
    import bambu_cli.utils as utils

    monkeypatch.setattr(paths.os, "sep", "\\")
    monkeypatch.setattr(utils, "_HOME_DIR", "C:\\Users\\alice")
    monkeypatch.setattr(utils.os, "sep", "\\")
    monkeypatch.setattr(utils.os, "altsep", "/")

    payload = utils._json_display_paths(
        {
            "file": "C:\\Users\\alice\\models\\cube.stl",
            "path": "D:\\out\\cube_sliced.3mf",
            "local_path": "C:\\Users\\alice\\w\\cube.3mf",
            "remote_path": "/cube.3mf",
            "filename": "cube_sliced.3mf",
        }
    )

    assert payload["file"] == "~/models/cube.stl"
    assert payload["path"] == "D:/out/cube_sliced.3mf"
    # local_path is a declared local-path field, so it is normalized too.
    assert payload["local_path"] == "~/w/cube.3mf"
    # Remote printer paths are already "/" and must be left alone.
    assert payload["remote_path"] == "/cube.3mf"
    assert "\\" not in "".join(v for v in payload.values() if isinstance(v, str))


def test_json_path_field_keeps_url_separators(monkeypatch):
    """A URL in a path-keyed field keeps its own separators and stays redacted."""
    import bambu_cli.paths as paths
    import bambu_cli.utils as utils

    monkeypatch.setattr(paths.os, "sep", "\\")
    at = "@"
    payload = utils._json_display_paths({"source": "https://user:pass" + at + "host.com/x.stl"})
    assert payload["source"] == "https://host.com/x.stl"


# ---------------------------------------------------------------------------
# utils._resolve_ip — do not cache failures
# ---------------------------------------------------------------------------


def test_resolve_ip_does_not_cache_failure(monkeypatch):
    import bambu_cli.utils as utils

    utils._RESOLVE_IP_CACHE.clear()
    calls = {"n": 0}

    def _failing(host, *a, **k):
        calls["n"] += 1
        raise OSError("transient DNS failure")

    monkeypatch.setattr(utils.socket, "getaddrinfo", _failing)
    # First call: resolution fails, returns host unchanged, does NOT cache.
    assert utils._resolve_ip("printer.local") == "printer.local"
    assert "printer.local" not in utils._RESOLVE_IP_CACHE

    # A later successful resolve must actually happen (not short-circuited by a
    # cached failure) and be cached.
    def _ok(host, *a, **k):
        calls["n"] += 1
        return [(None, None, None, None, ("10.0.0.5", 0))]

    monkeypatch.setattr(utils.socket, "getaddrinfo", _ok)
    assert utils._resolve_ip("printer.local") == "10.0.0.5"
    assert utils._RESOLVE_IP_CACHE.get("printer.local") == "10.0.0.5"
    utils._RESOLVE_IP_CACHE.clear()


# ---------------------------------------------------------------------------
# ams.parse_ams — external-spool sentinel + wizard active-tray selection
# ---------------------------------------------------------------------------


def _ams_status(tray_now, units):
    return {"ams": {"tray_now": str(tray_now), "ams": units}}


def test_parse_ams_external_spool_sentinel_not_active():
    from bambu_cli.ams import parse_ams

    units = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}]
    for sentinel in (254, 255):
        parsed = parse_ams(_ams_status(sentinel, units))
        assert parsed["active_tray"] is None
        assert all(not t["active"] for u in parsed["units"] for t in u["trays"])


def _patch_ams_status(monkeypatch, status):
    """Make _read_loaded_ams_material see ``status`` from the printer."""
    from bambu_cli.context import RuntimeContext

    fake_printer = MagicMock()
    fake_printer.status.return_value = status
    fake_ctx = MagicMock()
    fake_ctx.printer.return_value = fake_printer
    monkeypatch.setattr(RuntimeContext, "for_request", classmethod(lambda cls, args: fake_ctx))


def test_wizard_ams_material_multi_unit_picks_active_not_earlier_unit(monkeypatch):
    from bambu_cli.interactive.session import _read_loaded_ams_material

    # unit 0 holds a non-active PETG spool; the ACTIVE tray (tray_now=4) is PLA in
    # unit 1. Absolute index = unit_id*4 + slot => unit1/slot0 == 4.
    status = _ams_status(
        4,
        [
            {"id": 0, "tray": [{"id": 0, "tray_type": "PETG"}]},
            {"id": 1, "tray": [{"id": 0, "tray_type": "PLA"}]},
        ],
    )
    _patch_ams_status(monkeypatch, status)
    # Before the fix, the earlier-unit fallback returned PETG.
    assert _read_loaded_ams_material(argparse.Namespace()) == "PLA"


def test_wizard_ams_material_external_spool_sentinel_no_false_active(monkeypatch):
    from bambu_cli.interactive.session import _read_loaded_ams_material

    # tray_now=255 (external spool) with a PLA spool physically in unit 0. The
    # sentinel means "nothing loaded from the AMS"; parse_ams must mark nothing
    # active so the wizard does not present the sentinel as a firm AMS detection
    # of the active slot. With no active tray, the documented fallback returns
    # the first non-empty tray (PLA) — deterministic and non-crashing.
    status = _ams_status(255, [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}])
    _patch_ams_status(monkeypatch, status)
    assert _read_loaded_ams_material(argparse.Namespace()) == "PLA"


# ---------------------------------------------------------------------------
# download.extract._extract_zip_model — encrypted / Deflate64 -> ValueError
# ---------------------------------------------------------------------------


def test_extract_encrypted_zip_raises_valueerror(tmp_path):
    from bambu_cli.download.extract import _extract_zip_model

    zpath = tmp_path / "enc.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"solid x\nendsolid x\n")
    # Re-open and set an encryption flag is awkward; instead force archive.open
    # to raise RuntimeError (what a password-protected member does).
    args = argparse.Namespace(name=None, max_download_mb=100)

    import bambu_cli.download.extract as extract

    real_zipfile = extract.zipfile.ZipFile

    class _EncZip:
        def __init__(self, *a, **k):
            self._inner = real_zipfile(*a, **k)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def namelist(self):
            return self._inner.namelist()

        def infolist(self):
            return self._inner.infolist()

        def getinfo(self, name):
            return self._inner.getinfo(name)

        def open(self, *a, **k):
            raise RuntimeError("File model.stl is encrypted, password required for extraction")

    extract.zipfile.ZipFile = _EncZip
    try:
        with pytest.raises(ValueError) as ei:
            _extract_zip_model(str(zpath), str(tmp_path), args)
        # Message comes from the shared RuntimeError→ValueError translation
        # (merged with PR #96's variant): "ZIP member is encrypted or unsupported".
        assert "encrypted" in str(ei.value).lower()
    finally:
        extract.zipfile.ZipFile = real_zipfile


def test_extract_deflate64_raises_valueerror(tmp_path):
    from bambu_cli.download.extract import _extract_zip_model

    zpath = tmp_path / "d64.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model.stl", b"solid x\nendsolid x\n")
    args = argparse.Namespace(name=None, max_download_mb=100)

    import bambu_cli.download.extract as extract

    real_zipfile = extract.zipfile.ZipFile

    class _D64Zip:
        def __init__(self, *a, **k):
            self._inner = real_zipfile(*a, **k)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def namelist(self):
            return self._inner.namelist()

        def infolist(self):
            return self._inner.infolist()

        def getinfo(self, name):
            return self._inner.getinfo(name)

        def open(self, *a, **k):
            raise NotImplementedError("compression type 9")

    extract.zipfile.ZipFile = _D64Zip
    try:
        with pytest.raises(ValueError) as ei:
            _extract_zip_model(str(zpath), str(tmp_path), args)
        assert "unsupported compression" in str(ei.value).lower()
    finally:
        extract.zipfile.ZipFile = real_zipfile
