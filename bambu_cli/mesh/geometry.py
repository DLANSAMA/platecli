"""A triangle mesh in flat stdlib arrays, and the rigid rotations platecli applies to it.

Pure Python on purpose: platecli does not depend on numpy, and the work here is
a handful of linear passes over the vertices. Coordinates are millimetres.
Rotations are 3x3 row-major tuples that act on column vectors (``v' = R v``).
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field

from bambu_cli.constants import EXIT_FILE_ERROR
from bambu_cli.errors import FileError

Vec3 = tuple[float, float, float]
Matrix = tuple[Vec3, Vec3, Vec3]

IDENTITY: Matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

# A mesh bigger than this is refused rather than slowly eating the machine's
# memory in pure Python (about 60 bytes per triangle once loaded).
MAX_TRIANGLES = 2_000_000


class MeshError(FileError):
    """A model file that cannot be read or oriented."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_FILE_ERROR, failed_step="orient")


def matmul(a: Matrix, b: Matrix) -> Matrix:
    """``a @ b``: apply ``b`` first, then ``a``."""
    rows = []
    for i in range(3):
        rows.append(
            (
                a[i][0] * b[0][0] + a[i][1] * b[1][0] + a[i][2] * b[2][0],
                a[i][0] * b[0][1] + a[i][1] * b[1][1] + a[i][2] * b[2][1],
                a[i][0] * b[0][2] + a[i][1] * b[1][2] + a[i][2] * b[2][2],
            )
        )
    return (rows[0], rows[1], rows[2])


def apply(m: Matrix, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _trig(degrees: float) -> tuple[float, float]:
    """cos/sin with exact values on quarter turns (no 6e-17 residue in a 90° turn)."""
    quarter = degrees / 90.0
    if quarter == int(quarter):
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarter) % 4]
    rad = math.radians(degrees)
    return math.cos(rad), math.sin(rad)


def rotation_about(axis: str, degrees: float) -> Matrix:
    """Right-handed rotation of ``degrees`` about the world ``x``, ``y`` or ``z`` axis."""
    c, s = _trig(degrees)
    if axis == "x":
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == "y":
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    if axis == "z":
        return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    raise ValueError(f"unknown axis {axis!r}")


def normalize(v: Vec3) -> Vec3:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length == 0.0:
        raise ValueError("zero vector")
    return (v[0] / length, v[1] / length, v[2] / length)


def rotation_between(u: Vec3, v: Vec3) -> Matrix:
    """The smallest rotation taking unit vector ``u`` onto unit vector ``v``."""
    u = normalize(u)
    v = normalize(v)
    cos_t = u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    if cos_t > 1.0 - 1e-12:
        return IDENTITY
    if cos_t < -1.0 + 1e-12:
        # Half turn about any axis perpendicular to u. Prefer a world axis so a
        # face that already points straight up flips cleanly about X.
        helper = (1.0, 0.0, 0.0) if abs(u[0]) < 0.9 else (0.0, 1.0, 0.0)
        axis = normalize(cross(u, helper))
        x, y, z = axis
        return (
            (2 * x * x - 1, 2 * x * y, 2 * x * z),
            (2 * x * y, 2 * y * y - 1, 2 * y * z),
            (2 * x * z, 2 * y * z, 2 * z * z - 1),
        )
    k = cross(u, v)
    s2 = k[0] * k[0] + k[1] * k[1] + k[2] * k[2]
    f = (1.0 - cos_t) / s2
    kx, ky, kz = k
    # Rodrigues: R = I + [k]x + [k]x^2 * (1 - cos) / sin^2
    return (
        (1 - f * (ky * ky + kz * kz), -kz + f * kx * ky, ky + f * kx * kz),
        (kz + f * kx * ky, 1 - f * (kx * kx + kz * kz), -kx + f * ky * kz),
        (-ky + f * kx * kz, kx + f * ky * kz, 1 - f * (kx * kx + ky * ky)),
    )


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def clean_matrix(m: Matrix) -> Matrix:
    """Snap float noise (|x| < 1e-12) to 0 and near-units to ±1 for stable output."""

    def snap(x: float) -> float:
        if abs(x) < 1e-12:
            return 0.0
        if abs(abs(x) - 1.0) < 1e-12:
            return math.copysign(1.0, x)
        return x

    return (
        (snap(m[0][0]), snap(m[0][1]), snap(m[0][2])),
        (snap(m[1][0]), snap(m[1][1]), snap(m[1][2])),
        (snap(m[2][0]), snap(m[2][1]), snap(m[2][2])),
    )


def is_identity(m: Matrix, tol: float = 1e-9) -> bool:
    return all(abs(m[i][j] - IDENTITY[i][j]) <= tol for i in range(3) for j in range(3))


@dataclass
class Facets:
    """Per-triangle unit normals and areas, plus whole-mesh totals."""

    nx: array
    ny: array
    nz: array
    area: array
    total_area: float
    centroid: Vec3  # centre of mass for a closed mesh, area centroid otherwise
    closed: bool  # encloses a volume (so back faces can be skipped when drawing)


