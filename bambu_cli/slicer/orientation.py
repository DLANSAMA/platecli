"""The orientation step of ``slice``: decide how the model stands, before OrcaSlicer runs.

platecli turns the model itself and hands OrcaSlicer a file already in its pose:
OrcaSlicer 2.3.1's own ``--rotate``/``--rotate-x``/``--rotate-y`` flags crash it,
and it does not drop a 3MF object onto the bed. ``--orient 0`` is passed too,
so OrcaSlicer never changes the pose behind platecli's back.

With no orientation flag the mode is ``DEFAULT_ORIENT_MODE`` (auto: the
best-ranked face, the one ``plate orient`` lists first). Auto is never silent:
the pose is drawn in the terminal before anything is sliced or sent, and when it
differs from the file's a line says how it was turned and that
``--orient keep`` slices the file as exported. A file auto cannot read (or a
3MF with several objects) is sliced as exported, with a warning saying so.
``--orient keep``, ``--face`` and ``--rotate`` override it.

A pre-sliced 3MF is never re-oriented; a flag that asks to turn one is an error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import DEFAULT_ORIENT_MODE, EXIT_COMMAND_ERROR
from bambu_cli.errors import BambuError
from bambu_cli.jsonio import json_mode_requested
from bambu_cli.logging_utils import logger
from bambu_cli.mesh import (
    ORIENTABLE_EXTENSIONS,
    LoadedModel,
    Matrix,
    Mesh,
    OrientPlan,
    OrientRequest,
    describe_turn,
    is_3mf_sliced,
    load_model,
    plan_orientation,
    write_oriented,
)

# Past this size the default (auto) is skipped with a warning and the file is
# sliced as exported: reading and ranking a mesh this big in pure Python takes
# long enough to be a surprise. ``--orient auto`` or ``--face`` analyse it anyway.
AUTO_ANALYSIS_MAX_BYTES = 64 * 1024 * 1024
# The terminal picture: two views (from above, from the front) side by side.
PICTURE_COLUMNS = 30  # per view
PICTURE_ROWS = 9  # cells; each holds two pixels
PICTURE_GAP = 2


def explicit_request(args: argparse.Namespace) -> OrientRequest | None:
    """The pose the flags ask for, or None when no orientation flag was given.

    ``--rotate`` alone turns the default pose (auto) further; with
    ``--orient keep`` it turns the file's own pose. Raises ``BambuError`` when
    ``--face`` and ``--orient`` are both given: each says how the model lies.
    """
    mode = _namespace_get(args, "orient", None)
    face = _namespace_get(args, "face", None)
    rotations = tuple(_namespace_get(args, "rotate", None) or ())
    if mode is None and face is None and not rotations:
        return None
    if face is not None and mode is not None:
        raise BambuError(
            f"--face and --orient {mode} both say how the model lies; pass one of them.",
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
        )
    return OrientRequest(mode=mode or DEFAULT_ORIENT_MODE, face=face, rotations=rotations)


def orient_request(args: argparse.Namespace) -> OrientRequest:
    """The orientation to slice with: the flags', or the default. Raises ``BambuError`` on a contradiction."""
    return explicit_request(args) or OrientRequest(mode=DEFAULT_ORIENT_MODE)


def asks_to_turn(args: argparse.Namespace) -> bool:
    """True when the flags themselves ask to change the pose (refused for a sliced file).

    The default never counts: a pre-sliced file without flags is sent as it is,
    and ``--orient keep`` on one is allowed.
    """
    request = explicit_request(args)
    return request is not None and request.changes_pose


def request_flags(request: OrientRequest) -> str:
    """The shortest CLI flags that select ``request`` ("" for the default pose)."""
    parts = []
    if request.face is not None:
        parts.append(f"--face {request.face}")
    elif request.mode != DEFAULT_ORIENT_MODE:
        parts.append(f"--orient {request.mode}")
    parts.extend(f"--rotate {axis}={degrees:g}" for axis, degrees in request.rotations)
    return " ".join(parts)


def presliced_refusal(name: str) -> BambuError:
    return BambuError(
        f"{name} is already sliced, so its orientation is fixed in the G-code; platecli never re-orients "
        "a sliced file. Drop --orient/--face/--rotate, or orient the model file (STL or an unsliced 3MF).",
        exit_code=EXIT_COMMAND_ERROR,
        failed_step="validate",
    )


def _matrix_json(rotation: Matrix) -> list[list[float]]:
    return [[round(v, 6) + 0.0 for v in row] for row in rotation]


