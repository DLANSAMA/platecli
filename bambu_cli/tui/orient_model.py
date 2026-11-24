"""The pose being chosen on the orientation screen, kept in CLI vocabulary (view layer only).

Every key on the screen turns into an ``OrientRequest`` (the same object
``--orient`` / ``--face`` / ``--rotate`` build), and the pose shown is
``plan_orientation`` of that request: exactly what ``plate slice`` would slice
with the matching flags, which the screen prints so the pose can be repeated
from the CLI. The screen opens on the default pose (``DEFAULT_ORIENT_MODE``,
auto), the one Prepare slices when nobody opens it. Nothing here knows about
Textual.

Turns stack on top of the face: ``f`` picks a face and clears the turns, then
``x``/``y``/``z`` and ``[``/``]`` turn the model from there, the way
``--face 2 --rotate z=15`` does. Turns about the same axis in a row merge into
one (``z=15`` twice is ``z=30``), so the flags stay short.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from bambu_cli.constants import DEFAULT_ORIENT_MODE
from bambu_cli.mesh import (
    Analyzer,
    LoadedModel,
    Mesh,
    MeshError,
    OrientPlan,
    OrientRequest,
    describe_turn,
    is_3mf_sliced,
    is_identity,
    load_model,
    plan_orientation,
)
from bambu_cli.slicer.orientation import request_flags
from bambu_cli.textsafe import display_safe

QUARTER_TURN = 90.0
SPIN_STEP = 15.0
# The render views a person can cycle through, in order.
VIEW_ORDER = ("iso", "front", "side", "top")
VIEW_NAMES = {"iso": "from above", "front": "from the front", "side": "from the side", "top": "from the top"}

_ORIENTABLE = (".stl", ".obj", ".3mf")
# What Prepare slices when the orientation screen was never opened.
DEFAULT_REQUEST = OrientRequest(mode=DEFAULT_ORIENT_MODE)


@dataclass(frozen=True)
class LoadedPoseModel:
    """A model ready to be posed: its mesh, analysed once, and the name to show."""

    name: str
    model: LoadedModel
    analyzer: Analyzer

    @property
    def mesh(self) -> Mesh:
        return self.model.mesh


def load_pose_model(path: str) -> LoadedPoseModel:
    """Read and analyse ``path`` (STL, OBJ or an unsliced 3MF with one object).

    Raises ``MeshError`` with a message to show, for a file that cannot be
    posed: a sliced 3MF (its pose is fixed in the G-code), a 3MF with several
    objects, or anything unreadable.
    """
    name = display_safe(os.path.basename(path))
    ext = os.path.splitext(path)[1].lower()
    if ext not in _ORIENTABLE:
        raise MeshError(f"{name} is not a model platecli can turn (STL, OBJ, STEP or an unsliced 3MF).")
    if ext == ".3mf" and is_3mf_sliced(path):
        raise MeshError(f"{name} is already sliced, so its orientation is fixed in the G-code.")
    model = load_model(path)
    if model.object_count != 1:
        raise MeshError(
            f"{name} places {model.object_count} objects; platecli turns a 3MF with exactly one. "
            "Orient it in OrcaSlicer, or export the part you want as STL."
        )
    analyzer = Analyzer(model.mesh)
    analyzer.rank()  # rank once here, off the UI thread; plans reuse it
    return LoadedPoseModel(name=name, model=model, analyzer=analyzer)


def _normalized(degrees: float) -> float:
    """``degrees`` folded into (-180, 180]."""
    folded = degrees % 360.0
    return folded - 360.0 if folded > 180.0 else folded


def with_turn(request: OrientRequest, axis: str, degrees: float) -> OrientRequest:
    """``request`` turned ``degrees`` more about ``axis``, merged into a trailing turn on that axis."""
    turns = list(request.rotations)
    if turns and turns[-1][0] == axis:
        degrees += turns.pop()[1]
    degrees = _normalized(degrees)
    if abs(degrees) > 1e-9:
        turns.append((axis, degrees))
    return replace(request, rotations=tuple(turns))


def flags(request: OrientRequest) -> str:
    """The CLI flags that select this pose ("" for the default pose, which needs none)."""
    return request_flags(request)


def pose_line(summary: dict | None) -> tuple[str, bool] | None:
    """The line beside the prepare screen's picture of the sliced pose, and whether it warns.

    Says when auto turned the model and how to keep the file's orientation
    instead (``o``, then ``0``). None when there is no pose to talk about.
    """
    if not summary or summary.get("presliced"):
        return None
    mode = summary.get("mode")
    turned = summary.get("turned")
    rotations = summary.get("rotations") or []
    tips = summary.get("stable") is False
    if summary.get("warning") and "stable" not in summary:
        return "Auto could not read this model, so it is sliced as exported.", True
    if mode == "keep" and not rotations:
        if summary.get("warning"):
            return "As exported it rests on a point or an edge · o, then a, lays it on its best face", True
        return "As exported" + (" · it may tip over" if tips else "") + " · o changes it", tips
    if mode == "auto" and not rotations:
        if turned:
            line = f"Auto turned this model {turned} · o, then 0, keeps the file's orientation"
        else:
            line = "Auto: it already lies on its best face, as exported · o changes it"
        return line + (" · it may tip over" if tips else ""), tips
    base = f"Face {summary.get('face')}" if mode == "face" else ("Auto" if mode == "auto" else "As exported")
    turns = ", ".join(str(spec) for spec in rotations)
    what = f"{base} + {turns}" if turns else base
    moved = f"turned {turned} from the file" if turned else "the file's own pose"
    return f"{what}: {moved}" + (" · it may tip over" if tips else "") + " · o changes it", tips


class PoseEditor:
    """The request under construction and its plan, one key at a time."""

    def __init__(self, loaded: LoadedPoseModel, request: OrientRequest | None = None) -> None:
        self.loaded = loaded
        self.view = VIEW_ORDER[0]
        start = request or DEFAULT_REQUEST
        try:
            self._set(start)
        except ValueError:
            # A face number from an earlier model this one does not have.
            self._set(DEFAULT_REQUEST)

    def _set(self, request: OrientRequest) -> None:
        self.plan: OrientPlan = plan_orientation(self.loaded.mesh, request, self.loaded.analyzer)
        self.request = request

    @property
    def face_count(self) -> int:
        return len(self.plan.faces)

    def next_face(self, step: int = 1) -> None:
        """Lay the model on the next (or previous) ranked face; turns are cleared.

        From a pose no face was chosen for (as exported, or turned by hand),
        ``f`` starts at face 1, the best, and ``F`` at the last.
        """
        count = self.face_count
        if count == 0:
            return
        if self.request.face is None:
            target = 1 if step > 0 else count
            if target == self.plan.face and count > 1:
                target = target % count + 1 if step > 0 else target - 1
        else:
            target = (self.request.face - 1 + step) % count + 1
        self._set(OrientRequest(face=target))

    def turn(self, axis: str, degrees: float) -> None:
        self._set(with_turn(self.request, axis, degrees))

    def auto(self) -> None:
        self._set(OrientRequest(mode="auto"))

    def reset(self) -> None:
        """``0``: the pose the file was exported in."""
        self._set(OrientRequest(mode="keep"))

    def cycle_view(self) -> None:
        self.view = VIEW_ORDER[(VIEW_ORDER.index(self.view) + 1) % len(VIEW_ORDER)]

    def result(self) -> OrientRequest:
        """What the prepare screen should slice: always the pose on screen, never "the default".

        Explicit even when it is the default pose, so the slice resolves it
        the way this screen did (the default skips auto on a very big file;
        a pose someone looked at does not).
        """
        return self.request

    def describe(self) -> str:
        request = self.request
        if request.changes_pose:
            return request.describe()
        return "as exported"

    def facts(self) -> list[tuple[str, str]]:
        """``(label, value)`` rows describing the current pose."""
        pose = self.plan.pose
        sx, sy, sz = pose.size
        rows = [
            ("Pose", self.describe()),
            ("Turned", describe_turn(self.plan.rotation) or "no, as exported"),
            ("Height", f"{pose.height:.1f} mm"),
            ("Footprint", f"{sx:.0f} × {sy:.0f} mm"),
            ("On the bed", f"{pose.contact_area:.0f} mm²"),
            ("Overhang", f"{pose.overhang_area:.0f} mm² (orange)"),
            ("Stands", "yes" if pose.stable else "no: it may tip over"),
            ("CLI", flags(self.request) or "(no flags needed)"),
        ]
        if self.plan.warning:
            rows.insert(0, ("Note", "it rests on a point or an edge as exported; press a for auto"))
        return rows

    def face_rows(self) -> list[tuple[bool, str]]:
        """One line per ranked face, flagged when it is the current pose (fits 38 cells)."""
        rows = []
        for n, pose in enumerate(self.plan.faces, 1):
            tag = "auto" if n == 1 else ("exported" if is_identity(pose.rotation) else "")
            stands = "yes" if pose.stable else "tips"
            line = f"{n:>2}  {pose.height:>7.1f}  {pose.contact_area:>7.0f}  {stands:<6} {tag}".rstrip()
            rows.append((n == self.plan.face, line))
        return rows


FACE_HEADER = " #  tall mm  bed mm²  stands"
