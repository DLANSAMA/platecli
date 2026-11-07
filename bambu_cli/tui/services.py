"""Thin sync adapters between the Textual view layer and the domain.

Screens and widgets hold *no* domain logic (plan §4.1 view/logic split): every
decision — how to reach the printer, how to normalize AMS data, how to describe
a failure — lives here in plain synchronous functions/dataclasses that are
unit-tested without a Textual pilot. The screens call these from thread workers
(``printer.status()`` blocks on a ``threading.Event``) and render the result.
"""

from __future__ import annotations

import argparse
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bambu_cli.textsafe import display_safe


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

    Holds one ``RuntimeContext`` / printer for the TUI lifetime and calls
    ``hold_mqtt()`` so dashboard and monitor refreshes reuse a single TLS
    session. ``close()`` releases it.

    NEVER raises: any failure (MQTT error, timeout, missing config) is
    captured into ``StatusSnapshot(ok=False, error=...)`` so the dashboard
    renders an inline "printer unreachable" state instead of crashing.
    """

    def __init__(self, ctx: Any = None) -> None:
        self._ctx = ctx
        self._printer: Any = None

    def fetch(self, args: argparse.Namespace) -> StatusSnapshot:
        try:
            from bambu_cli.ams import parse_ams
            from bambu_cli.context import RuntimeContext

            if self._printer is None:
                if self._ctx is None:
                    self._ctx = RuntimeContext.for_request(args)
                self._printer = self._ctx.printer()
                self._printer.hold_mqtt()
            data = self._printer.status()
            if not isinstance(data, dict) or not data:
                return StatusSnapshot(ok=False, error="Printer returned no status.")
            ams = parse_ams(data)
            return StatusSnapshot(ok=True, raw=data, ams=ams)
        except Exception as exc:  # noqa: BLE001 -- see class docstring: never crash the UI
            return StatusSnapshot(ok=False, error=_short_error(exc))

    def close(self) -> None:
        """Drop the held MQTT session. Safe to call twice or before any fetch."""
        printer = self._printer
        self._printer = None
        if printer is not None:
            printer.release_mqtt()


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


# --- View models (pure; unit-tested without a pilot) -------------------------
#
# Everything the dashboard and monitor draw is decided here, from a snapshot,
# so the widgets only lay strings and numbers out. Printer-supplied strings pass
# through ``display_safe`` on the way in, and the widgets render them as
# ``Text`` (never as markup), so a filament called "PLA [matte]" or a file
# called "a[/b]c.gcode" is shown verbatim and cannot break a render.


@dataclass(frozen=True)
class StateBadge:
    """A printer state in words, plus the tone the view colours it with.

    Tones: ``active`` (printing), ``paused``, ``done``, ``error``, ``busy``
    (getting ready) and ``idle``.
    """

    label: str
    tone: str


_STATE_BADGES: dict[str, StateBadge] = {
    "RUNNING": StateBadge("Printing", "active"),
    "PAUSE": StateBadge("Paused", "paused"),
    "PREPARE": StateBadge("Preparing", "busy"),
    "SLICING": StateBadge("Slicing", "busy"),
    "INIT": StateBadge("Starting up", "busy"),
    "FINISH": StateBadge("Finished", "done"),
    "FAILED": StateBadge("Failed", "error"),
    "STOP": StateBadge("Stopped", "idle"),
    "IDLE": StateBadge("Idle", "idle"),
    "OFFLINE": StateBadge("Offline", "error"),
}
_OFFLINE = StateBadge("Offline", "error")
# States with a job percentage worth drawing. PREPARE reports 0% while it heats
# and levels, which would read as a stalled print.
_PROGRESS_STATES = frozenset({"RUNNING", "PAUSE", "FINISH", "FAILED"})


def _state_key(snapshot: StatusSnapshot) -> str:
    return str(snapshot.raw.get("gcode_state") or "").strip().upper()


def state_badge(snapshot: StatusSnapshot) -> StateBadge:
    """``RUNNING`` -> ``Printing`` and so on; a failed fetch is ``Offline``.

    An unrecognised state is shown title-cased rather than hidden, so a new
    firmware state is still visible, just not coloured.
    """
    if not snapshot.ok:
        return _OFFLINE
    key = _state_key(snapshot)
    if key in _STATE_BADGES:
        return _STATE_BADGES[key]
    return StateBadge(display_safe(key.title())[:24] or "Unknown", "idle")


def _number(value: Any) -> float | None:
    """A finite float, or None for anything else (a spoofed NaN included)."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _whole(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _within(value: float | None, low: float, high: float) -> float | None:
    """``value`` if it is a plausible reading, else None (a spoofed 1e308 is not one)."""
    return value if value is not None and low <= value <= high else None


