"""Printables.com integration, behind a strict adapter.

Printables has no public, documented, versioned API — platecli talks to their
GraphQL endpoint against an observed schema. That makes this the most likely
part of the tool to break through no fault of its own, so it is fenced off:

* ``client.py`` is the only module that knows the wire format. Nothing outside
  this package may import it.
* ``adapter.py`` guarantees that no Printables failure escapes as an exception.
  A schema change becomes a typed, reportable outcome — never a traceback in
  the middle of ``plate job``.
* This module is the entire public surface. Import from here.

The names below are what the rest of the codebase may use:

``is_printables_url(url)``
    Cheap URL test. Never raises, never touches the network.

``resolve_printables_url(url)``
    ``(download_url, filename)``, or ``(None, None)`` on any failure. The
    long-standing shape, kept so callers that only branch on "did it resolve"
    need no changes.

``PrintablesAdapter`` / ``PrintablesResolution``
    The richer surface: *why* a resolution failed, and what to tell the user.
"""

from __future__ import annotations

from bambu_cli.printables.adapter import PrintablesAdapter, PrintablesResolution
from bambu_cli.printables.errors import (
    PrintablesContractChanged,
    PrintablesError,
    PrintablesModelUnavailable,
    PrintablesUnavailable,
)

__all__ = [
    "PrintablesAdapter",
    "PrintablesContractChanged",
    "PrintablesError",
    "PrintablesModelUnavailable",
    "PrintablesResolution",
    "PrintablesUnavailable",
    "is_printables_url",
    "resolve_printables",
    "resolve_printables_url",
]


def is_printables_url(url):
    """True if *url* is a printables.com model page. Never raises."""
    return PrintablesAdapter.handles(url)


def resolve_printables(url, adapter=None):
    """Resolve *url*, returning a :class:`PrintablesResolution` with failure detail."""
    return (adapter or PrintablesAdapter()).resolve(url)


def resolve_printables_url(url, adapter=None):
    """Resolve *url* to ``(download_url, filename)``, or ``(None, None)``.

    Kept as the default entry point because every existing caller branches on
    ``if not resolved_url``. Use :func:`resolve_printables` when you want to
    report *why* it failed.
    """
    return resolve_printables(url, adapter=adapter).as_tuple()
