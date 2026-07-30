"""Injectable collaborators for the TUI (prompts-free analog of ``GoDeps``).

The Textual app receives a ``TuiDeps`` carrying the pipeline seams and the
status provider, exactly as the wizard receives a ``GoDeps``. Tests pass a
``TuiDeps`` with scripted fakes so pilot tests never touch real MQTT.

Phase 1 only needs a status provider (``StatusService``); the ``GoSteps``
pipeline seam is carried through now so Phase 2/3 screens have somewhere to
plug in without changing the app's constructor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bambu_cli.tui.services import StatusService

if TYPE_CHECKING:
    from bambu_cli.interactive.session import GoSteps


def _default_steps() -> GoSteps:
    from bambu_cli.interactive.session import GoSteps

    return GoSteps()


@dataclass
class TuiDeps:
    """Everything the Textual app needs, injectable for tests.

    ``status_provider`` fetches a normalized status snapshot (see
    ``StatusService.fetch``); tests substitute a fake with scripted results.
    ``steps`` is the shared ``GoSteps`` pipeline seam, reserved for later
    phases' prepare/print flow.
    """

    status_provider: Any = None
    steps: Any = field(default=None)

    def get_status_provider(self) -> Any:
        if self.status_provider is not None:
            return self.status_provider
        return StatusService()

    def get_steps(self) -> Any:
        if self.steps is not None:
            return self.steps
        return _default_steps()