# Readings outside these are reports nobody should be shown: no FDM heater
# reaches 1000 °C, and no print runs ten thousand hours or layers.
_TEMP_RANGE = (-50.0, 1000.0)
_MAX_LAYERS = 1_000_000


def format_remaining(minutes: Any) -> str:
    """Render ``mc_remaining_time`` (minutes) as ``1h 05m`` / ``42m`` / ``—``."""
    try:
        total = int(minutes)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if total < 0:
        return "—"
    hours, mins = divmod(total, 60)
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


# Longer than this is a bogus report, not a real print; it gets no clock time.
_MAX_ETA_MINUTES = 14 * 24 * 60
# A running print reporting 0 minutes left is finishing only from here on.
_FINISHING_PERCENT = 90


def eta_clock(minutes: Any, now: datetime) -> str | None:
    """The wall-clock time a print ends, ``17:55``, or None when unknown.

    A finish on another day says which (``Tue 09:10``), so an overnight print
    is not mistaken for one ending this afternoon.
    """
    total = _whole(minutes)
    if total is None or total <= 0 or total > _MAX_ETA_MINUTES:
        return None
    done = now + timedelta(minutes=total)
    if done.date() == now.date():
        return done.strftime("%H:%M")
    return done.strftime("%a %H:%M")


def progress_percent(snapshot: StatusSnapshot) -> int:
    """Clamped 0-100 completion percentage for the progress bar."""
    if not snapshot.ok:
        return 0
    value = _whole(snapshot.raw.get("mc_percent", 0))
    if value is None:
        return 0
    return max(0, min(100, value))


_JOB_SUFFIXES = (".gcode.3mf", ".3mf", ".gcode")


def job_name(snapshot: StatusSnapshot) -> str:
    """The job's name: ``subtask_name``, else ``benchy`` for ``/sdcard/benchy.gcode.3mf``.

    ``subtask_name`` is a name and is shown whole; ``gcode_file`` is a path on
    the printer, so only its last part is the job. ``""`` when neither is set.
    """
    if not snapshot.ok:
        return ""
    raw = snapshot.raw
    name = display_safe(raw.get("subtask_name") or "").strip()
    if not name:
        name = display_safe(raw.get("gcode_file") or "").strip()
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = name.lower()
    for suffix in _JOB_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


# A real reading is "-48dBm"; anything much longer would push the state badge
# off an 80-column top bar.
_MAX_WIFI_CHARS = 12


def wifi_text(snapshot: StatusSnapshot) -> str | None:
    """``Wi-Fi -48 dBm`` from ``wifi_signal``, or None when not reported."""
    if not snapshot.ok:
        return None
    signal = display_safe(snapshot.raw.get("wifi_signal") or "").strip()
    if not signal:
        return None
    if len(signal) > _MAX_WIFI_CHARS:
        signal = signal[: _MAX_WIFI_CHARS - 1] + "…"
    if signal.endswith("dBm") and not signal.endswith(" dBm"):
        signal = signal[:-3] + " dBm"
    return f"Wi-Fi {signal}"


# --- AMS -----------------------------------------------------------------------

_HEX6 = re.compile(r"[0-9A-Fa-f]{6}")
_DARK_INK = "#0D1117"
_LIGHT_INK = "#F5F7FA"


def swatch_colors(color: Any) -> tuple[str, str] | None:
    """``("#0A6AC8", ink)`` for a reported filament colour, or None if it is not one.

    ``ink`` is the text colour that stays readable on that swatch: dark on a
    light filament, light on a dark one (WCAG relative luminance, so yellow gets
    dark text and blue gets light text).
    """
    if not isinstance(color, str):
        return None
    hexpart = color.strip().lstrip("#")[:6]
    if not _HEX6.fullmatch(hexpart):
        return None

    def channel(offset: int) -> float:
        value = int(hexpart[offset : offset + 2], 16) / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
    # Above this, black text has the better contrast ratio; below it, white does.
    ink = _DARK_INK if luminance > 0.179 else _LIGHT_INK
    return f"#{hexpart.upper()}", ink


@dataclass(frozen=True)
class TrayView:
    """One AMS tray (or the external spool), ready to draw."""

    label: str
    material: str
    swatch: str | None
    ink: str
    remain: int | None
    active: bool
    empty: bool
    external: bool = False


@dataclass(frozen=True)
class AmsUnitView:
    """One AMS unit: its title, its sensor line, and its trays in slot order."""

    title: str
    detail: str
    trays: tuple[TrayView, ...]


@dataclass(frozen=True)
class AmsView:
    units: tuple[AmsUnitView, ...]
    external: TrayView | None

    @property
    def trays(self) -> tuple[TrayView, ...]:
        external = (self.external,) if self.external is not None else ()
        return tuple(tray for unit in self.units for tray in unit.trays) + external


