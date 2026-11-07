"""The AMS as tray cards: a swatch in the filament's colour, its material, how much is left.

View layer only. ``services.ams_view`` names the trays (``A1``-``A4``, like
Bambu Studio and the printer's screen) and validates the colours; this module
draws them. The cards are built once per AMS layout and updated in place after
that, so a 10 s refresh does not tear down and remount the widgets.
"""

from __future__ import annotations

from typing import Any

from rich.style import Style
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from bambu_cli.tui.services import AmsUnitView, AmsView, TrayView

_METER_LOW = 20


class TrayCard(Widget):
    """One tray: two swatch rows (material on the first) and a remaining meter."""

    COMPONENT_CLASSES = {"tray--empty", "tray--meter", "tray--meter-low", "tray--track", "tray--muted"}

    def __init__(self, tray: TrayView, **kwargs: Any) -> None:
        super().__init__(classes="tray-card", **kwargs)
        self.tray = tray
        self._label()

    def show(self, tray: TrayView) -> None:
        self.tray = tray
        self._label()
        self.refresh()

    def _label(self) -> None:
        self.border_title = Text(("▶ " if self.tray.active else "") + self.tray.label)
        self.set_class(self.tray.active, "-active")
        self.set_class(self.tray.empty, "-empty")

    def render(self) -> Text:
        tray = self.tray
        width = max(self.content_size.width, 1)
        text = Text(no_wrap=True, overflow="ellipsis")
        if tray.empty:
            empty = self.get_component_rich_style("tray--empty")
            text.append("empty".center(width)[:width] + "\n", style=empty)
            text.append(" " * width + "\n", style=empty)
            return text
        if tray.swatch is not None:
            swatch = Style(color=tray.ink, bgcolor=tray.swatch, bold=True)
        else:
            swatch = self.get_component_rich_style("tray--empty") + Style(bold=True)
        material = tray.material if len(tray.material) <= width else tray.material[: max(width - 1, 0)] + "…"
        text.append(material.center(width)[:width] + "\n", style=swatch)
        text.append(" " * width + "\n", style=swatch)
        text.append_text(self._meter(width))
        return text

    def _meter(self, width: int) -> Text:
        remain = self.tray.remain
        if remain is None:
            return Text("left: ?".ljust(width)[:width], style=self.get_component_rich_style("tray--muted"))
        label = f" {remain}%"
        bar = max(width - len(label), 0)
        filled = round(bar * remain / 100)
        style = "tray--meter-low" if remain < _METER_LOW else "tray--meter"
        return Text.assemble(
            ("━" * filled, self.get_component_rich_style(style)),
            ("━" * (bar - filled), self.get_component_rich_style("tray--track")),
            (label, self.get_component_rich_style("tray--muted")),
        )


class AmsUnitBox(Horizontal):
    """One AMS unit's row of tray cards (plus the external spool on the first)."""

    def __init__(self, unit: AmsUnitView | None, external: TrayView | None, **kwargs: Any) -> None:
        trays = list(unit.trays) if unit else []
        if external is not None:
            trays.append(external)
        super().__init__(*[TrayCard(tray) for tray in trays], classes="ams-unit", **kwargs)
        self._titles(unit)

    def _titles(self, unit: AmsUnitView | None) -> None:
        self.border_title = Text(unit.title if unit else "Spool")
        self.border_subtitle = Text(unit.detail) if unit and unit.detail else None

    def show(self, unit: AmsUnitView | None, external: TrayView | None) -> None:
        """Update in place; the caller guarantees the same layout (see ``_shape``)."""
        self._titles(unit)
        trays = list(unit.trays) if unit else []
        if external is not None:
            trays.append(external)
        # strict=False: a remount still in flight can briefly hold fewer cards.
        for card, tray in zip(self.query(TrayCard).results(TrayCard), trays, strict=False):
            card.show(tray)


def _rows(view: AmsView) -> list[tuple[AmsUnitView | None, TrayView | None]]:
    """One row per unit; the external spool rides on the first (or alone)."""
    if not view.units:
        return [(None, view.external)]
    return [(unit, view.external if index == 0 else None) for index, unit in enumerate(view.units)]


def _shape(view: AmsView | None, reachable: bool) -> Any:
    """What decides the strip's widgets; a change of shape rebuilds them.

    With no AMS the note is the only widget, and its words depend on whether
    the printer answered at all, so ``reachable`` is part of that shape.
    """
    if view is None:
        return ("note", reachable)
    return tuple(
        (unit.title if unit else None, len(unit.trays) if unit else 0, ext is not None) for unit, ext in _rows(view)
    )


class AmsStrip(Vertical):
    """Every AMS unit and the external spool, or one line saying why there are none."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Static("Waiting for the printer…", classes="ams-note", markup=False), **kwargs)
        self._shape: Any = "unset"

    def show(self, view: AmsView | None, *, reachable: bool = True) -> None:
        shape = _shape(view, reachable)
        if shape != self._shape:
            self._shape = shape
            self.remove_children()
            if view is None:
                note = "No AMS or external spool reported" if reachable else "AMS unavailable while offline"
                self.mount(Static(note, classes="ams-note", markup=False))
            else:
                self.mount_all([AmsUnitBox(unit, ext) for unit, ext in _rows(view)])
            return
        if view is not None:
            for box, (unit, ext) in zip(self.query(AmsUnitBox).results(AmsUnitBox), _rows(view), strict=False):
                box.show(unit, ext)
