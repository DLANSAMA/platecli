"""Regression tests for BambuPrinter.upload_file's verified state machine.

Covers the (fresh|probe) -> (resume|restart) -> transfer -> verify phases,
including the fix for the stale-file shortcut (a same-size remote file must
not be trusted as "success" unless this run actually attempted a transfer).
"""

import ftplib
import os

import pytest

from bambu_cli.printer import BambuPrinter


class FakeFTP:
    """Records commands/args; failures and sizes are scripted per-call."""

    def __init__(self, size_script=None, storbinary_script=None):
        self.calls = []
        self.deleted = []
        self.stor_calls = []
        # size_script / storbinary_script: list of values/exceptions consumed
        # in order; a callable is invoked with no args, an Exception instance
        # (or class) is raised, anything else is returned as-is.
        self._size_script = list(size_script or [])
        self._stor_script = list(storbinary_script or [])

    def _consume(self, script, default):
        if not script:
            return default
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("scripted failure")
        return item

    def delete(self, path):
        self.deleted.append(path)

    def size(self, path):
        self.calls.append(("size", path))
        return self._consume(self._size_script, None)

    def storbinary(self, command, fp, blocksize=8192, rest=None, callback=None):
        _, _, remote_path = command.partition(" ")
        offset = fp.tell()
        self.stor_calls.append({"remote_path": remote_path, "rest": rest, "offset": offset})
        self.calls.append(("storbinary", remote_path, rest))
        result = self._consume(self._stor_script, None)
        if callback:
            fp.seek(0, os.SEEK_END)
            fp.seek(offset)
            data = fp.read()
            if data:
                callback(data)
        return result

    def quit(self):
        pass

    def close(self):
        pass


class FakeFTPFactory:
    """Yields a sequence of FakeFTP instances, one per get_ftp_client() call.

    If exhausted, keeps returning the last instance (mirrors a printer that
    stays reachable across many retries without needing an explicit entry
    for every single connection attempt).
    """

    def __init__(self, ftps):
        self._ftps = list(ftps)
        self._last = None
        self.instances = []

    def __call__(self, printer, timeout=60):
        if self._ftps:
            self._last = self._ftps.pop(0)
        self.instances.append(self._last)
        return self._last


def make_printer():
    return BambuPrinter(ip="127.0.0.1", serial="SN", access_code="12345678")


@pytest.fixture
def local_file(tmp_path):
    def _make(size=2048, byte=b"\xab"):
        path = tmp_path / "job.gcode"
        path.write_bytes(byte * size)
        return str(path)

    return _make


@pytest.fixture
def sleeps():
    calls = []

    def _sleep(delay):
        calls.append(delay)

    return calls, _sleep


def _patch_ftp(monkeypatch, factory):
    monkeypatch.setattr("bambu_cli.protocols.ftps._create_raw_ftp", factory)


def test_clean_success_with_size_verified(monkeypatch, local_file):
    path = local_file(2048)
    ftp = FakeFTP(size_script=[2048])
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode") is True
    assert ftp.deleted == ["/model/job.gcode"]
    assert len(ftp.stor_calls) == 1
    assert ftp.stor_calls[0]["rest"] is None


def test_size_mismatch_then_retry_succeeds(monkeypatch, local_file, sleeps):
    calls, sleep = sleeps
    path = local_file(2048)
    # First attempt "succeeds" (no exception) but verify shows a mismatch;
    # second attempt verifies correctly.
    ftp = FakeFTP(size_script=[1024, 2048])
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is True
    assert len(ftp.stor_calls) == 2
    # Mismatch (1024 < 2048) resumes from the reported offset.
    assert ftp.stor_calls[1]["rest"] == 1024
    assert calls  # backoff sleep was invoked


def test_size_raises_after_success_accepts_with_warning(monkeypatch, local_file, caplog):
    path = local_file(2048)
    ftp = FakeFTP(size_script=[ftplib.error_perm("550 SIZE not supported")])
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    with caplog.at_level("WARNING", logger="bambu.printer"):
        result = printer.upload_file(path, "/model/job.gcode")
    assert result is True
    assert any("Could not verify remote size" in r.message for r in caplog.records)


def test_mid_transfer_failure_resumes_from_probed_offset(monkeypatch, local_file, sleeps):
    calls, sleep = sleeps
    path = local_file(2048)
    ftp_fail = FakeFTP(size_script=[1024], storbinary_script=[OSError("connection reset")])
    ftp_probe = ftp_fail  # probe reuses same connection type/instance in this scenario
    ftp_ok = FakeFTP(size_script=[2048])
    factory = FakeFTPFactory([ftp_fail, ftp_probe, ftp_ok])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is True
    assert ftp_ok.stor_calls[0]["rest"] == 1024
    assert ftp_ok.stor_calls[0]["offset"] == 1024
    assert calls