def _remain(value: Any) -> int | None:
    """0-100, or None when the printer cannot measure it (it reports -1 then)."""
    number = _whole(value)
    if number is None or number < 0:
        return None
    return min(number, 100)


def _tray_view(tray: dict[str, Any], label: str, *, external: bool = False) -> TrayView:
    empty = bool(tray.get("empty"))
    colors = None if empty else swatch_colors(tray.get("color"))
    return TrayView(
        label=label,
        material="" if empty else (display_safe(tray.get("type")).strip() or "?"),
        swatch=colors[0] if colors else None,
        ink=colors[1] if colors else _LIGHT_INK,
        remain=None if empty else _remain(tray.get("remain")),
        active=bool(tray.get("active")),
        empty=empty,
        external=external,
    )


def _unit_detail(unit: dict[str, Any]) -> str:
    """``humidity 4 · 27 °C``. The humidity level is shown as reported, not judged."""
    parts = []
    humidity = _whole(unit.get("humidity"))
    if humidity is not None:
        parts.append(f"humidity {humidity}")
    temp = _number(unit.get("temp"))
    # An AMS lite has no temperature sensor and reports 0.
    if temp is not None and temp > 0:
        parts.append(f"{temp:.0f} °C")
    return " · ".join(parts)


def ams_view(snapshot: StatusSnapshot) -> AmsView | None:
    """The AMS units and external spool, or None when there is nothing to show."""
    if not snapshot.ok or not snapshot.ams:
        return None
    from bambu_cli.ams import tray_label

    units = []
    # parse_ams only emits dicts, but this runs on the UI thread: a malformed
    # entry is skipped rather than allowed to raise mid-refresh.
    for unit in snapshot.ams.get("units") or []:
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get("id", 0)
        trays = tuple(
            _tray_view(tray, tray_label(unit_id, tray.get("slot", 0)))
            for tray in unit.get("trays") or []
            if isinstance(tray, dict)
        )
        letter = tray_label(unit_id, 0)[:-1]
        title = f"AMS {letter}" if len(letter) == 1 else f"AMS {unit_id}"
        units.append(AmsUnitView(title=title, detail=_unit_detail(unit), trays=trays))
    raw_external = snapshot.ams.get("external_tray")
    external = _tray_view(raw_external, "Ext", external=True) if isinstance(raw_external, dict) else None
    if not units and external is None:
        return None
    return AmsView(units=tuple(units), external=external)


def feed_text(snapshot: StatusSnapshot) -> str | None:
    """``PETG from A2``: what the active tray holds, or None when none is active."""
    view = ams_view(snapshot)
    if view is None:
        return None
    for tray in view.trays:
        if tray.active and not tray.empty:
            where = "the external spool" if tray.external else tray.label
            return f"{tray.material} from {where}"
    return None


# --- Temperatures ------------------------------------------------------------


@dataclass(frozen=True)
class TempView:
    """A heater's reading and target, in words."""

    current: float | None
    target: float | None

    @property
    def value(self) -> str:
        return "—" if self.current is None else f"{self.current:.0f} °C"

    @property
    def changing(self) -> str | None:
        """``heating`` / ``cooling`` while more than a few degrees off target, else None."""
        if not self.target or self.current is None:
            return None
        if self.current < self.target - _TEMP_SETTLED:
            return "heating"
        if self.current > self.target + _TEMP_SETTLED:
            return "cooling"
        return None

    @property
    def caption(self) -> str:
        """``heating to 220 °C`` / ``target 220 °C`` / ``heater off``."""
        if not self.target:
            return "heater off"
        target = f"{self.target:.0f} °C"
        changing = self.changing
        return f"{changing} to {target}" if changing else f"target {target}"


# Within this many degrees of the target a heater counts as there.
_TEMP_SETTLED = 3.0
HEATERS = (
    ("nozzle", "Nozzle", "nozzle_temper", "nozzle_target_temper", 300.0),
    ("bed", "Bed", "bed_temper", "bed_target_temper", 120.0),
)


def temp_view(snapshot: StatusSnapshot, current_key: str, target_key: str) -> TempView:
    if not snapshot.ok:
        return TempView(None, None)
    target = _within(_number(snapshot.raw.get(target_key)), *_TEMP_RANGE)
    current = _within(_number(snapshot.raw.get(current_key)), *_TEMP_RANGE)
    return TempView(current, target if target and target > 0 else None)


