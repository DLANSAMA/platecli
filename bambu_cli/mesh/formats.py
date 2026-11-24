"""Read STL (binary and ASCII) and OBJ meshes; write binary STL."""

from __future__ import annotations

import os
import struct
from array import array

from bambu_cli.mesh.geometry import MAX_TRIANGLES, Mesh, MeshError

_STL_RECORD = struct.Struct("<12fH")
_STL_VERTS = struct.Struct("<12x9f2x")  # skip the stored normal and attribute
# A text model this large is not a printable part; refuse before reading it.
MAX_MODEL_FILE_BYTES = 1024 * 1024 * 1024


class _VertexPool:
    """De-duplicates vertices so shared corners are stored (and projected) once."""

    def __init__(self) -> None:
        self.index: dict[tuple[float, float, float], int] = {}
        self.xs = array("d")
        self.ys = array("d")
        self.zs = array("d")

    def add(self, x: float, y: float, z: float) -> int:
        key = (x, y, z)
        found = self.index.get(key)
        if found is not None:
            return found
        idx = len(self.xs)
        self.index[key] = idx
        self.xs.append(x)
        self.ys.append(y)
        self.zs.append(z)
        return idx

    def mesh(self, tris: array) -> Mesh:
        return Mesh(self.xs, self.ys, self.zs, tris)


def _too_many(name: str) -> MeshError:
    return MeshError(f"{name} has more than {MAX_TRIANGLES:,} triangles; platecli will not orient a mesh that large.")


def _check_size(path: str) -> int:
    size = os.path.getsize(path)
    if size > MAX_MODEL_FILE_BYTES:
        raise MeshError(f"{os.path.basename(path)} is larger than {MAX_MODEL_FILE_BYTES // (1024 * 1024)} MB.")
    return size


def _is_binary_stl(head: bytes, size: int) -> bool:
    if size < 84 or len(head) < 84:
        return False
    (count,) = struct.unpack_from("<I", head, 80)
    if 84 + 50 * count == size:
        return True
    # "solid" opens ASCII files, but some binary writers put it in the header
    # too, so a matching size wins above. Without "solid" it is binary, and
    # load_stl reports a body shorter than the count declares as truncated.
    return not head.lstrip().lower().startswith(b"solid") and count > 0


def load_stl(path: str) -> Mesh:
    name = os.path.basename(path)
    size = _check_size(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
        if _is_binary_stl(head, size):
            (count,) = struct.unpack_from("<I", head, 80)
            if count > MAX_TRIANGLES:
                raise _too_many(name)
            data = fh.read(50 * count)
            if len(data) < 50 * count:
                raise MeshError(f"{name} is truncated: it declares {count} triangles but holds fewer.")
            return _binary_stl_mesh(data, count)
    return _ascii_stl_mesh(path, name)


def _binary_stl_mesh(data: bytes, count: int) -> Mesh:
    pool = _VertexPool()
    add = pool.add
    tris = array("l")
    for rec in _STL_VERTS.iter_unpack(data[: 50 * count]):
        tris.append(add(rec[0], rec[1], rec[2]))
        tris.append(add(rec[3], rec[4], rec[5]))
        tris.append(add(rec[6], rec[7], rec[8]))
    return pool.mesh(tris)


def _ascii_stl_mesh(path: str, name: str) -> Mesh:
    pool = _VertexPool()
    tris = array("l")
    corner: list[int] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            word = parts[0].lower()
            if word == "vertex":
                if len(parts) < 4:
                    raise MeshError(f"{name}: malformed vertex line {line.strip()[:60]!r}.")
                try:
                    corner.append(pool.add(float(parts[1]), float(parts[2]), float(parts[3])))
                except ValueError as exc:
                    raise MeshError(f"{name}: malformed vertex line {line.strip()[:60]!r}.") from exc
            elif word == "endloop":
                if len(corner) >= 3:
                    for k in range(1, len(corner) - 1):
                        tris.extend((corner[0], corner[k], corner[k + 1]))
                    if len(tris) // 3 > MAX_TRIANGLES:
                        raise _too_many(name)
                corner = []
    if not tris:
        raise MeshError(f"{name} is not a readable STL: no triangles found.")
    return pool.mesh(tris)


def load_obj(path: str) -> Mesh:
    """Wavefront OBJ: ``v`` vertices and ``f`` faces (polygons are fanned)."""
    name = os.path.basename(path)
    _check_size(path)
    xs = array("d")
    ys = array("d")
    zs = array("d")
    tris = array("l")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            if line.startswith("v "):
                parts = line.split()
                try:
                    xs.append(float(parts[1]))
                    ys.append(float(parts[2]))
                    zs.append(float(parts[3]))
                except (IndexError, ValueError) as exc:
                    raise MeshError(f"{name}:{lineno}: malformed vertex.") from exc
            elif line.startswith("f "):
                refs = []
                count = len(xs)
                for token in line.split()[1:]:
                    head = token.split("/", 1)[0]
                    try:
                        ref = int(head)
                    except ValueError as exc:
                        raise MeshError(f"{name}:{lineno}: malformed face.") from exc
                    idx = ref - 1 if ref > 0 else count + ref
                    if ref == 0 or not 0 <= idx < count:
                        raise MeshError(f"{name}:{lineno}: face refers to vertex {ref}, which does not exist.")
                    refs.append(idx)
                for k in range(1, len(refs) - 1):
                    tris.extend((refs[0], refs[k], refs[k + 1]))
                if len(tris) // 3 > MAX_TRIANGLES:
                    raise _too_many(name)
    if not tris:
        raise MeshError(f"{name} has no faces.")
    return Mesh(xs, ys, zs, tris)


def write_stl(path: str, mesh: Mesh, header: bytes = b"platecli oriented mesh") -> None:
    """Binary STL, normals recomputed from the (rotated) geometry."""
    facets = mesh.facets()
    xs, ys, zs, tris = mesh.xs, mesh.ys, mesh.zs, mesh.tris
    nx, ny, nz = facets.nx, facets.ny, facets.nz
    pack = _STL_RECORD.pack
    with open(path, "wb") as fh:
        fh.write(header[:80].ljust(80, b"\0"))
        fh.write(struct.pack("<I", mesh.triangle_count))
        for t in range(mesh.triangle_count):
            i, j, k = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
            fh.write(pack(nx[t], ny[t], nz[t], xs[i], ys[i], zs[i], xs[j], ys[j], zs[j], xs[k], ys[k], zs[k], 0))
