"""Process-wide logger used by production modules.

``LoggerProxy`` always delegates to a replaceable backend so tests can install
a ``MagicMock`` (via ``set_logger`` / the ``mock_bambu_logger`` fixture) without
patching ``bambu_cli.bambu.logger`` or each consumer module's import binding.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

_BACKEND: Any = logging.getLogger("bambu")


class LoggerProxy:
    """Attribute-forwarding proxy to the current process logger backend."""

    def __getattr__(self, name: str):
        return getattr(_BACKEND, name)


logger = LoggerProxy()


def set_logger(backend: Any) -> None:
    """Replace the process logger backend (tests pass a ``MagicMock``)."""
    global _BACKEND
    _BACKEND = backend


def reset_logger() -> None:
    """Restore the default ``logging.getLogger("bambu")`` backend."""
    global _BACKEND
    _BACKEND = logging.getLogger("bambu")


@contextmanager
def patched_logger(backend: Any | None = None):
    """Context manager that installs ``backend`` (default: ``MagicMock``) for the block."""
    from unittest.mock import MagicMock

    mock = backend if backend is not None else MagicMock()
    prev = _BACKEND
    set_logger(mock)
    try:
        yield mock
    finally:
        set_logger(prev)
