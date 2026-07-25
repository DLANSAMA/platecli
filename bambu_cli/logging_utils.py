"""Process-wide logger used by production modules.

``LoggerProxy`` always delegates to a replaceable backend so tests can install
a ``MagicMock`` (via ``set_logger`` / the ``mock_bambu_logger`` fixture) without
patching ``bambu_cli.bambu.logger`` or each consumer module's import binding.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any

_BACKEND: Any = logging.getLogger("bambu")


class LoggerProxy:
    """Attribute-forwarding proxy to the current process logger backend.

    Only ``__getattr__`` is forwarded so ``patch.object(logger, "warning")`` and
    attribute assignment (e.g. ``logger.propagate = False`` in setup_logging)
    stay on the proxy instance and do not rebind the real logging.Logger.
    """

    def __getattr__(self, name: str) -> Any:
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


def safe_log_error(message: Any, **kwargs: Any) -> None:
    """Log an error without ever letting the logging layer abort the process.

    The --json contract (README "Built for AI agents") promises a parseable envelope on
    every run. A handler that raises while formatting a user-controlled string (rich
    markup, encoding, a broken stream) must not be able to swallow that envelope, so
    failures here degrade to a bare stderr write.

    Only ``Exception`` is caught, so ``KeyboardInterrupt``/``SystemExit`` still propagate.
    """
    try:
        logger.error(message, **kwargs)
    except Exception:
        # Logging must never be fatal; fall back to the rawest possible write.
        try:
            print(f"ERROR: {message}", file=sys.stderr)
        except Exception:
            pass
