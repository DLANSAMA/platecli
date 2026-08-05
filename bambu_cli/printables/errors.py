"""Typed failures for the Printables adapter.

Printables' GraphQL API is undocumented and unversioned: the schema, the error
envelope, and the file lists can all change without notice, and a scraped
integration is the first thing to break when they do. These types let the
adapter say *which* kind of breakage happened, so the CLI can tell a user
"Printables changed their API" instead of a generic "download failed" — and so
a schema change never surfaces as a raw ``AttributeError`` traceback.

Every one of these is raised inside the adapter and converted to a
``PrintablesResolution`` before it reaches a caller. They are part of the
adapter's internal vocabulary, not its public contract; see ``adapter.py``.
"""

from __future__ import annotations

from bambu_cli.errors import BambuError


class PrintablesError(BambuError):
    """Base for every Printables-specific failure.

    Subclasses ``BambuError`` so that if one ever does escape the adapter it
    still lands on the CLI's normal error path with an exit code, rather than
    crashing as an unhandled exception.
    """

    #: Short, stable token for JSON envelopes / logs.
    reason = "printables_error"

    #: What the user should do about it.
    remedy = "Try the download again, or download the file from Printables manually."


class PrintablesUnavailable(PrintablesError):
    """The API could not be reached: DNS, TLS, timeout, connection reset, 5xx."""

    reason = "printables_unavailable"
    remedy = "Check your network connection and retry; Printables may also be down."


class PrintablesContractChanged(PrintablesError):
    """A response arrived but did not have the shape this adapter expects.

    This is the "they changed their API / DOM" case, and the reason the adapter
    exists. It is deliberately distinct from :class:`PrintablesUnavailable`:
    retrying will not help, and the fix is a code change here — not anywhere
    else in the tool.
    """

    reason = "printables_contract_changed"
    remedy = (
        "Printables appears to have changed their API. Download the file from the "
        "model page in a browser and pass the local path instead. Please report this "
        "so the Printables adapter can be updated."
    )


class PrintablesModelUnavailable(PrintablesError):
    """The API answered correctly, but this model has nothing we can print.

    A well-formed "no STL/STEP/3MF here" or "no such model" — the integration is
    working, the model just is not usable. Not a breakage.
    """

    reason = "printables_model_unavailable"
    remedy = "Pick a model that publishes an STL, STEP, or 3MF file."
