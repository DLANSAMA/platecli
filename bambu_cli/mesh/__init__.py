"""Model geometry: read meshes, decide their orientation on the bed, write them back.

Stdlib only (no numpy). The slicer, the ``orient`` / ``preview`` commands and the
TUI all go through :func:`load_model` and :func:`write_oriented`, so the pose a
person picks in one front-end is the pose every other one would slice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from bambu_cli.constants import DEFAULT_ORIENT_MODE
from bambu_cli.mesh.formats import load_obj, load_stl, write_stl
from bambu_cli.mesh.geometry import IDENTITY, MAX_TRIANGLES, Matrix, Mesh, MeshError, Vec3, is_identity
from bambu_cli.mesh.orient import (
    AXES,
    MAX_FACES,
    ORIENT_MODES,
    Analyzer,
    OrientPlan,
    OrientRequest,
    Pose,
    describe_turn,
    plan_orientation,
)
from bambu_cli.mesh.threemf import (
    ThreeMF,
    compose,
    is_3mf_sliced,
    is_unsliced_3mf,
    load_3mf,
    rewrite_item_transforms,
)

ORIENTABLE_EXTENSIONS = (".stl", ".obj", ".3mf")

__all__ = [
    "AXES",
    "DEFAULT_ORIENT_MODE",
    "IDENTITY",
    "MAX_FACES",
    "MAX_TRIANGLES",
    "ORIENTABLE_EXTENSIONS",
    "ORIENT_MODES",
    "Analyzer",
    "LoadedModel",
    "Matrix",
    "Mesh",
    "MeshError",
    "OrientPlan",
    "OrientRequest",
    "Pose",
    "Vec3",
    "describe_turn",
    "is_3mf_sliced",
    "is_unsliced_3mf",
    "is_identity",
    "load_model",
    "plan_orientation",
    "write_oriented",
]


@dataclass
class LoadedModel:
    path: str
    kind: str  # "stl" | "obj" | "3mf"
    mesh: Mesh
    project: ThreeMF | None = None

    @property
    def object_count(self) -> int:
        return len(self.project.items) if self.project is not None else 1


def load_model(path: str) -> LoadedModel:
    """Read an STL, OBJ or (unsliced or sliced) 3MF into world-space millimetres."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        return LoadedModel(path, "stl", load_stl(path))
    if ext == ".obj":
        return LoadedModel(path, "obj", load_obj(path))
    if ext == ".3mf":
        project = load_3mf(path)
        return LoadedModel(path, "3mf", project.mesh, project)
    raise MeshError(f"Cannot read {os.path.basename(path)}: platecli orients .stl, .obj and .3mf models.")


def write_oriented(model: LoadedModel, plan: OrientPlan, out_dir: str) -> str:
    """Write the model in ``plan``'s pose into ``out_dir``; return the new path.

    A mesh becomes a binary STL. A 3MF keeps every byte of its project except
    the build item's placement, so its settings, paint and modifiers survive.
    """
    stem = os.path.splitext(os.path.basename(model.path))[0]
    if model.project is None:
        out = os.path.join(out_dir, f"{stem}.stl")
        write_stl(out, model.mesh.rotated(plan.rotation).translated(plan.translation))
        return out
    project = model.project
    if len(project.items) != 1:
        raise MeshError(
            f"{os.path.basename(model.path)} places {len(project.items)} objects; platecli orients a 3MF "
            "with exactly one. Export the object you want as STL, or orient it in OrcaSlicer."
        )
    scale = project.unit_mm
    t = plan.translation
    placement = (plan.rotation, (t[0] / scale, t[1] / scale, t[2] / scale))
    new_transform = compose(placement, project.items[0].transform)
    out = os.path.join(out_dir, f"{stem}.3mf")
    rewrite_item_transforms(model.path, out, project.root_path, {0: new_transform})
    return out
