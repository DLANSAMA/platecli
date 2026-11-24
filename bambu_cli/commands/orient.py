"""``plate orient`` and ``plate preview``: see and choose a model's pose without slicing.

``orient`` ranks the faces the model can lie on (face 1 is what auto, the
default, picks), reports the pose the flags select and, at a colour terminal,
draws it; ``preview`` draws that pose and every ranked face into one PNG. Both
take the same ``--orient`` / ``--face`` / ``--rotate`` flags as ``slice`` and
``job``, and resolve them the same way, so what you look at here is what those
commands slice.
"""

from __future__ import annotations

import argparse
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.constants import DEFAULT_ORIENT_MODE, EXIT_COMMAND_ERROR, EXIT_FILE_ERROR
from bambu_cli.errors import BambuError
from bambu_cli.logging_utils import logger
from bambu_cli.mesh import (
    LoadedModel,
    MeshError,
    OrientPlan,
    OrientRequest,
    Pose,
    is_3mf_sliced,
    is_identity,
    load_model,
    plan_orientation,
)
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.paths import path_for_message as _path_for_message
from bambu_cli.slicer.orientation import draw_pose, explicit_request, plan_summary, request_flags, turn_line
from bambu_cli.textsafe import display_safe
from bambu_cli.utils import emit_json, emit_json_error

_MODEL_EXTENSIONS = (".stl", ".obj", ".3mf", ".step", ".stp")


def _default_step_converter() -> Callable[[str], tuple[str | None, bool]]:
    from bambu_cli.slicer.step_convert import _convert_step_to_stl

    return _convert_step_to_stl


@contextmanager
def _loaded(
    args: argparse.Namespace,
    command: str,
    step_converter: Callable[[str], tuple[str | None, bool]] | None = None,
) -> Iterator[tuple[str, LoadedModel]]:
    """Validate ``args.file`` and load it (converting STEP first); clean up after."""
    path = _expand_path(args.file)
    if path.startswith("-") or not os.path.isfile(path):
        emit_json_error(
            args, command, EXIT_FILE_ERROR, f"File not found: {_path_for_message(path)}", failed_step="validate"
        )
    ext = os.path.splitext(path)[1].lower()
    if ext not in _MODEL_EXTENSIONS:
        emit_json_error(
            args,
            command,
            EXIT_FILE_ERROR,
            f"{display_safe(os.path.basename(path))} is not a model: {command} reads .stl, .obj, .step/.stp and .3mf.",
            failed_step="validate",
            file=path,
        )
    if ext == ".3mf" and is_3mf_sliced(path):
        emit_json_error(
            args,
            command,
            EXIT_COMMAND_ERROR,
            f"{display_safe(os.path.basename(path))} is already sliced, so its orientation is fixed in the G-code. "
            f"{command} works on model files (STL, OBJ, STEP or an unsliced 3MF).",
            failed_step="validate",
            file=path,
        )
    converted_dir: str | None = None
    load_path = path
    try:
        if ext in (".step", ".stp"):
            converter = step_converter or _default_step_converter()
            converted, ok = converter(path)
            if not ok or not converted:
                emit_json_error(
                    args, command, EXIT_COMMAND_ERROR, "STEP/STP conversion failed.", failed_step="convert", file=path
                )
            assert converted is not None  # noqa: S101 -- emit_json_error is NoReturn
            converted_dir = os.path.dirname(converted)
            load_path = converted
        try:
            model = load_model(load_path)
        except MeshError as exc:
            emit_json_error(args, command, exc.exit_code, str(exc), failed_step="load", file=path)
        model.path = path  # report (and name outputs after) the file the user gave
        yield path, model
    finally:
        if converted_dir:
            shutil.rmtree(converted_dir, ignore_errors=True)


def _plan(args: argparse.Namespace, command: str, model: LoadedModel, path: str) -> tuple[OrientRequest, OrientPlan]:
    try:
        explicit = explicit_request(args)
        request = explicit or OrientRequest(mode=DEFAULT_ORIENT_MODE)
        if request.changes_pose and model.object_count != 1:
            if explicit is None:
                # The default would turn it, but a 3MF with several objects is
                # sliced as exported; show that.
                return OrientRequest(mode="keep"), plan_orientation(model.mesh, OrientRequest(mode="keep"))
            raise BambuError(
                f"{display_safe(os.path.basename(path))} places {model.object_count} objects; platecli orients a 3MF "
                "with exactly one.",
                exit_code=EXIT_COMMAND_ERROR,
                failed_step="validate",
            )
        return request, plan_orientation(model.mesh, request)
    except ValueError as exc:
        emit_json_error(args, command, EXIT_COMMAND_ERROR, str(exc), failed_step="validate", file=path)
    except BambuError as exc:
        emit_json_error(args, command, exc.exit_code, str(exc), failed_step=exc.failed_step or "validate", file=path)


