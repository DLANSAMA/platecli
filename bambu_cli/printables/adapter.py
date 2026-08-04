"""The Printables containment boundary.

Printables' API is undocumented, unversioned, and scraped. The guarantee this
module makes is narrow and load-bearing:

    **No exception raised while talking to Printables escapes the adapter.**

Not a ``PrintablesError``, not a ``KeyError`` from a renamed field, not an
``AttributeError`` from a null envelope, not a ``MemoryError`` from a huge
body. ``resolve()`` always returns a :class:`PrintablesResolution`. If
Printables changes their schema tomorrow, ``plate download`` and ``plate job``
report a clean, specific failure and keep working for every other source —
which is the whole point of the adapter.

The one thing that *is* allowed to propagate is ``KeyboardInterrupt`` /
``SystemExit``: swallowing those would make Ctrl-C stop working.
"""

from __future__ import annotations

from dataclasses import dataclass

from bambu_cli.logging_utils import logger
from bambu_cli.printables import client
from bambu_cli.printables.errors import PrintablesContractChanged, PrintablesError


@dataclass(frozen=True)
class PrintablesResolution:
    """The outcome of resolving one Printables model URL.

    ``ok`` resolutions carry ``url``/``filename``. Failures carry a ``reason``
    token, a human ``error`` message, and a ``remedy`` — enough for a caller to
    build a JSON envelope or print a useful line without knowing anything about
    GraphQL.
    """

    ok: bool
    url: str | None = None
    filename: str | None = None
    reason: str | None = None
    error: str | None = None
    remedy: str | None = None

    def as_tuple(self):
        """``(url, filename)`` — the legacy shape callers already handle."""
        return (self.url, self.filename) if self.ok else (None, None)


class PrintablesAdapter:
    """Resolves printables.com model pages to a direct file URL.

    Collaborators are injected rather than imported at call time so tests can
    drive the whole surface without patching module globals:

    * ``opener_factory`` — builds the SSRF-guarded urllib opener
    * ``headers_factory`` — builds the outbound headers
    """

    def __init__(self, opener_factory=None, headers_factory=None):
        self._opener_factory = opener_factory or client.default_opener
        self._headers_factory = headers_factory or client.gql_headers

    @staticmethod
    def handles(url):
        """True if this adapter recognises *url*. Never raises."""
        try:
            return client.is_printables_model_url(url)
        except Exception:  # pragma: no cover -- defensive; is_printables_model_url guards internally
            return False

    def resolve(self, url):
        """Resolve *url*, returning a :class:`PrintablesResolution`.

        Never raises for a Printables-side problem. See the module docstring.
        """
        if not self.handles(url):
            return PrintablesResolution(
                ok=False,
                reason="not_a_printables_url",
                error="Not a Printables model URL.",
                remedy="Pass a printables.com/model/<id> URL.",
            )

        try:
            return self._resolve_inner(url)
        except PrintablesError as exc:
            return self._failure(exc.reason, str(exc), exc.remedy)
        except (KeyboardInterrupt, SystemExit):
            # Never swallow process control flow.
            raise
        except BaseException as exc:  # noqa: BLE001 -- the containment boundary is the point
            # Anything unanticipated is, by definition, the API not matching what
            # this adapter was written against. Report it as a contract change
            # rather than letting a bare KeyError escape into the download path.
            return self._failure(
                PrintablesContractChanged.reason,
                f"Unexpected failure talking to Printables ({type(exc).__name__}: {exc}).",
                PrintablesContractChanged.remedy,
            )

    def _resolve_inner(self, url):
        model_id = client.model_id_from_url(url)
        if model_id is None:  # pragma: no cover -- handles() already established the shape
            raise PrintablesContractChanged("Could not read a model id from a URL that looked like one.")

        logger.info(f"🔍 Detected Printables model #{model_id}, resolving files...")
        headers = self._headers_factory()
        opener = self._opener_factory()

        file_id, file_type, file_name = client.get_file_info(model_id, headers, opener)
        link, name = client.get_download_link(file_id, model_id, file_type, file_name, headers, opener)
        return PrintablesResolution(ok=True, url=link, filename=name)

    @staticmethod
    def _failure(reason, message, remedy):
        logger.error(message)
        if remedy:
            logger.info(f"   {remedy}")
        return PrintablesResolution(ok=False, reason=reason, error=message, remedy=remedy)
