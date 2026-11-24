"""Read the geometry of a 3MF package and rewrite its build-item placement.

Only what orientation needs: the meshes, the component tree, the ``<build>``
items and their transforms. Everything else in the package (Bambu/Orca project
settings, per-object settings, paint data, thumbnails) is copied through
byte-for-byte when the placement is rewritten, so the project keeps its settings.

XML is parsed with expat and refused outright if it declares a DTD or an entity,
which rules out entity-expansion bombs; every zip member is size-checked first.
"""

from __future__ import annotations

import re
import zipfile
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import IO
from xml.parsers import expat

from bambu_cli.mesh.formats import MAX_MODEL_FILE_BYTES
from bambu_cli.mesh.geometry import IDENTITY, MAX_TRIANGLES, Matrix, Mesh, MeshError, Vec3, apply, matmul

ROOT_MODEL_DEFAULT = "3D/3dmodel.model"
_MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_UNITS_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}
_MAX_COMPONENT_DEPTH = 16
_MAX_RELS_BYTES = 1024 * 1024

Affine = tuple[Matrix, Vec3]
IDENTITY_AFFINE: Affine = (IDENTITY, (0.0, 0.0, 0.0))


def compose(a: Affine, b: Affine) -> Affine:
    """Apply ``b`` first, then ``a``."""
    ra, ta = a
    rb, tb = b
    moved = apply(ra, tb)
    return matmul(ra, rb), (moved[0] + ta[0], moved[1] + ta[1], moved[2] + ta[2])


def parse_transform(text: str | None) -> Affine:
    """3MF's 12-number row-vector matrix (``x' = [x y z 1] M``) as ``(R, t)``."""
    if text is None or not text.strip():
        return IDENTITY_AFFINE
    try:
        m = [float(v) for v in text.split()]
    except ValueError as exc:
        raise MeshError(f"Malformed 3MF transform {text[:80]!r}.") from exc
    if len(m) != 12:
        raise MeshError(f"Malformed 3MF transform {text[:80]!r}: expected 12 numbers.")
    rot: Matrix = ((m[0], m[3], m[6]), (m[1], m[4], m[7]), (m[2], m[5], m[8]))
    return rot, (m[9], m[10], m[11])


def format_transform(affine: Affine) -> str:
    """Inverse of :func:`parse_transform`."""
    r, t = affine
    values = [r[0][0], r[1][0], r[2][0], r[0][1], r[1][1], r[2][1], r[0][2], r[1][2], r[2][2], t[0], t[1], t[2]]
    return " ".join(_fmt(v) for v in values)


def _fmt(v: float) -> str:
    if abs(v) < 1e-12:
        return "0"
    text = f"{v:.9f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


@dataclass
class _Object:
    xs: array = field(default_factory=lambda: array("d"))
    ys: array = field(default_factory=lambda: array("d"))
    zs: array = field(default_factory=lambda: array("d"))
    tris: array = field(default_factory=lambda: array("l"))
    components: list[tuple[str, str, Affine]] = field(default_factory=list)


@dataclass(frozen=True)
class BuildItem:
    objectid: str
    transform: Affine  # in the model's own unit
    printable: bool


@dataclass
class ThreeMF:
    """The world-space geometry of a 3MF's build plus what a rewrite needs."""

    mesh: Mesh  # every build item, in millimetres, world coordinates
    items: list[BuildItem]
    unit_mm: float
    root_path: str
    item_meshes: list[Mesh]


class _Rejected(Exception):
    pass


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _attr(attrs: dict[str, str], wanted: str) -> str | None:
    if wanted in attrs:
        return attrs[wanted]
    for key, value in attrs.items():
        if _local(key) == wanted:
            return value
    return None


def _safe_parser() -> expat.XMLParserType:
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def refuse(*_args: object) -> None:
        raise _Rejected("DTDs and entity declarations are not allowed in a 3MF model.")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse  # type: ignore[assignment]
    return parser