class TempHistory:
    """The last ``size`` readings of each heater, oldest first, for the sparklines.

    A failed fetch adds nothing, so a dropped connection leaves a gap in time,
    not a fake drop to zero.
    """

    def __init__(self, size: int = 120) -> None:
        self._series: dict[str, deque[float]] = {key: deque(maxlen=size) for key, *_ in HEATERS}

    def add(self, snapshot: StatusSnapshot) -> None:
        if not snapshot.ok:
            return
        for key, _title, current_key, _target_key, _scale in HEATERS:
            # Bounded like temp_view: one spoofed 1e308 would otherwise set the
            # sparkline's scale for the next ``size`` samples and flatten it.
            value = _within(_number(snapshot.raw.get(current_key)), *_TEMP_RANGE)
            if value is not None:
                self._series[key].append(value)

    def series(self, key: str) -> list[float]:
        return list(self._series.get(key, ()))


# --- The job hero --------------------------------------------------------------


@dataclass(frozen=True)
class JobView:
    """What the job panel says about the printer right now.

    ``percent`` is None when there is no progress worth drawing (idle, getting
    ready, offline); the panel then shows ``lines`` alone, the first as a
    headline. Otherwise it draws the percentage big and ``lines`` beside it.
    """

    badge: StateBadge
    name: str
    percent: int | None
    lines: tuple[str, ...]


def _layer_count(value: Any) -> int | None:
    count = _whole(value)
    return count if count is not None and 0 <= count <= _MAX_LAYERS else None


def _layer_text(raw: dict[str, Any]) -> str | None:
    layer = _layer_count(raw.get("layer_num"))
    total = _layer_count(raw.get("total_layer_num"))
    if not layer and not total:
        return None
    if total:
        return f"Layer {layer or 0} of {total}"
    return f"Layer {layer}"


def job_view(snapshot: StatusSnapshot, now: datetime) -> JobView:
    """Turn a snapshot into the job panel's percent and lines (pure; ``now`` injected)."""
    badge = state_badge(snapshot)
    if not snapshot.ok:
        reason = display_safe(snapshot.error).strip() or "No status came back."
        return JobView(badge, "", None, ("Can't reach the printer", reason))

    raw = snapshot.raw
    key = _state_key(snapshot)
    name = job_name(snapshot)
    feed = feed_text(snapshot)
    layer = _layer_text(raw)

    if key in _PROGRESS_STATES:
        remaining = _whole(raw.get("mc_remaining_time"))
        if remaining is not None and not 0 <= remaining <= _MAX_ETA_MINUTES:
            remaining = None
        if key == "RUNNING":
            if remaining is None:
                first = "Time left unknown"
            elif remaining == 0:
                # The printer also reports 0 minutes before its estimate is in,
                # at the start of a print; only near the end does 0 mean done.
                first = "Finishing up" if progress_percent(snapshot) >= _FINISHING_PERCENT else "Time left unknown"
            else:
                eta = eta_clock(remaining, now)
                first = f"{format_remaining(remaining)} left" + (f" · done about {eta}" if eta else "")
        elif key == "PAUSE":
            left = f" · {format_remaining(remaining)} left" if remaining and remaining > 0 else ""
            first = f"Paused{left}"
        elif key == "FINISH":
            first = "Finished"
        else:
            first = "This print failed"
        lines = [first]
        if layer:
            lines.append(layer)
        if key == "FAILED":
            lines.append("The printer's screen says why")
        elif feed:
            lines.append(feed)
        return JobView(badge, name, progress_percent(snapshot), tuple(lines))

    if badge.tone == "busy":
        headline = {"SLICING": "Slicing on the printer", "INIT": "The printer is starting up"}.get(
            key, "Getting ready to print"
        )
        return JobView(badge, name, None, (headline, name or "Heating and checking the bed"))

    if key not in _STATE_BADGES:
        # A missing or new firmware state is not "ready": say what is known.
        headline = f"Printer state: {badge.label}" if key else "Waiting for the printer's state"
        return JobView(badge, "", None, (headline,))

    # An idle printer still reports the last job's file; naming the panel after
    # it would read as if that job were running.
    headline = "Print stopped" if key == "STOP" else "Ready for a print"
    detail = f"Loaded: {feed}" if feed else "Nothing loaded in the AMS"
    if snapshot.ams is None:
        detail = ""
    return JobView(badge, "", None, (headline, detail) if detail else (headline,))


def printer_descriptor() -> str:
    """``P1S · 192.168.1.23``, ``Simulated P1S``, or ``P1S · not set up``."""
    from bambu_cli.context import current_settings, current_simulation

    settings = current_settings()
    model = display_safe(settings.printer_model).strip() or "Printer"
    if current_simulation():
        return f"Simulated {model}"
    ip = display_safe(settings.printer_ip).strip()
    if not ip or ip == "0.0.0.0":
        return f"{model} · not set up"
    return f"{model} · {ip}"


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
        overrides: Any = None,
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
        if overrides is not None:
            state.overrides = overrides
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
