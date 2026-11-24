"""Orientation: see the model on the plate and choose how it lies before it is sliced.

The keys build the same request as ``--face`` / ``--orient`` / ``--rotate``
(see ``tui/orient_model.py``), and the side panel prints those flags, so a pose
picked here is one ``plate slice`` can repeat. The screen opens on the pose
Prepare would slice (auto, unless one was chosen before). Enter hands the
request back to the prepare screen, which slices with it; Escape leaves the
pose as it was.

Loading (and, for STEP, converting) the model and drawing it run in thread
workers: a big mesh takes a moment, and the UI must keep answering keys. A
draw that finishes after a newer key press is thrown away, so the picture
always matches the panel.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from bambu_cli.mesh import OrientRequest
from bambu_cli.tui.orient_model import (
    FACE_HEADER,
    QUARTER_TURN,
    SPIN_STEP,
    VIEW_NAMES,
    LoadedPoseModel,
    PoseEditor,
)
from bambu_cli.tui.widgets.model_view import ModelView, draw_model
from bambu_cli.tui.widgets.summary import summary_grid
from bambu_cli.tui.widgets.topbar import TopBar

# Each line fits the 38-cell side panel; Enter and Esc are in the footer.
KEYS_HELP = "f F face · x y z turn 90°\nX Y Z turn back · [ ] spin 15°\na auto · 0 as exported · v view"


class OrientScreen(Screen[Any]):
    """Dismisses with the chosen request (None: the default, when the model could not be read), or ``CANCELLED``."""

    BINDINGS = [
        Binding("enter", "use", "Use this pose"),
        Binding("escape", "cancel", "Cancel"),
        Binding("f", "face(1)", "Next face"),
        Binding("F", "face(-1)", "Prev face", show=False),
        Binding("x", "turn('x', 1)", "Turn x", show=False),
        Binding("X", "turn('x', -1)", show=False),
        Binding("y", "turn('y', 1)", "Turn y", show=False),
        Binding("Y", "turn('y', -1)", show=False),
        Binding("z", "turn('z', 1)", "Turn z", show=False),
        Binding("Z", "turn('z', -1)", show=False),
        Binding("left_square_bracket", "spin(-1)", "Spin", show=False),
        Binding("right_square_bracket", "spin(1)", show=False),
        Binding("a", "auto", "Auto"),
        Binding("0", "reset", "As exported", show=False),
        Binding("v", "view", "View", show=False),
        Binding("f1", "app.help", "Help", show=False),
    ]

    def __init__(self, deps: Any, model_path: str, request: OrientRequest | None = None) -> None:
        super().__init__()
        self._deps = deps
        self._model_path = model_path
        self._start = request
        self.editor: PoseEditor | None = None
        self._draws = 0  # bumped per draw request; a stale draw is dropped
        self._left = False

    def compose(self) -> ComposeResult:
        yield TopBar("Orientation", id="topbar")
        with Horizontal(id="orient-body"):
            yield ModelView("Loading the model…", id="orient-view")
            with VerticalScroll(id="orient-side"):
                yield Static("", id="orient-facts")
                yield Static("", id="orient-faces", markup=False)
                yield Static(KEYS_HELP, id="orient-keys", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#orient-view").border_title = "Model"
        self.run_worker(self._load_worker, thread=True, exclusive=True, group="orient-load", exit_on_error=False)

    def on_unmount(self) -> None:
        self._left = True

    # --- loading ------------------------------------------------------------

    def _load_worker(self) -> None:
        loaded, error = self._deps.get_pipeline().load_pose_model(self._model_path)
        self.app.call_from_thread(self._loaded, loaded, error)

    def _loaded(self, loaded: LoadedPoseModel | None, error: str | None) -> None:
        if self._left:
            return
        view = self.query_one("#orient-view", ModelView)
        if loaded is None:
            view.update(Text(error or "This model cannot be turned.", style="bold"))
            self.query_one("#orient-facts", Static).update("Press Esc to go back; the file is sliced as it is.")
            return
        self.editor = PoseEditor(loaded, self._start)
        view.border_title = f"{loaded.name} · {VIEW_NAMES[self.editor.view]}"
        self._refresh()

    # --- keys ---------------------------------------------------------------

    def _edit(self, change: Any) -> None:
        if self.editor is None:
            return
        change(self.editor)
        self._refresh()

    def action_face(self, step: int) -> None:
        self._edit(lambda editor: editor.next_face(step))

    def action_turn(self, axis: str, sign: int) -> None:
        self._edit(lambda editor: editor.turn(axis, sign * QUARTER_TURN))

    def action_spin(self, sign: int) -> None:
        self._edit(lambda editor: editor.turn("z", sign * SPIN_STEP))

    def action_auto(self) -> None:
        self._edit(PoseEditor.auto)

    def action_reset(self) -> None:
        self._edit(PoseEditor.reset)

    def action_view(self) -> None:
        self._edit(PoseEditor.cycle_view)

    def action_use(self) -> None:
        if self.editor is None:
            self.dismiss(self._start)
            return
        self.dismiss(self.editor.result())

    def action_cancel(self) -> None:
        self.dismiss(CANCELLED)

    def on_resize(self, event: events.Resize) -> None:
        if self.editor is not None:
            self.call_after_refresh(self._draw)

    # --- drawing ------------------------------------------------------------

    def _refresh(self) -> None:
        editor = self.editor
        assert editor is not None  # noqa: S101 -- callers check
        self.query_one("#orient-facts", Static).update(summary_grid(editor.facts()))
        lines = Text(no_wrap=True, overflow="ellipsis")
        lines.append(FACE_HEADER + "\n", style="dim")
        for current, line in editor.face_rows():
            lines.append(("▶" if current else " ") + line[1:] + "\n", style="bold" if current else "")
        self.query_one("#orient-faces", Static).update(lines)
        self.query_one("#orient-view").border_title = f"{editor.loaded.name} · {VIEW_NAMES[editor.view]}"
        self._draw()

    def _draw(self) -> None:
        editor = self.editor
        if editor is None:
            return
        self._draws += 1
        columns, rows = self.query_one("#orient-view", ModelView).cell_size()
        self.run_worker(
            partial(self._draw_worker, self._draws, editor.plan.rotation, editor.view, columns, rows),
            thread=True,
            exclusive=True,
            group="orient-draw",
            exit_on_error=False,
        )

    def _draw_worker(self, ticket: int, rotation: Any, view: str, columns: int, rows: int) -> None:
        assert self.editor is not None  # noqa: S101 -- set before any draw is queued
        picture = draw_model(self.editor.loaded.mesh, rotation, columns, rows, view=view)
        self.app.call_from_thread(self._show, ticket, picture)

    def _show(self, ticket: int, picture: Text) -> None:
        if self._left or ticket != self._draws:
            return
        self.query_one("#orient-view", ModelView).update(picture)


class _Cancelled:
    """Escape: distinct from None, which means "slice the default pose"."""

    def __repr__(self) -> str:
        return "CANCELLED"


CANCELLED: Any = _Cancelled()