def _face_payload(pose: Pose, face: int | None) -> dict:
    return {
        "face": face,
        "down": [round(v, 4) + 0.0 for v in pose.down],
        "height_mm": round(pose.height, 2),
        "size_mm": [round(v, 2) for v in pose.size],
        "bed_contact_mm2": round(pose.contact_area, 1),
        "overhang_mm2": round(pose.overhang_area, 1),
        "stable": pose.stable,
        "rotation": _matrix(pose.rotation),
    }


def _matrix(m) -> list[list[float]]:  # noqa: ANN001 -- a 3x3 tuple
    return [[round(v, 6) + 0.0 for v in row] for row in m]


def cmd_orient(args: argparse.Namespace, *, step_converter=None) -> OrientPlan:  # noqa: ANN001
    """Rank the faces a model can lie on and report the selected pose."""
    with _loaded(args, "orient", step_converter) as (path, model):
        request, plan = _plan(args, "orient", model, path)
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Orient, Orientation, OrientFace

            emit_json(
                Orient(
                    status="ok",
                    command="orient",
                    file=path,
                    format=model.kind,
                    triangles=model.mesh.triangle_count,
                    objects=model.object_count,
                    as_exported=OrientFace(**_face_payload(plan.file_pose, None)),
                    faces=[OrientFace(**_face_payload(p, n)) for n, p in enumerate(plan.faces, 1)],
                    selected=Orientation(**plan_summary(plan, request)),
                    rotation=_matrix(plan.rotation),
                )
            )
            return plan
        draw_pose(args, model.mesh, plan.rotation)
        _print_ranking(path, model, request, plan)
        return plan


def _print_ranking(path: str, model: LoadedModel, request: OrientRequest, plan: OrientPlan) -> None:
    name = display_safe(os.path.basename(path))
    sx, sy, sz = plan.file_pose.size
    logger.info(f"🧭 {name}: {model.mesh.triangle_count:,} triangles, {sx:.1f} × {sy:.1f} × {sz:.1f} mm as exported")
    if model.object_count != 1:
        logger.info(f"   It places {model.object_count} objects, so it is sliced as exported (platecli turns one).")
    if plan.warning:
        logger.warning(f"⚠️  It {plan.warning}: drop --orient keep to use it.")
    # The ranking is data: stdout, one face per line, so it pipes and greps.
    print(" face    on the bed     overhang    height   stands")
    for n, pose in enumerate(plan.faces, 1):
        notes = []
        if n == 1:
            notes.append("auto")
        if is_identity(pose.rotation):
            notes.append("as exported")
        print(
            f"{n:>5}  {pose.contact_area:>9.0f} mm²  {pose.overhang_area:>8.0f} mm²  "
            f"{pose.height:>6.1f} mm   {'yes' if pose.stable else 'NO '}"
            + (f"   ← {', '.join(notes)}" if notes else "")
        )
    logger.info(turn_line(plan, request))
    flags = request_flags(request)
    logger.info(f"   plate slice {name}{' ' + flags if flags else ''} slices this pose; pick another with")
    logger.info("   --face N, --rotate x=90 (turns stack on the face) or --orient keep (as exported)")


def _preview_path(args: argparse.Namespace, path: str) -> str:
    output = _namespace_get(args, "output", None)
    if output:
        out = _expand_path(output)
        if os.path.isdir(out):
            out = os.path.join(out, os.path.splitext(os.path.basename(path))[0] + "_preview.png")
        return out
    return os.path.splitext(path)[0] + "_preview.png"


def cmd_preview(args: argparse.Namespace, *, step_converter=None) -> str:  # noqa: ANN001
    """Render the selected pose and the ranked faces into one PNG."""
    from bambu_cli.mesh.sheet import render_sheet

    with _loaded(args, "preview", step_converter) as (path, model):
        request, plan = _plan(args, "preview", model, path)
        out = _preview_path(args, path)
        if out.startswith("-") or not out.lower().endswith(".png"):
            emit_json_error(
                args,
                "preview",
                EXIT_COMMAND_ERROR,
                f"--output must name a .png file (got {_path_for_message(out)})",
                failed_step="validate",
                file=path,
            )
        sheet, shown = render_sheet(model.mesh, plan, request, display_safe(os.path.basename(path)))
        try:
            with open(out, "wb") as fh:
                fh.write(sheet.png())
        except OSError as exc:
            emit_json_error(
                args,
                "preview",
                EXIT_FILE_ERROR,
                f"Could not write {_path_for_message(out)}: {exc.strerror or exc}",
                failed_step="write",
                file=path,
            )
        if bool(_namespace_get(args, "json", False)):
            from bambu_cli.contracts import Orientation, Preview

            emit_json(
                Preview(
                    status="ok",
                    command="preview",
                    file=path,
                    output=out,
                    width=sheet.width,
                    height=sheet.height,
                    faces_shown=shown,
                    selected=Orientation(**plan_summary(plan, request)),
                )
            )
        else:
            logger.info(f"🖼️  Preview written: {_path_for_message(out)} ({request.describe()}, {shown} ranked faces)")
        return out
