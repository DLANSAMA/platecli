"""Regression tests that exercise the SHIPPED package (`bambu_cli.*`), not the
legacy `scripts.bambu` copy that the main suite imports.

These pin four real bugs that shipped because CI only tested `scripts.bambu`:

  (a) Slicer must accept a valid .3mf when OrcaSlicer exits non-zero ONLY because
      of headless GL / thumbnail noise (`_benign_rc` near bambu_cli/slicer.py:585),
      but must still FAIL on a genuine error ("nothing to be sliced", no .3mf).
  (b) FTPS teardown must use close(), never the hanging quit()
      (bambu_cli/protocols/ftps.py ConnectionManager.clear / PooledFTPWrapper.__exit__).
  (c) The download success path must be able to resolve `_record_download_success`
      (it was a NameError) -- bambu_cli/bambu.py _cmd_download.
  (d) snapshot must prefer the direct camera grab and NOT shell out to Docker when
      a frame is obtained -- bambu_cli/bambu.py _cmd_snapshot + _grab_camera_frame_direct.
"""

import os
import sys
import types
import inspect
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# paho-mqtt is an optional/heavy dep; stub it the same way the main suite does so
# importing the package never fails on environments without it installed.
_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli import slicer  # noqa: E402
from bambu_cli.protocols import ftps  # noqa: E402


def _slice_args(tmpdir, infile):
    """A plain namespace with every attribute cmd_slice reads via getattr/args.x."""
    return types.SimpleNamespace(
        file=infile,
        output=tmpdir,
        quality="standard",
        copies=1,
        filament="PLA Basic",
        threads=None,
        infill=15,
        pattern="3dhoneycomb",
        nozzle_temp=220,
        bed_temp=60,
        supports=False,
        support_type=None,
        walls=None,
        json=False,
        sim=False,
    )


def _fake_popen_factory(returncode, stdout="", stderr=""):
    """Return a class that stands in for subprocess.Popen and yields the given result.

    cmd_slice now reads stdout/stderr via reader threads calling read1() on the
    raw byte pipes, so expose them as io.BytesIO streams plus poll()/wait()/kill().
    """
    import io

    class _FakePopen:
        def __init__(self, *a, **k):
            self.returncode = returncode
            self.stdout = io.BytesIO(stdout.encode("utf-8"))
            self.stderr = io.BytesIO(stderr.encode("utf-8"))

        def communicate(self, timeout=None):
            return stdout, stderr

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    return _FakePopen


def _write_profiles(tmpdir):
    paths = {}
    for nm in ("machine.json", "process.json", "filament.json"):
        p = os.path.join(tmpdir, nm)
        with open(p, "w") as fh:
            fh.write("{}")
        paths[nm] = p
    return paths


# ---------------------------------------------------------------------------
# (a) benign GL/thumbnail non-zero exit is treated as success; real errors fail
# ---------------------------------------------------------------------------

