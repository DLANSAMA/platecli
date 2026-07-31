"""Injectable collaborators for the TUI (prompts-free analog of ``GoDeps``).

The Textual app receives a ``TuiDeps`` carrying the pipeline seams and the
status provider, exactly as the wizard receives a ``GoDeps``. Tests pass a
``TuiDeps`` with scripted fakes so pilot tests never touch real MQTT, never
download, and never shell out to a slicer.

``steps`` is the very same ``GoSteps`` the ``plate go`` wizard uses (defined in
``bambu_cli.interactive.core``), so both front-ends inject at one seam; the
prepare screen reaches it through ``get_pipeline()`` / ``get_ams_detector()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bambu_cli.tui.services import MonitorService, PipelineService, StatusService

if TYPE_CHECKING:
    from bambu_cli.interactive.core import GoSteps


def _default_steps() -> GoSteps:
    from bambu_cli.interactive.core import GoSteps

    return GoSteps()


@dataclass
class TuiDeps:
    """Everything the Textual app needs, injectable for tests.

    ``status_provider`` fetches a normalized status snapshot (see
    ``StatusService.fetch``); tests substitute a fake with scripted results.
    ``steps`` is the shared ``GoSteps`` pipeline seam; ``pipeline`` and
    ``ams_detector`` default to adapters built on top of it, and can be replaced
    wholesale by a pilot test that wants scripted prepare results. The monitor
    polls through ``monitor_service`` at ``poll_interval`` seconds.
    """

    status_provider: Any = None
    steps: Any = field(default=None)
    pipeline: Any = field(default=None)
    ams_detector: Any = field(default=None)
    monitor_service: Any = field(default=None)
    # Seconds between monitor polls. Injected so pilot tests never sleep.
    poll_interval: float = 3.0

    def get_status_provider(self) -> Any:
        if self.status_provider is not None:
            return self.status_provider
        return StatusService()

    def get_steps(self) -> Any:
        if self.steps is not None:
            return self.steps
        return _default_steps()

    def get_pipeline(self) -> Any:
        if self.pipeline is not None:
            return self.pipeline
        return PipelineService(steps=self.get_steps())

    def get_monitor_service(self) -> Any:
        if self.monitor_service is not None:
            return self.monitor_service
        return MonitorService(self.get_status_provider())

    def get_poll_interval(self) -> float:
        return self.poll_interval

    def get_ams_detector(self) -> Any:
        if self.ams_detector is not None:
            return self.ams_detector
        # Same seam the wizard uses: GoSteps decides between the real
        # best-effort AMS read and whatever a test injected.
        return self.get_steps().get_ams_material()
