"""Injectable job step callables for the job/send orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bambu_cli.errors import BambuError


class MissingJobStep(BambuError):
    """A pipeline stage was reached with no callable supplied for it.

    Raised rather than silently importing a default, so a miswired caller fails
    loudly at the boundary instead of dragging the command layer into ``job``.
    """


@dataclass
class JobSteps:
    """The four callables the job/send orchestrator drives.

    ``job`` is an orchestrator: it sequences download -> slice -> upload ->
    print, but it must not know *who* implements those. The handlers live in
    ``bambu_cli.commands`` (a higher layer), so this dataclass previously
    late-bound to them through function-local imports — an upward dependency
    that ``scripts/check_layers.py`` rejects.

    The caller now supplies them. ``bambu_cli.commands.cmd_job`` is the
    composition root and wires the real handlers; tests pass fakes. A field left
    as ``None`` is not defaulted — reaching that stage raises ``MissingJobStep``.
    """

    download: Callable | None = None
    slice: Callable | None = None
    upload: Callable | None = None
    print_: Callable | None = None

    def _resolve(self, value, name):
        if value is None:
            raise MissingJobStep(
                f"the job orchestrator reached the {name!r} step but no {name!r} callable was supplied to JobSteps"
            )
        return value

    def get_download(self):
        return self._resolve(self.download, "download")

    def get_slice(self):
        return self._resolve(self.slice, "slice")

    def get_upload(self):
        return self._resolve(self.upload, "upload")

    def get_print(self):
        return self._resolve(self.print_, "print")