def plan_summary(
    plan: OrientPlan | None,
    request: OrientRequest,
    *,
    presliced: bool = False,
    warning: str | None = None,
) -> dict:
    """The ``orientation`` object of the ``--json`` payload (see contracts.Orientation)."""
    mode = "face" if request.face is not None else request.mode
    summary: dict = {
        "mode": mode,
        "changed": bool(plan.changed) if plan is not None else False,
        "face": plan.face if plan is not None else request.face,
        "rotations": [f"{axis}={degrees:g}" for axis, degrees in request.rotations],
    }
    if presliced:
        summary["presliced"] = True
    if plan is not None:
        pose = plan.pose
        summary.update(
            turned=describe_turn(plan.rotation),
            rotation=_matrix_json(plan.rotation),
            height_mm=round(pose.height, 2),
            size_mm=[round(v, 2) for v in pose.size],
            bed_contact_mm2=round(pose.contact_area, 1),
            overhang_mm2=round(pose.overhang_area, 1),
            stable=pose.stable,
        )
    warning = warning or (plan.warning if plan is not None else None)
    if warning:
        summary["warning"] = warning
    return summary


@dataclass(frozen=True)
class ParsedOrientationSummary:
    """Normalized reading of a ``plan_summary`` dict shared by UI formatters."""

    mode: str
    face: int | None
    rotations: tuple[str, ...]
    has_turned: bool
    turned: str | None
    height_mm: float | None
    stable: bool | None
    warning: str | None
    auto_failed: bool
    unstable_keep: bool

    @property
    def tips_over(self) -> bool:
        return self.stable is False


def parse_orientation_summary(summary: dict | None) -> ParsedOrientationSummary | None:
    """Parse a ``plan_summary`` dict into structured fields, or None if absent/pre-sliced."""
    if not summary or summary.get("presliced"):
        return None
    warning = summary.get("warning") or None
    has_stable = "stable" in summary
    height = summary.get("height_mm")
    height_mm = float(height) if isinstance(height, (int, float)) and not isinstance(height, bool) else None
    return ParsedOrientationSummary(
        mode=summary.get("mode") or "keep",
        face=summary.get("face"),
        rotations=tuple(str(spec) for spec in (summary.get("rotations") or ())),
        has_turned="turned" in summary,
        turned=summary.get("turned"),
        height_mm=height_mm,
        stable=summary.get("stable") if has_stable else None,
        warning=warning,
        auto_failed=bool(warning and not has_stable),
        unstable_keep=bool(warning and has_stable),
    )


@dataclass
class OrientOutcome:
    path: str  # the file OrcaSlicer should load
    summary: dict | None  # contracts.Orientation fields, or None when not applicable
    reoriented_3mf: bool = False  # a 3MF whose object platecli moved (needs --arrange)


def apply_orientation(
    args: argparse.Namespace,
    filepath: str,
    work_dir: str,
    *,
    display_name: str | None = None,
) -> OrientOutcome:
    """Put ``filepath`` in the requested (or default) pose; return what OrcaSlicer should load."""
    explicit = explicit_request(args)
    request = explicit or OrientRequest(mode=DEFAULT_ORIENT_MODE)
    asked_to_turn = explicit is not None and explicit.changes_pose
    name = display_name or os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ORIENTABLE_EXTENSIONS:
        if asked_to_turn:
            raise BambuError(
                f"platecli orients .stl, .obj, .step/.stp and unsliced .3mf models, not {name}.",
                exit_code=EXIT_COMMAND_ERROR,
                failed_step="validate",
            )
        return OrientOutcome(filepath, None)
    if ext == ".3mf" and is_3mf_sliced(filepath):
        if asked_to_turn:
            raise presliced_refusal(name)
        return OrientOutcome(filepath, plan_summary(None, OrientRequest(mode="keep"), presliced=True))

    if not request.changes_pose:
        return _keep(args, filepath, name, request)
    if explicit is None:
        model, skip = _load_for_default(filepath, name)
        if model is None:
            logger.warning(f"⚠️  {skip}")
            return OrientOutcome(filepath, plan_summary(None, OrientRequest(mode="keep"), warning=skip))
    else:
        model = load_model(filepath)
    try:
        plan = plan_orientation(model.mesh, request)
    except ValueError as exc:
        raise BambuError(
            f"{name}: {exc}. Run 'plate orient {name}' to list its faces.",
            exit_code=EXIT_COMMAND_ERROR,
            failed_step="validate",
        ) from exc
    if plan.changed:
        oriented = write_oriented(model, plan, work_dir)
        reoriented_3mf = model.kind == "3mf"
    else:
        # The chosen pose is the file's own: slice the file untouched.
        oriented, reoriented_3mf = filepath, False
    _announce(args, model.mesh, plan, request)
    return OrientOutcome(oriented, plan_summary(plan, request), reoriented_3mf=reoriented_3mf)


