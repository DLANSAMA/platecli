"""Shared pytest fixtures for the platecli suite."""

import pytest

from tests.bambu_test_base import install_baseline_context


@pytest.fixture(autouse=True)
def _no_network_throttle():
    """Zero the per-host politeness delay and clear its state for every test.

    ``bambu_cli.netsafety._throttle_host`` sleeps up to
    MIN_HOST_REQUEST_INTERVAL between requests to the same host. Real sleeps in
    the suite would be slow AND order-dependent (``_last_request_at`` is
    module-level mutable state shared across tests), so pin the interval to 0
    and clear the map around every test. Tests that exercise the throttle
    itself must monkeypatch the interval back up locally.
    """
    from bambu_cli import netsafety

    saved = netsafety.MIN_HOST_REQUEST_INTERVAL
    netsafety.MIN_HOST_REQUEST_INTERVAL = 0.0
    netsafety._last_request_at.clear()
    yield
    netsafety.MIN_HOST_REQUEST_INTERVAL = saved
    netsafety._last_request_at.clear()


@pytest.fixture(autouse=True)
def _reset_runtime_context():
    """Isolate the process-wide RuntimeContext between tests.

    Config state lives on the installed RuntimeContext; reset it to the shared
    baseline around every test so a context installed by one test (e.g. via
    ``main()`` or ``set_current``) can't leak into the next.
    """
    install_baseline_context()
    yield
    install_baseline_context()


@pytest.fixture(autouse=True)
def _restore_sim_ftp_files():
    """Snapshot and restore the module-level _SIM_FTP_FILES dict around every test.

    Several tests (e.g. test_execute_print_simulation_ok) mutate this dict
    directly via _SIM_FTP_FILES["key"] = value. Without a guard the mutation
    persists for the rest of the process, making FTP-listing assertions
    order-dependent.
    """
    from bambu_cli.protocols import ftps

    snapshot = dict(ftps._SIM_FTP_FILES)
    yield
    ftps._SIM_FTP_FILES.clear()
    ftps._SIM_FTP_FILES.update(snapshot)