@dataclass
class Mesh:
    """Indexed triangles: vertex ``i`` is ``(xs[i], ys[i], zs[i])``; triangle ``t``
    uses vertices ``tris[3t]``, ``tris[3t+1]``, ``tris[3t+2]``."""

    xs: array
    ys: array
    zs: array
    tris: array
    _facets: Facets | None = field(default=None, repr=False, compare=False)

    @classmethod
    def empty(cls) -> Mesh:
        return cls(array("d"), array("d"), array("d"), array("l"))

    @property
    def vertex_count(self) -> int:
        return len(self.xs)

    @property
    def triangle_count(self) -> int:
        return len(self.tris) // 3

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.xs:
            raise MeshError("The model has no vertices.")
        return (
            (min(self.xs), min(self.ys), min(self.zs)),
            (max(self.xs), max(self.ys), max(self.zs)),
        )

    def size(self) -> Vec3:
        lo, hi = self.bounds()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def projection(self, d: Vec3) -> list[float]:
        """``v . d`` for every vertex (the coordinate along ``d``)."""
        a, b, c = d
        return [a * x + b * y + c * z for x, y, z in zip(self.xs, self.ys, self.zs, strict=True)]

    def rotated(self, m: Matrix) -> Mesh:
        """A copy with every vertex rotated by ``m`` about the origin."""
        r0, r1, r2 = m
        xs, ys, zs = self.xs, self.ys, self.zs
        return Mesh(
            array("d", [r0[0] * x + r0[1] * y + r0[2] * z for x, y, z in zip(xs, ys, zs, strict=True)]),
            array("d", [r1[0] * x + r1[1] * y + r1[2] * z for x, y, z in zip(xs, ys, zs, strict=True)]),
            array("d", [r2[0] * x + r2[1] * y + r2[2] * z for x, y, z in zip(xs, ys, zs, strict=True)]),
            self.tris,
        )

    def translated(self, t: Vec3) -> Mesh:
        tx, ty, tz = t
        return Mesh(
            array("d", [x + tx for x in self.xs]),
            array("d", [y + ty for y in self.ys]),
            array("d", [z + tz for z in self.zs]),
            self.tris,
        )

    def facets(self) -> Facets:
        """Normals, areas and centroid, computed once and cached."""
        if self._facets is not None:
            return self._facets
        xs, ys, zs, tris = self.xs, self.ys, self.zs, self.tris
        nx = array("d")
        ny = array("d")
        nz = array("d")
        area = array("d")
        total = 0.0
        vol6 = 0.0
        cvx = cvy = cvz = 0.0  # volume-weighted centroid accumulators
        cax = cay = caz = 0.0  # area-weighted centroid accumulators
        if not tris:
            raise MeshError("The model has no surface (every triangle is degenerate).")
        ox, oy, oz = xs[tris[0]], ys[tris[0]], zs[tris[0]]
        for t in range(0, len(tris), 3):
            i, j, k = tris[t], tris[t + 1], tris[t + 2]
            ax, ay, az = xs[i], ys[i], zs[i]
            ux, uy, uz = xs[j] - ax, ys[j] - ay, zs[j] - az
            vx, vy, vz = xs[k] - ax, ys[k] - ay, zs[k] - az
            cx = uy * vz - uz * vy
            cy = uz * vx - ux * vz
            cz = ux * vy - uy * vx
            length = math.sqrt(cx * cx + cy * cy + cz * cz)
            if length > 0.0:
                nx.append(cx / length)
                ny.append(cy / length)
                nz.append(cz / length)
            else:
                nx.append(0.0)
                ny.append(0.0)
                nz.append(0.0)
            a = 0.5 * length
            area.append(a)
            total += a
            mx = (ax + xs[j] + xs[k]) / 3.0
            my = (ay + ys[j] + ys[k]) / 3.0
            mz = (az + zs[j] + zs[k]) / 3.0
            cax += a * mx
            cay += a * my
            caz += a * mz
            # Signed volume of the tetrahedron (ref_origin, a, b, c), times 6.
            # Shifting to a vertex on the mesh keeps open planar meshes at zero
            # volume regardless of translation and avoids far-origin cancellation.
            v6 = (ax - ox) * cx + (ay - oy) * cy + (az - oz) * cz
            vol6 += v6
            # Tetra centroid relative to ref_origin is 0.75 * (tri_centroid - ref_origin).
            cvx += v6 * 0.75 * (mx - ox)
            cvy += v6 * 0.75 * (my - oy)
            cvz += v6 * 0.75 * (mz - oz)
        if total <= 0.0:
            raise MeshError("The model has no surface (every triangle is degenerate).")
        # A closed, consistently wound mesh has a real volume; an open or
        # inconsistent one does not, and its surface centroid is the best guess.
        closed = abs(vol6) > 1e-9 * max(total, 1.0) ** 1.5
        centroid = (
            (ox + cvx / vol6, oy + cvy / vol6, oz + cvz / vol6) if closed else (cax / total, cay / total, caz / total)
        )
        if closed and vol6 < 0.0:
            # Closed mesh wound inside out (normals point inward): flip them
            # so every "down-facing" test sees outward normals. Never flip an
            # open mesh whose tiny residual signed volume happens to be negative.
            for arr in (nx, ny, nz):
                for idx in range(len(arr)):
                    arr[idx] = -arr[idx]
        self._facets = Facets(nx, ny, nz, area, total, centroid, closed)
        return self._facets
