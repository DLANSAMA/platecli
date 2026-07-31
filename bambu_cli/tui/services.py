"""Thin sync adapters between the Textual view layer and the domain.

Screens and widgets hold *no* domain logic (plan §4.1 view/logic split): every
decision — how to reach the printer, how to normalize AMS data, how to describe
a failure — lives here in plain synchronous functions/dataclasses that are
unit-tested without a Textual pilot. The screens call these from thread workers
(``printer.status()`` blocks on a ``threading.Event``) and render the result.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StatusSnapshot:
    """A normalized, view-ready printer status snapshot.

    ``ok`` is False when the fetch failed (printer unreachable, timeout, etc.);
    ``error`` then carries a short human message and ``raw``/``ams`` are empty.
    This dataclass is what the dashboard renders, so a failure is an ordinary
    value the UI shows inline — it never surfaces as an exception that could
    crash the app.
    """

    ok: bool
    raw: dict[str, Any] = field(default_factory=dict)
    ams: dict[str, Any] | None = None
    error: str | None = None

    @property
    def gcode_state(self) -> str:
        return str(self.raw.get("gcode_state", "UNKNOWN"))


class StatusService:
    """Fetch a normalized status snapshot from the configured/simulated printer.

    Wraps ``RuntimeContext.for_request(args).printer().status()`` and
    ``parse_ams``. NEVER raises: any failure (MQTT error, timeout, missing
    config) is captured into ``StatusSnapshot(ok=False, error=...)`` so the
    dashboard renders an inline "printer unreachable" state instead of crashing.
    """

    def fetch(self, args: argparse.Namespace) -> StatusSnapshot:
        try:
            from bambu_cli.ams import parse_ams
            from bambu_cli.context import RuntimeContext

            ctx = RuntimeContext.for_request(args)
            data = ctx.printer().status()
            if not isinstance(data, dict) or not data:
                return StatusSnapshot(ok=False, error="Printer returned no status.")
            ams = parse_ams(data)
            return StatusSnapshot(ok=True, raw=data, ams=ams)
        except Exception as exc:  # noqa: BLE001 -- see class docstring: never crash the UI
            return StatusSnapshot(ok=False, error=_short_error(exc))


def _short_error(exc: Exception) -> str:
    """Render an exception as a single short line for the unreachable state."""
    message = str(exc).strip()
    if message:
        return message
    return f"Printer unreachable ({type(exc).__name__})."


def _prepare_error(exc: Exception) -> str:
    """Render a prepare-pipeline failure — never as a connectivity diagnosis.

    A message-less ``OSError`` out of the slicer is a prepare failure, not a
    printer that cannot be reached, so this does not share the status path's
    "Printer unreachable" fallback wording.
    """
    message = str(exc).strip()
    if message:
        return message
    return f"Preparing the model failed ({type(exc).__name__})."


# --- View-model helpers (pure; unit-tested without a pilot) -----------------


def status_lines(snapshot: StatusSnapshot) -> list[tuple[str, str]]:
    """Return ``(label, value)`` rows describing the printer status panel.

    Pure formatting over a snapshot — no I/O — so the widget only has to render
    strings. On a failed snapshot returns a single explanatory row.
    """
    if not snapshot.ok:
        return [("State", snapshot.error or "Printer unreachable.")]

    raw = snapshot.raw

    def _temp(cur_key: str, target_key: str) -> str:
        cur = raw.get(cur_key)
        target = raw.get(target_key)
        if cur is None:
            return "—"
        if target:
            return f"{cur}°C → {target}°C"
        return f"{cur}°C"

    rows: list[tuple[str, str]] = [
        ("State", str(raw.get("gcode_state", "UNKNOWN"))),
        ("Progress", f"{raw.get('mc_percent', 0)}%"),
        ("Nozzle", _temp("nozzle_temper", "nozzle_target_temper")),
        ("Bed", _temp("bed_temper", "bed_target_temper")),
    ]
    layer = raw.get("layer_num")
    total_layer = raw.get("total_layer_num")
    if layer is not None or total_layer is not None:
        rows.append(("Layer", f"{layer or 0} / {total_layer or 0}"))
    if raw.get("wifi_signal"):
        rows.append(("Wi-Fi", str(raw.get("wifi_signal"))))
    if raw.get("gcode_file"):
        rows.append(("File", str(raw.get("gcode_file"))))
    return rows


def ams_tray_rows(snapshot: StatusSnapshot) -> list[dict[str, Any]]:
    """Flatten the AMS units/trays into view-ready rows, or ``[]`` when absent.

    Each row: ``{"unit": int, "slot": int, "label": str, "type": str,
    "color": str|None, "remain": str, "active": bool, "empty": bool}``.
    """
    if not snapshot.ok or not snapshot.ams:
        return []
    rows: list[dict[str, Any]] = []
    for unit in snapshot.ams.get("units", []):
        unit_id = unit.get("id", 0)
        for tray in unit.get("trays", []):
            slot = tray.get("slot", 0)
            empty = bool(tray.get("empty"))
            ftype = tray.get("type") or ("empty" if empty else "?")
            remain = tray.get("remain")
            remain_label = "—" if remain is None or remain < 0 else f"{remain}%"
            rows.append(
                {
                    "unit": unit_id,
                    "slot": slot,
                    "label": f"AMS{unit_id}·{slot}",
                    "type": ftype,
                    "color": tray.get("color"),
                    "remain": remain_label,
                    "active": bool(tray.get("active")),
                    "empty": empty,
                }
            )
    return rows


# --- Prepare pipeline -------------------------------------------------------


@dataclass
class PrepareResult:
    """The outcome of one prepare run (download → extract → slice → estimate).

    ``ok`` is False when the pipeline aborted; ``error`` then carries the
    ``BambuError`` message and ``rows`` is empty. ``state`` is always returned so
    the caller can clean up the temp workdir it owns (and, in a later phase,
    build the job namespace from it).
    """

    ok: bool
    state: Any
    rows: list[tuple[str, str]] = field(default_factory=list)
    model_path: str | None = None
    error: str | None = None


class PipelineService:
    """Run the wizard's prepare pipeline for the TUI, without any prompts.

    Sequencing (download → optional zip-extract → slice → estimate) is NOT
    reimplemented here: it is the shared ``interactive.core.run_prepare_pipeline``
    the ``plate go`` wizard runs, driven through the same injectable ``GoSteps``
    seam. This class turns a ``BambuError`` into a value the screen can render,
    since blocking calls run in a Textual thread worker where an escaping
    exception would kill the app.

    Workdir ownership: the caller may pass the ``workdir`` it created, and then
    owns deleting it (the screen does, so it can clean up even when the user
    leaves mid-run and the result is never delivered). Without one, a workdir is
    created here and the caller cleans it through ``cleanup``/``cleanup_workdir``.
    """

    def __init__(self, steps: Any = None) -> None:
        self._steps = steps

    def _get_steps(self) -> Any:
        if self._steps is not None:
            return self._steps
        from bambu_cli.interactive.core import GoSteps

        return GoSteps()

    def prepare(
        self,
        args: argparse.Namespace,
        *,
        source: str,
        material: str,
        quality: str,
        supports: bool,
        workdir: str | None = None,
        detected_material: str | None = None,
        detected_slot: int | None = None,
    ) -> PrepareResult:
        """Download/slice ``source`` with the chosen presets and preview it.

        Never raises: any failure from the pipeline (bad download, slicer
        failure, unexpected OSError) comes back as
        ``PrepareResult(ok=False, error=...)`` with the workdir cleaned up.
        """
        from bambu_cli.errors import BambuError
        from bambu_cli.interactive.core import (
            WizardState,
            cleanup_workdir,
            make_workdir,
            preview_rows,
            run_prepare_pipeline,
        )

        state = WizardState(
            source=source,
            material=material,
            quality=quality,
            supports=supports,
            detected_ams_material=detected_material,
            detected_ams_slot=detected_slot,
        )
        state.workdir = workdir if workdir is not None else make_workdir(prefix="bambu-tui-")
        try:
            model_path = run_prepare_pipeline(self._get_steps(), state, state.workdir)
            rows = preview_rows(state, model_path)
        except BambuError as exc:
            cleanup_workdir(state)
            return PrepareResult(ok=False, state=state, error=str(exc) or "Preparing the model failed.")
        except Exception as exc:  # noqa: BLE001 -- see docstring: a worker must never crash the UI
            # The pipeline is supposed to speak BambuError, but this runs in a
            # Textual thread worker: an unexpected OSError from a collaborator
            # must still surface as an inline message with the workdir cleaned up.
            cleanup_workdir(state)
            return PrepareResult(ok=False, state=state, error=_prepare_error(exc))
        return PrepareResult(ok=True, state=state, rows=rows, model_path=model_path)

    def cleanup(self, state: Any) -> None:
        """Delete the temp workdir a prepare run created (safe to call twice)."""
        from bambu_cli.interactive.core import cleanup_workdir

        if state is not None:
            cleanup_workdir(state)

    def cleanup_workdir(self, workdir: str | None) -> None:
        """Delete a temp workdir by path (safe when it is already gone)."""
        from bambu_cli.interactive.core import WizardState, cleanup_workdir

        if workdir:
            cleanup_workdir(WizardState(workdir=workdir))


# --- Live job monitoring ----------------------------------------------------


def terminal_gcode_states() -> frozenset[str]:
    """The gcode_state values that end a print watch.

    Imported from ``protocols.mqtt`` (lazily, so the TUI import path does not
    pull the MQTT stack in eagerly) rather than re-listed here: the TUI monitor
    must stop on exactly the set ``mqtt.monitor_status`` stops on.
    """
    from bambu_cli.protocols.mqtt import TERMINAL_GCODE_STATES

    return TERMINAL_GCODE_STATES


class MonitorService:
    """Poll printer status for a running job until it reaches a terminal state.

    A thin adapter over the same status provider the dashboard uses (so failures
    arrive as ``StatusSnapshot(ok=False)`` and never as exceptions). The loop
    itself lives in the screen's cancellable thread worker; this class only
    answers "what is the state now" and "is that state terminal", which keeps
    both decisions unit-testable without a pilot.
    """

    def __init__(self, status_provider: Any = None) -> None:
        self._status_provider = status_provider

    def _provider(self) -> Any:
        if self._status_provider is not None:
            return self._status_provider
        return StatusService()

    def poll(self, args: argparse.Namespace) -> StatusSnapshot:
        return self._provider().fetch(args)

    def is_terminal(self, snapshot: StatusSnapshot) -> bool:
        """True when the job is over — an unreadable status is never terminal."""
        if not snapshot.ok:
            return False
        return snapshot.gcode_state in terminal_gcode_states()


def job_progress_lines(snapshot: StatusSnapshot) -> list[tuple[str, str]]:
    """Return ``(label, value)`` rows for the job-progress widget (pure)."""
    if not snapshot.ok:
        return [("Status", snapshot.error or "Printer unreachable.")]

    raw = snapshot.raw
    rows: list[tuple[str, str]] = [
        ("State", str(raw.get("gcode_state", "UNKNOWN"))),
        ("Progress", f"{raw.get('mc_percent', 0)}%"),
        ("Layer", f"{raw.get('layer_num') or 0} / {raw.get('total_layer_num') or 0}"),
        ("Remaining", format_remaining(raw.get("mc_remaining_time"))),
    ]
    if raw.get("gcode_file"):
        rows.append(("File", str(raw.get("gcode_file"))))
    return rows


def format_remaining(minutes: Any) -> str:
    """Render ``mc_remaining_time`` (minutes) as ``1h 05m`` / ``42m`` / ``—``."""
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        return "—"
    hours, mins = divmod(total, 60)
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def progress_percent(snapshot: StatusSnapshot) -> int:
    """Clamped 0-100 completion percentage for the progress bar."""
    if not snapshot.ok:
        return 0
    try:
        value = int(snapshot.raw.get("mc_percent", 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))