def _open_member(zf: zipfile.ZipFile, name: str, limit: int) -> IO[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise MeshError(f"The 3MF is missing {name}.") from exc
    if info.file_size > limit:
        raise MeshError(f"{name} inside the 3MF is larger than {limit // (1024 * 1024)} MB.")
    return zf.open(info)


def _root_model_path(zf: zipfile.ZipFile) -> str:
    names = set(zf.namelist())
    if "_rels/.rels" in names:
        with _open_member(zf, "_rels/.rels", _MAX_RELS_BYTES) as fh:
            text = fh.read().decode("utf-8", "replace")
        for match in re.finditer(r"<Relationship\b[^>]*>", text):
            tag = match.group(0)
            rel_type = re.search(r'\bType="([^"]*)"', tag)
            target = re.search(r'\bTarget="([^"]*)"', tag)
            if rel_type and target and rel_type.group(1) == _MODEL_REL_TYPE:
                path = target.group(1).lstrip("/")
                if path in names:
                    return path
    if ROOT_MODEL_DEFAULT in names:
        return ROOT_MODEL_DEFAULT
    raise MeshError("Not a 3MF model: no 3D model part found.")


class _PartParser:
    """Streams one ``.model`` part into ``_Object`` records."""

    def __init__(self, part: str, budget: list[int]) -> None:
        self.part = part
        self.budget = budget  # [triangles still allowed], shared across parts
        self.objects: dict[str, _Object] = {}
        self.items: list[BuildItem] = []
        self.unit = "millimeter"
        self._current: _Object | None = None
        self._in_build = False

    def start(self, name: str, attrs: dict[str, str]) -> None:
        tag = _local(name)
        obj = self._current
        if tag == "vertex" and obj is not None:
            try:
                obj.xs.append(float(attrs["x"]))
                obj.ys.append(float(attrs["y"]))
                obj.zs.append(float(attrs["z"]))
            except (KeyError, ValueError) as exc:
                raise MeshError(f"Malformed vertex in {self.part}.") from exc
        elif tag == "triangle" and obj is not None:
            try:
                obj.tris.extend((int(attrs["v1"]), int(attrs["v2"]), int(attrs["v3"])))
            except (KeyError, ValueError) as exc:
                raise MeshError(f"Malformed triangle in {self.part}.") from exc
            self.budget[0] -= 1
            if self.budget[0] < 0:
                raise MeshError(
                    f"The 3MF has more than {MAX_TRIANGLES:,} triangles; platecli will not orient a mesh that large."
                )
        elif tag == "object":
            obj_id = _attr(attrs, "id")
            if obj_id is None:
                raise MeshError(f"An object in {self.part} has no id.")
            self._current = _Object()
            self.objects[obj_id] = self._current
        elif tag == "component" and obj is not None:
            ref = _attr(attrs, "objectid")
            if ref is None:
                raise MeshError(f"A component in {self.part} has no objectid.")
            path = _attr(attrs, "path")
            target = path.lstrip("/") if path else self.part
            obj.components.append((target, ref, parse_transform(_attr(attrs, "transform"))))
        elif tag == "build":
            self._in_build = True
        elif tag == "item" and self._in_build:
            ref = _attr(attrs, "objectid")
            if ref is None:
                raise MeshError("A 3MF build item has no objectid.")
            printable = (_attr(attrs, "printable") or "1").strip() not in ("0", "false")
            self.items.append(BuildItem(ref, parse_transform(_attr(attrs, "transform")), printable))
        elif tag == "model":
            self.unit = attrs.get("unit", "millimeter")

    def end(self, name: str) -> None:
        tag = _local(name)
        if tag == "object":
            self._current = None
        elif tag == "build":
            self._in_build = False

    def parse(self, fh: IO[bytes]) -> None:
        parser = _safe_parser()
        parser.StartElementHandler = self.start
        parser.EndElementHandler = self.end
        try:
            parser.ParseFile(fh)
        except _Rejected as exc:
            raise MeshError(str(exc)) from exc
        except expat.ExpatError as exc:
            raise MeshError(f"{self.part} is not well-formed XML: {exc}") from exc


def is_3mf_sliced(path: str) -> bool:
    """True when the package carries sliced G-code (``Metadata/plate_<n>.gcode``)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return any(re.match(r"^Metadata/plate_\d+\.gcode$", n.replace("\\", "/")) for n in zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def is_unsliced_3mf(path: str) -> bool:
    """True for a readable 3MF that holds a model but no sliced G-code: a project to slice."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n.replace("\\", "/") for n in zf.namelist()]
            if any(re.match(r"^Metadata/plate_\d+\.gcode$", n) for n in names):
                return False
            _root_model_path(zf)
            return True
    except (OSError, zipfile.BadZipFile, MeshError):
        return False


def load_3mf(path: str) -> ThreeMF:
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MeshError(f"Not a readable 3MF (zip) file: {exc}") from exc
    with zf:
        root = _root_model_path(zf)
        budget = [MAX_TRIANGLES]
        parts: dict[str, _PartParser] = {}

        def part(name: str) -> _PartParser:
            if name not in parts:
                parser = _PartParser(name, budget)
                with _open_member(zf, name, MAX_MODEL_FILE_BYTES) as fh:
                    parser.parse(fh)
                parts[name] = parser
            return parts[name]

        root_part = part(root)
        unit_mm = _UNITS_MM.get(root_part.unit)
        if unit_mm is None:
            raise MeshError(f"Unknown 3MF unit {root_part.unit!r}.")
        if not root_part.items:
            raise MeshError("The 3MF has no build items (nothing is placed on the plate).")

        item_meshes: list[Mesh] = []
        for item in root_part.items:
            xs = array("d")
            ys = array("d")
            zs = array("d")
            tris = array("l")
            _flatten(part, root, item.objectid, item.transform, xs, ys, zs, tris, depth=0, seen=())
            if unit_mm != 1.0:
                xs = array("d", [v * unit_mm for v in xs])
                ys = array("d", [v * unit_mm for v in ys])
                zs = array("d", [v * unit_mm for v in zs])
            item_meshes.append(Mesh(xs, ys, zs, tris))

    merged = _merge(item_meshes)
    if merged.triangle_count == 0:
        raise MeshError("The 3MF build holds no triangles.")
    return ThreeMF(merged, list(root_part.items), unit_mm, root, item_meshes)


def _flatten(
    part_of: Callable[[str], _PartParser],
    part_name: str,
    obj_id: str,
    affine: Affine,
    xs: array,
    ys: array,
    zs: array,
    tris: array,
    *,
    depth: int,
    seen: tuple[tuple[str, str], ...],
) -> None:
    key = (part_name, obj_id)
    if depth > _MAX_COMPONENT_DEPTH or key in seen:
        raise MeshError("The 3MF component tree is cyclic or too deep.")
    obj = part_of(part_name).objects.get(obj_id)
    if obj is None:
        raise MeshError(f"The 3MF refers to object {obj_id} in {part_name}, which does not exist.")
    if obj.tris:
        base = len(xs)
        count = len(obj.xs)
        (r0, r1, r2), (tx, ty, tz) = affine
        for x, y, z in zip(obj.xs, obj.ys, obj.zs, strict=True):
            xs.append(r0[0] * x + r0[1] * y + r0[2] * z + tx)
            ys.append(r1[0] * x + r1[1] * y + r1[2] * z + ty)
            zs.append(r2[0] * x + r2[1] * y + r2[2] * z + tz)
        for idx in obj.tris:
            if not 0 <= idx < count:
                raise MeshError(f"A triangle in object {obj_id} refers to a vertex that does not exist.")
            tris.append(base + idx)
    for comp_part, comp_id, comp_affine in obj.components:
        _flatten(
            part_of,
            comp_part,
            comp_id,
            compose(affine, comp_affine),
            xs,
            ys,
            zs,
            tris,
            depth=depth + 1,
            seen=(*seen, key),
        )


def _merge(meshes: list[Mesh]) -> Mesh:
    if len(meshes) == 1:
        return meshes[0]
    out = Mesh.empty()
    for m in meshes:
        base = len(out.xs)
        out.xs.extend(m.xs)
        out.ys.extend(m.ys)
        out.zs.extend(m.zs)
        out.tris.extend(i + base for i in m.tris)
    return out


_ITEM_TAG = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?item\b[^>]*>")
_BUILD_SPAN = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?build\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?build>", re.S)
_TRANSFORM_ATTR = re.compile(r'(\stransform\s*=\s*)(["\'])(.*?)\2', re.S)


def rewrite_item_transforms(src: str, dst: str, root_path: str, transforms: dict[int, Affine]) -> None:
    """Copy ``src`` to ``dst`` with build item ``i``'s transform replaced.

    A targeted text edit of the ``<build>`` section rather than an XML
    round-trip: re-serialising would rename namespace prefixes, which the
    Bambu/Orca loaders are not obliged to accept.
    """
    with zipfile.ZipFile(src) as zin:
        with _open_member(zin, root_path, MAX_MODEL_FILE_BYTES) as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MeshError(f"{root_path} is not UTF-8 text.") from exc
        build = _BUILD_SPAN.search(text)
        if build is None:
            raise MeshError("The 3MF has no <build> section to rewrite.")
        counter = [0]

        def edit(match: re.Match[str]) -> str:
            index = counter[0]
            counter[0] += 1
            tag = match.group(0)
            if index not in transforms:
                return tag
            value = format_transform(transforms[index])
            if _TRANSFORM_ATTR.search(tag):
                return _TRANSFORM_ATTR.sub(lambda m: f"{m.group(1)}{m.group(2)}{value}{m.group(2)}", tag, count=1)
            head = re.match(r"<(?:[A-Za-z_][\w.-]*:)?item\b", tag)
            assert head is not None  # noqa: S101 -- _ITEM_TAG guarantees the prefix
            return f'{tag[: head.end()]} transform="{value}"{tag[head.end() :]}'

        new_build = _ITEM_TAG.sub(edit, build.group(0))
        if counter[0] <= max(transforms, default=-1):
            raise MeshError("The 3MF build has fewer items than expected.")
        new_text = text[: build.start()] + new_build + text[build.end() :]
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                if info.filename == root_path:
                    zout.writestr(info, new_text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
                else:
                    if info.file_size > MAX_MODEL_FILE_BYTES:
                        raise MeshError(f"{info.filename} inside the 3MF is too large to copy.")
                    zout.writestr(info, zin.read(info), compress_type=info.compress_type)


def item_affine_mm(item: BuildItem, unit_mm: float) -> Affine:
    """A build item's transform with its translation expressed in millimetres."""
    rot, t = item.transform
    return rot, (t[0] * unit_mm, t[1] * unit_mm, t[2] * unit_mm)