def _load_for_default(filepath: str, name: str) -> tuple[LoadedModel | None, str]:
    """The model for the default (auto) pose, or None and why it is sliced as exported instead.

    The default must never stop a slice OrcaSlicer could do, so a file auto
    cannot handle falls back to the file's pose, with the reason said.
    """
    keep_note = "it is sliced as exported"
    try:
        size = os.path.getsize(filepath)
    except OSError as exc:
        return None, f"{name}: could not read it to choose how it lies ({exc.strerror or exc}); {keep_note}."
    if size > AUTO_ANALYSIS_MAX_BYTES:
        return None, (
            f"{name} is {size // (1024 * 1024)} MiB, too big to choose how it lies quickly; {keep_note}. "
            "--orient auto or --face N analyses it anyway."
        )
    try:
        model = load_model(filepath)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        return None, f"{name}: could not read the model to choose how it lies ({exc}); {keep_note}."
    if model.object_count != 1:
        return None, (
            f"{name} places {model.object_count} objects; platecli turns a 3MF with exactly one, "
            f"so {keep_note}, each object where the file put it."
        )
    return model, ""


def _keep(args: argparse.Namespace, filepath: str, name: str, request: OrientRequest) -> OrientOutcome:
    """``--orient keep``: slice the file as exported; analyse it (best effort) to show and warn."""
    try:
        if os.path.getsize(filepath) > AUTO_ANALYSIS_MAX_BYTES:
            return OrientOutcome(filepath, plan_summary(None, request))
        model: LoadedModel = load_model(filepath)
        if model.object_count != 1:
            return OrientOutcome(filepath, plan_summary(None, request))
        plan = plan_orientation(model.mesh, request)
    except Exception as exc:  # noqa: BLE001 -- keeping the pose must slice exactly as before
        # Keeping the pose never needs the analysis, so nothing in it may stop
        # a slice; OrcaSlicer reports a model it cannot read on its own terms.
        logger.debug(f"Orientation analysis skipped for {name}: {exc}")
        return OrientOutcome(filepath, plan_summary(None, request))
    _announce(args, model.mesh, plan, request)
    if plan.warning:
        logger.warning(f"⚠️  {name} {plan.warning}: drop --orient keep to use it.")
    return OrientOutcome(filepath, plan_summary(plan, request))


def turn_line(plan: OrientPlan, request: OrientRequest) -> str:
    """One line saying how the model will lie, and how to keep the file's pose when auto moved it."""
    pose = plan.pose
    facts = f"{pose.height:.1f} mm tall, {pose.contact_area:.0f} mm² on the bed"
    if not pose.stable:
        facts += " (⚠️ it may not stand on its own)"
    turned = describe_turn(plan.rotation)
    if request.face is None and request.mode == "auto" and not request.rotations:
        if turned is None:
            return f"🧭 Orientation: as exported, already its best face — {facts}"
        return f"🧭 Auto turned this model {turned} — {facts}. --orient keep slices it as exported"
    if not request.changes_pose:
        return f"🧭 Orientation: as exported — {facts}"
    moved = f", turned {turned} from the file" if turned else ", the file's own pose"
    return f"🧭 Orientation: {request.describe()}{moved} — {facts}"


def _announce(args: argparse.Namespace, mesh: Mesh, plan: OrientPlan, request: OrientRequest) -> None:
    """Draw the pose (on a colour terminal) and say how the model will lie."""
    draw_pose(args, mesh, plan.rotation)
    logger.info(turn_line(plan, request))


def draw_pose(args: argparse.Namespace, mesh: Mesh, rotation: Matrix) -> bool:
    """Draw ``mesh`` in ``rotation`` on stderr when ``picture_wanted``; True when it was drawn."""
    if not picture_wanted(args):
        return False
    stream = _terminal()
    try:
        for line in pose_picture(mesh, rotation):
            stream.write(line + "\n")
        stream.flush()
    except (OSError, ValueError) as exc:  # a closed or odd stream: the text line still says it
        logger.debug(f"Orientation picture skipped: {exc}")
        return False
    return True


def picture_wanted(args: argparse.Namespace) -> bool:
    """Draw the pose in the terminal: a person at a colour terminal, not ``--json`` or a front-end with its own."""
    if not _namespace_get(args, "orient_preview", True) or json_mode_requested(args):
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("TERM", "") == "dumb":
        return False
    isatty = getattr(_terminal(), "isatty", None)
    try:
        return bool(isatty and isatty())
    except (OSError, ValueError):
        return False


def _terminal():  # noqa: ANN202 -- a text stream
    """Where the picture goes: stderr, beside the log, so stdout stays data."""
    return sys.stderr


def pose_picture(mesh: Mesh, rotation: Matrix) -> list[str]:
    """The model in ``rotation``, from above and from the front, as ANSI half-block lines."""
    from bambu_cli.mesh.render import ansi_half_blocks, render

    columns = PICTURE_COLUMNS
    width = shutil.get_terminal_size((80, 24)).columns
    if width < 2 * columns + PICTURE_GAP:
        columns = max(8, (width - PICTURE_GAP) // 2)
    views = [
        ansi_half_blocks(render(mesh, rotation, columns, PICTURE_ROWS * 2, view=view, grid=False))
        for view in ("iso", "front")
    ]
    gap = " " * PICTURE_GAP
    labels = "from above".ljust(columns) + gap + "from the front"
    return [left + gap + right for left, right in zip(*views, strict=True)] + [f"\x1b[2m{labels}\x1b[0m"]
