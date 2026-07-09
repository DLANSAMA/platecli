"""Injectable job step factories and JobSteps dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


def _default_download():
    from bambu_cli.commands import cmd_download

    return cmd_download


def _default_slice():
    from bambu_cli.commands import cmd_slice

    return cmd_slice


def _default_upload():
    from bambu_cli.commands import cmd_upload

    return cmd_upload


def _default_print():
    from bambu_cli.commands import cmd_print

    return cmd_print


@dataclass
class JobSteps:
    """Injectable step callables for the job/send orchestrator.

    Each field defaults to a zero-arg factory that late-binds to the real
    command handlers on ``bambu_cli.commands`` at call time. Tests inject a
    custom ``JobSteps`` (or patch ``bambu_cli.commands.cmd_*``) rather than
    patching the former ``bambu_cli.bambu`` facade.
    """

    download: Callable | None = None
    slice: Callable | None = None
    upload: Callable | None = None
    print_: Callable | None = None

    def _resolve(self, value, default_factory):
        return value if value is not None else default_factory()

    def get_download(self):
        return self._resolve(self.download, _default_download)

    def get_slice(self):
        return self._resolve(self.slice, _default_slice)

    def get_upload(self):
        return self._resolve(self.upload, _default_upload)

    def get_print(self):
        return self._resolve(self.print_, _default_print)