def test_stale_same_size_file_without_transfer_is_not_shortcut(monkeypatch, local_file, sleeps):
    """A same-size remote file must not be trusted unless a STOR was attempted
    this run. Here the very first connection fails before any STOR call, so
    the probe seeing remote==local must trigger a restart, not success."""
    calls, sleep = sleeps
    path = local_file(2048)

    class DeadOnEnterFTP(FakeFTP):
        pass

    # get_ftp_client's __enter__ succeeds (FakeFTP itself), but we simulate a
    # failure occurring inside the `with` block before storbinary executes by
    # raising directly from storbinary on the very first attempt, without any
    # bytes ever having been sent — attempted_transfer is still True in that
    # case in the real implementation (set right before the call), so to test
    # the true "no transfer attempted yet" path we fail on `open`/delete phase
    # instead: simulate by making the *first* FTP's delete raise, and the
    # probe (second connection) show a stale same-size file, while storbinary
    # never even ran because get_ftp_client's __enter__ itself blew up.
    class BrokenConnectFactory:
        def __init__(self, probe_ftp, real_ftp):
            self.calls = 0
            self.probe_ftp = probe_ftp
            self.real_ftp = real_ftp

        def __call__(self, printer, timeout=60):
            self.calls += 1
            if self.calls == 1:
                raise OSError("connection refused")
            if self.calls == 2:
                return self.probe_ftp
            return self.real_ftp

    probe_ftp = FakeFTP(size_script=[2048])  # stale file already at full size
    real_ftp = FakeFTP(size_script=[2048])
    factory = BrokenConnectFactory(probe_ftp, real_ftp)
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is True
    # The stale file must have been deleted (restart from zero), and the real
    # transfer must upload from the beginning, not shortcut via probe.
    assert probe_ftp.deleted == ["/model/job.gcode"]
    assert real_ftp.stor_calls[0]["rest"] is None
    assert real_ftp.stor_calls[0]["offset"] == 0


def test_stale_same_size_after_real_transfer_attempt_is_trusted(monkeypatch, local_file, sleeps):
    """Once a STOR was actually attempted this run, a same-size probe result
    on retry is trusted as verified success (no need to re-transfer)."""
    calls, sleep = sleeps
    path = local_file(2048)
    ftp_fail = FakeFTP(size_script=[2048], storbinary_script=[OSError("reset")])
    factory = FakeFTPFactory([ftp_fail, ftp_fail])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is True
    # Only one STOR attempt happened; the probe's size match ended the retry loop.
    assert len(ftp_fail.stor_calls) == 1


def test_remote_larger_than_local_restarts_from_zero(monkeypatch, local_file, sleeps):
    calls, sleep = sleeps
    path = local_file(1024)
    ftp_fail = FakeFTP(size_script=[4096], storbinary_script=[OSError("reset")])
    ftp_ok = FakeFTP(size_script=[1024])
    factory = FakeFTPFactory([ftp_fail, ftp_fail, ftp_ok])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is True
    assert "/model/job.gcode" in ftp_fail.deleted
    assert ftp_ok.stor_calls[0]["rest"] is None
    assert ftp_ok.stor_calls[0]["offset"] == 0


def test_auth_error_530_fails_immediately_with_access_code_message(monkeypatch, local_file, caplog):
    path = local_file(1024)
    ftp = FakeFTP(storbinary_script=[ftplib.error_perm("530 Login incorrect")])
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    with caplog.at_level("ERROR", logger="bambu.printer"):
        result = printer.upload_file(path, "/model/job.gcode")
    assert result is False
    assert ftp.stor_calls  # only one attempt made
    assert len(ftp.stor_calls) == 1
    assert any("access code" in r.message for r in caplog.records)


def test_other_permanent_error_550_fails_immediately(monkeypatch, local_file):
    path = local_file(1024)
    ftp = FakeFTP(storbinary_script=[ftplib.error_perm("550 Permission denied")])
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode") is False
    assert len(ftp.stor_calls) == 1


def test_retries_exhausted_returns_false(monkeypatch, local_file, sleeps):
    calls, sleep = sleeps
    path = local_file(1024)
    ftp = FakeFTP(
        size_script=[OSError("no size")] * 10,
        storbinary_script=[OSError("fail")] * 10,
    )
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    printer = make_printer()
    assert printer.upload_file(path, "/model/job.gcode", sleep=sleep) is False
    # 1 initial attempt + 3 retries = 4 storbinary calls total
    assert len(ftp.stor_calls) == 4
    assert len(calls) == 3


def test_backoff_delays_increase_and_no_real_sleep(monkeypatch, local_file):
    path = local_file(1024)
    ftp = FakeFTP(
        size_script=[OSError("no size")] * 10,
        storbinary_script=[OSError("fail")] * 10,
    )
    factory = FakeFTPFactory([ftp])
    _patch_ftp(monkeypatch, factory)

    recorded = []
    printer = make_printer()
    # Remove jitter for a deterministic increasing-delay assertion.
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    assert printer.upload_file(path, "/model/job.gcode", sleep=recorded.append) is False
    assert recorded == sorted(recorded)
    assert len(recorded) == 3
    assert recorded[0] < recorded[1] < recorded[2]
