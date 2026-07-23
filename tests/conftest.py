"""Shared pytest fixtures for the platecli suite."""

import pytest

from tests.bambu_test_base import install_baseline_context


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
