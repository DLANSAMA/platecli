"""Tiny synthetic meshes and 3MF packages for the mesh tests."""

from __future__ import annotations

import zipfile
from array import array

from bambu_cli.mesh.geometry import Mesh, rotation_about

MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/2013/01"


def make_mesh(verts: list[tuple[float, float, float]], tris: list[tuple[int, int, int]]) -> Mesh:
    return Mesh(
        array("d", [v[0] for v in verts]),
        array("d", [v[1] for v in verts]),
        array("d", [v[2] for v in verts]),
        array("l", [i for t in tris for i in t]),
    )


def box(sx: float = 10.0, sy: float = 10.0, sz: float = 10.0, origin=(0.0, 0.0, 0.0), reverse: bool = False) -> Mesh:
    """Closed axis-aligned box, 8 vertices / 12 triangles, outward CCW winding."""
    ox, oy, oz = origin
    verts = [
        (ox, oy, oz),
        (ox + sx, oy, oz),
        (ox + sx, oy + sy, oz),
        (ox, oy + sy, oz),
        (ox, oy, oz + sz),
        (ox + sx, oy, oz + sz),
        (ox + sx, oy + sy, oz + sz),
        (ox, oy + sy, oz + sz),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (3, 7, 6, 2), (0, 4, 7, 3), (1, 2, 6, 5)]
    tris = []
    for a, b, c, d in quads:
        tris += [(a, b, c), (a, c, d)]
    if reverse:
        tris = [(a, c, b) for a, b, c in tris]
    return make_mesh(verts, tris)


def pyramid_up(side: float = 20.0, height: float = 20.0) -> Mesh:
    """Square pyramid, base on z=0, apex up."""
    h = side / 2
    verts = [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0), (h, h, height)]
    tris = [(0, 3, 2), (0, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    return make_mesh(verts, tris)


def pyramid_down(side: float = 20.0, height: float = 20.0) -> Mesh:
    """The same pyramid upside down: apex at z=0 (the file pose balances on a point)."""
    return pyramid_up(side, height).rotated(rotation_about("x", 180)).translated((0.0, side, height))


def thin_plate() -> Mesh:
    """60 x 3 x 40 mm plate standing on its 60 x 3 edge."""
    return box(60.0, 3.0, 40.0)


def _transform_attr(t: str | None) -> str:
    return f' transform="{t}"' if t else ""


def _mesh_xml(mesh: Mesh) -> str:
    vs = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in zip(mesh.xs, mesh.ys, mesh.zs, strict=True))
    ts = "".join(
        f'<triangle v1="{mesh.tris[i]}" v2="{mesh.tris[i + 1]}" v3="{mesh.tris[i + 2]}"/>'
        for i in range(0, len(mesh.tris), 3)
    )
    return f"<mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh>"


def model_xml(objects: dict, build: list, unit: str | None = "millimeter") -> str:
    """objects: id -> Mesh | list[(objectid, transform|None, path|None)]; build: [(objectid, transform|None)]."""
    unit_attr = f' unit="{unit}"' if unit else ""
    body = ""
    for oid, spec in objects.items():
        if isinstance(spec, Mesh):
            inner = _mesh_xml(spec)
        else:
            comps = "".join(
                f'<component objectid="{c}"{_transform_attr(t)}' + (f' path="{pth}"' if pth else "") + "/>"
                for c, t, pth in spec
            )
            inner = f"<components>{comps}</components>"
        body += f'<object id="{oid}" type="model">{inner}</object>'
    items = "".join(f'<item objectid="{o}"{_transform_attr(t)}/>' for o, t in build)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<model{unit_attr} xmlns="{CORE_NS}">'
        f"<resources>{body}</resources><build>{items}</build></model>"
    )


def write_3mf(
    path,
    objects: dict,
    build: list,
    unit: str | None = "millimeter",
    gcode: bool = False,
    root: str = "3D/3dmodel.model",
    rels: bool = False,
    extra: dict[str, bytes] | None = None,
    xml: str | None = None,
) -> str:
    """Write a minimal 3MF zip. ``rels`` adds _rels/.rels pointing at ``root``."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if rels:
            zf.writestr(
                "_rels/.rels",
                f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Target="/{root}" Id="rel0" Type="{MODEL_REL}"/></Relationships>',
            )
        zf.writestr(root, xml if xml is not None else model_xml(objects, build, unit))
        zf.writestr("Metadata/project_settings.config", b'{ "layer_height": 0.2 }\n')
        zf.writestr("Metadata/thumb.png", bytes(range(256)))
        if gcode:
            zf.writestr("Metadata/plate_1.gcode", "G28\n")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return str(path)


def flared_on_stem() -> Mesh:
    """A 2 mm stem under an inverted frustum (2 mm wide at z=5, 40 mm wide at z=15).

    Its sloped sides are down-facing, steeper than 45 degrees past vertical, and float above the bed.
    """
    frustum = box(40.0, 40.0, 10.0, origin=(0.0, 0.0, 5.0))
    for i in range(4):  # bottom ring of the frustum: pull the corners in
        frustum.xs[i] = 19.0 if frustum.xs[i] == 0.0 else 21.0
        frustum.ys[i] = 19.0 if frustum.ys[i] == 0.0 else 21.0
    stem = box(2.0, 2.0, 5.0, origin=(19.0, 19.0, 0.0))
    n = frustum.vertex_count
    frustum.xs.extend(stem.xs)
    frustum.ys.extend(stem.ys)
    frustum.zs.extend(stem.zs)
    frustum.tris.extend(i + n for i in stem.tris)
    return frustum