def test_a_benign_gl_noise_nonzero_is_success():
    """rc=1 with GLFW/skip-thumbnail noise + a valid non-empty .3mf -> returns path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        infile = os.path.join(tmpdir, "part.stl")
        with open(infile, "wb") as fh:
            fh.write(b"solid x\nendsolid x\n")
        outpath = slicer._sliced_output_path(infile, tmpdir, 1)
        with open(outpath, "wb") as fh:  # non-empty fake .3mf "produced" by Orca
            fh.write(b"PK\x03\x04" + b"\x00" * 4096)

        profiles = _write_profiles(tmpdir)
        tmp_proc = types.SimpleNamespace(name=profiles["process.json"])
        tmp_fil = types.SimpleNamespace(name=profiles["filament.json"])
        args = _slice_args(tmpdir, infile)

        stderr = "Failed to create GLFW window ... skip thumbnail"
        FakePopen = _fake_popen_factory(1, stdout="", stderr=stderr)

        # Force every os.path.exists check True: the profile paths cmd_slice
        # constructs under PROFILES_DIR don't exist on disk, and the produced
        # output .3mf (which we DID write) must read as present.
        with patch.object(slicer.subprocess, "Popen", FakePopen), \
             patch.object(slicer, "_slicer_executable_problem", return_value=None), \
             patch.object(slicer, "_create_temp_profiles", return_value=(tmp_proc, tmp_fil)), \
             patch.object(slicer, "_validate_slice_options", return_value=None), \
             patch.object(slicer.os.path, "exists", return_value=True), \
             patch.object(slicer.os.path, "getsize", return_value=4100), \
             patch.object(bambu, "ORCA_SLICER", "/usr/bin/true"), \
             patch.object(bambu, "PROFILES_DIR", tmpdir):
            result = slicer.cmd_slice(args)

        assert result == outpath, "benign GL-noise non-zero exit should be treated as success"


def test_a_real_error_still_fails():
    """rc=1 with 'nothing to be sliced' and no .3mf -> must sys.exit non-zero."""
    with tempfile.TemporaryDirectory() as tmpdir:
        infile = os.path.join(tmpdir, "part.stl")
        with open(infile, "wb") as fh:
            fh.write(b"solid x\nendsolid x\n")
        outpath = slicer._sliced_output_path(infile, tmpdir, 1)  # intentionally never written
        assert not os.path.exists(outpath)

        profiles = _write_profiles(tmpdir)
        tmp_proc = types.SimpleNamespace(name=profiles["process.json"])
        tmp_fil = types.SimpleNamespace(name=profiles["filament.json"])
        args = _slice_args(tmpdir, infile)

        FakePopen = _fake_popen_factory(1, stdout="", stderr="[error] nothing to be sliced")

        # Report every profile/input path as present, but the (absent) output .3mf
        # as missing -- so we reach the *real slicing error* branch, not a
        # profile-missing branch.
        def exists_side_effect(path):
            return path != outpath

        with patch.object(slicer.subprocess, "Popen", FakePopen), \
             patch.object(slicer, "_slicer_executable_problem", return_value=None), \
             patch.object(slicer, "_create_temp_profiles", return_value=(tmp_proc, tmp_fil)), \
             patch.object(slicer, "_validate_slice_options", return_value=None), \
             patch.object(slicer.os.path, "exists", side_effect=exists_side_effect), \
             patch.object(bambu, "ORCA_SLICER", "/usr/bin/true"), \
             patch.object(bambu, "PROFILES_DIR", tmpdir):
            with pytest.raises(SystemExit) as excinfo:
                slicer.cmd_slice(args)

        code = excinfo.value.code
        assert code not in (0, None), f"real slice error must exit non-zero, got {code!r}"


# ---------------------------------------------------------------------------
# (b) FTPS teardown uses close(), never quit()
# ---------------------------------------------------------------------------

class _RecordingFtp:
    """Fake ftp object that records which teardown methods were called."""

    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append("close")

    def quit(self):
        self.calls.append("quit")

    def voidcmd(self, *a, **k):
        self.calls.append("voidcmd")


def test_b_connection_manager_clear_uses_close_not_quit():
    mgr = ftps.ConnectionManager()
    fake = _RecordingFtp()
    mgr._ftp_client = fake
    mgr.clear()
    assert "close" in fake.calls, "clear() must close the FTP connection"
    assert "quit" not in fake.calls, "clear() must NOT call the hanging quit()"
    assert mgr._ftp_client is None


def test_b_pooled_wrapper_exit_on_error_uses_close_not_quit():
    mgr = ftps.ConnectionManager()
    fake = _RecordingFtp()
    mgr._ftp_client = fake
    wrapper = ftps.PooledFTPWrapper(fake, mgr)
    wrapper.__enter__()  # acquires usage lock
    wrapper.__exit__(RuntimeError, RuntimeError("boom"), None)
    assert "close" in fake.calls, "__exit__ on error must close the FTP connection"
    assert "quit" not in fake.calls, "__exit__ must NOT call the hanging quit()"


# ---------------------------------------------------------------------------
# (c) download path can resolve _record_download_success (was a NameError)
# ---------------------------------------------------------------------------

def test_c_record_download_success_importable():
    from bambu_cli.utils import _record_download_success
    assert callable(_record_download_success)


def test_c_cmd_download_references_record_download_success_without_nameerror():
    """The _cmd_download body must reference a *resolvable* name.

    The original bug was a bare NameError on `_record_download_success` at runtime.
    Assert (1) the symbol appears in the function source and (2) it is importable
    exactly the way the fix imports it (function-scope import)."""
    src = inspect.getsource(bambu._cmd_download)
    assert "_record_download_success" in src, "download path should call _record_download_success"
    ns = {}
    exec("from bambu_cli.utils import _record_download_success", ns)
    assert callable(ns["_record_download_success"])


# ---------------------------------------------------------------------------
# (d) snapshot prefers the direct camera grab and does NOT use Docker
# ---------------------------------------------------------------------------

def test_d_snapshot_uses_direct_grab_not_docker():
    jpeg = b"\xff\xd8" + b"\x00" * 256 + b"\xff\xd9"
    with tempfile.TemporaryDirectory() as tmpdir:
        outpath = os.path.join(tmpdir, "snap.jpg")
        args = types.SimpleNamespace(output=outpath, json=False)

        with patch.object(bambu, "_grab_camera_frame_direct", return_value=jpeg), \
             patch.object(bambu, "load_access_code", return_value="ACODE"), \
             patch.object(bambu, "PRINTER_IP", "192.168.1.50"), \
             patch.object(bambu.subprocess, "run") as mock_run, \
             patch.object(bambu.shutil, "which", return_value="/usr/bin/docker"):
            bambu._cmd_snapshot(args)

        # File written with the direct-grab bytes
        assert os.path.exists(outpath)
        with open(outpath, "rb") as fh:
            assert fh.read() == jpeg

        # And Docker was never invoked
        for call in mock_run.call_args_list:
            cmd = call.args[0] if call.args else call.kwargs.get("args", [])
            joined = " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd)
            assert "docker" not in joined.lower(), f"snapshot must not call docker, saw: {cmd!r}"
        assert mock_run.call_count == 0, "direct grab path must not shell out at all"


# ---------------------------------------------------------------------------
# Download hardening regressions (SSRF + size limits)
# ---------------------------------------------------------------------------

def test_get_safe_connection_blocks_private_ip():
    """DNS resolving to a private address must be refused (SSRF guard)."""
    import socket
    import urllib.error
    from bambu_cli import download

    addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.5", 80))]
    with patch.object(download.socket, "getaddrinfo", return_value=addr_info), \
         patch.object(bambu, "ALLOW_PRIVATE_IPS", False):
        download._dns_cache.clear()
        with pytest.raises(urllib.error.URLError):
            download._get_safe_connection("evil.example.com", 80, 5, None)
        download._dns_cache.clear()


def test_safe_opener_has_no_default_http_handlers():
    """Every hop (including redirects) must connect via the Safe* handlers, and
    environment proxies must be disabled so validation cannot be bypassed."""
    import urllib.request
    from bambu_cli import download

    opener = download.build_safe_opener()
    handler_types = [type(h) for h in opener.handlers]
    assert download.SafeHTTPHandler in handler_types
    assert download.SafeHTTPSHandler in handler_types
    # The stock handlers would bypass IP validation entirely.
    assert urllib.request.HTTPHandler not in handler_types
    assert urllib.request.HTTPSHandler not in handler_types
    # ProxyHandler({}) registers no *_open methods, so no proxy handler is
    # present at all — environment proxies must never route these requests.
    proxy_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    for handler in proxy_handlers:
        assert not handler.proxies


def test_download_enforces_size_limit_mid_stream(tmp_path):
    """A response with no Content-Length must still be cut off at the limit."""
    from bambu_cli import download
    from bambu_cli.constants import EXIT_FILE_ERROR

    url = "https://example.com/model.stl"
    mock_resp = MagicMock()
    mock_resp.read.side_effect = lambda n: b"x" * n  # endless stream
    mock_resp.getheader.return_value = None          # no Content-Length
    mock_resp.geturl.return_value = url
    mock_opener = MagicMock()
    mock_opener.open.return_value.__enter__.return_value = mock_resp

    args = types.SimpleNamespace(
        url=url, output=str(tmp_path), name=None, max_download_mb=1, json=False,
        progress=False)

    with patch.object(download, "build_safe_opener", return_value=mock_opener):
        with pytest.raises(SystemExit) as excinfo:
            download._cmd_download(args)

    assert excinfo.value.code == EXIT_FILE_ERROR
    leftovers = [p for p in tmp_path.iterdir() if p.stat().st_size > 0]
    assert not leftovers, f"partial download not cleaned up: {leftovers}"
