"""Choose how a model stands on the bed: rank faces to lay it on, resolve a request.

A *pose* is a rotation of the model. platecli scores each candidate pose by what
decides whether a print works: how much of it touches the bed (adhesion), how
much of it hangs out over air (supports), whether its centre of mass sits over
what touches the bed (stability) and how tall it ends up (time, wobble).

Candidates are the model's flat faces: triangles are binned by normal and the
largest bins are tried face-down, plus the pose the file already has and the six
axis-aligned ones. Ranking is a suggestion; the person still sees and picks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from bambu_cli.mesh.geometry import (
    IDENTITY,
    Matrix,
    Mesh,
    Vec3,
    apply,
    clean_matrix,
    is_identity,
    matmul,
    normalize,
    rotation_about,
    rotation_between,
)

# A triangle within this distance of the lowest point prints in the first layer.
CONTACT_EPS_MM = 0.1
# ...and counts as resting on the bed when it faces within ~25° of straight down.
DOWN_COS = 0.9
# Down-facing surface steeper than 45° past vertical needs support.
OVERHANG_COS = math.cos(math.radians(45))
# Below this much bed contact a model is standing on a point or an edge.
MIN_CONTACT_MM2 = 0.5
MAX_FACES = 8
_MAX_CANDIDATES = 32
_BIN_STEPS = 24  # normal-bin resolution: about 2.4° per step
_SAME_DIRECTION_COS = math.cos(math.radians(3))
_FILE_POSE_BONUS = 0.02
_UNSTABLE_PENALTY = 10.0

ORIENT_MODES = ("keep", "auto")
AXES = ("x", "y", "z")
_DOWN: Vec3 = (0.0, 0.0, -1.0)


@dataclass(frozen=True)
class Pose:
    """One way to stand the model on the bed, and how it would print."""

    rotation: Matrix
    height: float
    contact_area: float
    overhang_area: float
    stable: bool
    score: float
    size: Vec3

    @property
    def down(self) -> Vec3:
        """The model-frame direction that faces the bed in this pose."""
        r = self.rotation
        return (-r[2][0], -r[2][1], -r[2][2])


@dataclass
class _Bins:
    keys: list[tuple[int, int, int]]
    area: list[float]
    normal: list[Vec3]
    members: list[list[int]]


def _bin_normals(mesh: Mesh) -> _Bins:
    facets = mesh.facets()
    index: dict[tuple[int, int, int], int] = {}
    bins = _Bins([], [], [], [])
    sums: list[list[float]] = []
    q = _BIN_STEPS
    for t, (nx, ny, nz, a) in enumerate(zip(facets.nx, facets.ny, facets.nz, facets.area, strict=True)):
        if a <= 0.0:
            continue
        key = (round(nx * q), round(ny * q), round(nz * q))
        slot = index.get(key)
        if slot is None:
            slot = len(bins.keys)
            index[key] = slot
            bins.keys.append(key)
            bins.area.append(0.0)
            bins.members.append([])
            sums.append([0.0, 0.0, 0.0])
        bins.area[slot] += a
        bins.members[slot].append(t)
        s = sums[slot]
        s[0] += nx * a
        s[1] += ny * a
        s[2] += nz * a
    for s in sums:
        try:
            bins.normal.append(normalize((s[0], s[1], s[2])))
        except ValueError:
            bins.normal.append((0.0, 0.0, 0.0))
    return bins


def _cross2(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """2D convex hull (Andrew's monotone chain), counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and _cross2(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross2(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inside(hull: list[tuple[float, float]], p: tuple[float, float], margin: float = 1e-6) -> bool:
    if len(hull) < 3:
        return False
    return all(_cross2(hull[i], hull[(i + 1) % len(hull)], p) >= -margin for i in range(len(hull)))


class Analyzer:
    """Scores poses of one mesh; the per-mesh work (normals, bins) is done once."""

    def __init__(self, mesh: Mesh) -> None:
        self.mesh = mesh
        self.facets = mesh.facets()
        self.bins = _bin_normals(mesh)
        lo, hi = mesh.bounds()
        self.diag = max(math.dist(lo, hi), 1e-6)
        self._ranked: list[Pose] | None = None

    def pose(self, rotation: Matrix) -> Pose:
        rotation = clean_matrix(rotation)
        mesh, facets, bins = self.mesh, self.facets, self.bins
        up = rotation[2]
        z = mesh.projection(up)
        zmin = min(z)
        height = max(z) - zmin
        ux, uy, uz = up

        overhang_total = 0.0
        contact = 0.0
        contact_raw = 0.0
        contact_vertices: set[int] = set()
        limit = zmin + CONTACT_EPS_MM
        tris = mesh.tris
        nxs, nys, nzs, areas = facets.nx, facets.ny, facets.nz, facets.area
        # Bins are ~2.4° wide, so test a slightly wider cone than DOWN_COS and
        # then decide per triangle.
        down_bin_cos = DOWN_COS - 0.05
        for normal, area, members in zip(bins.normal, bins.area, bins.members, strict=True):
            facing = ux * normal[0] + uy * normal[1] + uz * normal[2]
            if facing < -OVERHANG_COS:
                overhang_total += area
            if facing >= -down_bin_cos:
                continue
            for t in members:
                nzp = ux * nxs[t] + uy * nys[t] + uz * nzs[t]
                if nzp >= -DOWN_COS:
                    continue
                i, j, k = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
                if z[i] <= limit and z[j] <= limit and z[k] <= limit:
                    contact += areas[t] * -nzp
                    contact_raw += areas[t]
                    contact_vertices.update((i, j, k))
        overhang = max(0.0, overhang_total - contact_raw)

        px = mesh.projection(rotation[0])
        py = mesh.projection(rotation[1])
        size = (max(px) - min(px), max(py) - min(py), height)

        stable = False
        if contact >= MIN_CONTACT_MM2:
            hull = _hull([(px[i], py[i]) for i in contact_vertices])
            com = apply(rotation, facets.centroid)
            stable = _inside(hull, (com[0], com[1]), margin=1e-6 * self.diag * self.diag)

        total = facets.total_area
        score = (
            overhang / total
            + 0.15 * height / self.diag
            - 0.5 * min(contact / total, 0.25)
            + (0.0 if stable else _UNSTABLE_PENALTY)
            - (_FILE_POSE_BONUS if is_identity(rotation) else 0.0)
        )
        return Pose(rotation, height, contact, overhang, stable, score, size)

    def candidate_rotations(self) -> list[Matrix]:
        bins = self.bins
        order = sorted(range(len(bins.keys)), key=lambda b: bins.area[b], reverse=True)
        downs: list[Vec3] = [_DOWN]
        for axis in ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)):
            downs.append(axis)
        for b in order:
            if len(downs) >= _MAX_CANDIDATES:
                break
            if bins.normal[b] != (0.0, 0.0, 0.0):
                downs.append(bins.normal[b])
        unique: list[Vec3] = []
        for d in downs:
            if all(d[0] * u[0] + d[1] * u[1] + d[2] * u[2] < _SAME_DIRECTION_COS for u in unique):
                unique.append(d)
        return [rotation_between(d, _DOWN) for d in unique]

    def rank(self, limit: int = MAX_FACES) -> list[Pose]:
        """Candidate poses, best first. Face ``n`` in the CLI is ``rank()[n - 1]``.

        Ranked once per analyzer: an interactive front-end plans a new pose on
        every key press, and the ranking is the expensive part.
        """
        if self._ranked is None:
            poses = [self.pose(r) for r in self.candidate_rotations()]
            poses.sort(key=lambda p: p.score)
            self._ranked = poses
        return self._ranked[:limit]


MAX_ROTATION_DEGREES = 3600.0


def parse_rotation(text: str) -> tuple[str, float]:
    """``"x=90"`` (or ``x:90``, ``X90``) -> ``("x", 90.0)``. Raises ``ValueError``."""
    raw = text.strip().lower().replace(" ", "")
    if not raw or raw[0] not in AXES:
        raise ValueError(f"expected AXIS=DEGREES with AXIS one of x, y, z (got {text!r})")
    axis, rest = raw[0], raw[1:]
    if rest[:1] in ("=", ":"):
        rest = rest[1:]
    rest = rest.rstrip("°").removesuffix("deg")
    try:
        degrees = float(rest)
    except ValueError:
        raise ValueError(f"expected AXIS=DEGREES, e.g. x=90 or z=-45 (got {text!r})") from None
    if not math.isfinite(degrees) or abs(degrees) > MAX_ROTATION_DEGREES:
        raise ValueError(f"rotation must be a finite angle within ±{MAX_ROTATION_DEGREES:g}° (got {text!r})")
    return axis, degrees


@dataclass(frozen=True)
class OrientRequest:
    """What the person asked for, in CLI vocabulary."""

    mode: str = "keep"
    face: int | None = None
    rotations: tuple[tuple[str, float], ...] = ()

    @property
    def changes_pose(self) -> bool:
        """False when the request keeps the file's own orientation."""
        if self.mode == "auto" or self.face is not None:
            return True
        return not is_identity(self.extra_rotation())

    def extra_rotation(self) -> Matrix:
        total = IDENTITY
        for axis, degrees in self.rotations:
            total = matmul(rotation_about(axis, degrees), total)
        return total

    def describe(self) -> str:
        parts = []
        if self.face is not None:
            parts.append(f"face {self.face}")
        elif self.mode == "auto":
            parts.append("auto")
        for axis, degrees in self.rotations:
            parts.append(f"{axis}{degrees:+g}°")
        return ", ".join(parts) or "as exported"


@dataclass(frozen=True)
class OrientPlan:
    """The resolved orientation: rotate about the origin, then translate."""

    rotation: Matrix
    translation: Vec3
    pose: Pose
    file_pose: Pose
    faces: list[Pose] = field(default_factory=list)
    face: int | None = None  # the ranked face the base pose came from (1-based)
    source: str = "file"  # "file" | "auto" | "face"
    warning: str | None = None

    @property
    def changed(self) -> bool:
        return not is_identity(self.rotation)


def describe_turn(rotation: Matrix) -> str | None:
    """The turn ``rotation`` makes, in words: ``"90° about X"``, ``"180° about Y"``, ``"125°"``.

    None for no turn. A turn about one of the main axes (within 5°) names it,
    with a minus sign for the other direction; any other turn gives the angle.
    """
    r = clean_matrix(rotation)
    cos_angle = max(-1.0, min(1.0, (r[0][0] + r[1][1] + r[2][2] - 1.0) / 2.0))
    angle = math.degrees(math.acos(cos_angle))
    if angle < 0.5:
        return None
    if angle > 179.5:
        # Half turn: the axis is the column of R + I with the largest diagonal.
        k = max(range(3), key=lambda i: r[i][i])
        axis = [(r[k][j] + (1.0 if j == k else 0.0)) for j in range(3)]
    else:
        axis = [r[2][1] - r[1][2], r[0][2] - r[2][0], r[1][0] - r[0][1]]
    length = math.sqrt(sum(a * a for a in axis)) or 1.0
    axis = [a / length for a in axis]
    shown = round(angle)
    for i, name in enumerate("XYZ"):
        if abs(axis[i]) >= math.cos(math.radians(5)):
            sign = "-" if axis[i] < 0 and shown < 180 else ""
            return f"{sign}{shown}° about {name}"
    return f"{shown}°"


def placement(mesh: Mesh, rotation: Matrix) -> Vec3:
    """Translation that puts the rotated model on the bed (z-min 0) where it was in X/Y."""
    lo, hi = mesh.bounds()
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    px = mesh.projection(rotation[0])
    py = mesh.projection(rotation[1])
    pz = mesh.projection(rotation[2])
    return (
        cx - (min(px) + max(px)) / 2.0,
        cy - (min(py) + max(py)) / 2.0,
        -min(pz),
    )


def face_number_for(faces: list[Pose], rotation: Matrix) -> int | None:
    """The 1-based ranked face whose pose has the same bed-facing direction, if any."""
    target = (-rotation[2][0], -rotation[2][1], -rotation[2][2])
    for n, pose in enumerate(faces, 1):
        d = pose.down
        if d[0] * target[0] + d[1] * target[1] + d[2] * target[2] >= _SAME_DIRECTION_COS:
            return n
    return None


def plan_orientation(mesh: Mesh, request: OrientRequest, analyzer: Analyzer | None = None) -> OrientPlan:
    """Resolve ``request`` against ``mesh``. Raises ``ValueError`` for a face that does not exist."""
    analyzer = analyzer or Analyzer(mesh)
    faces = analyzer.rank()
    file_pose = analyzer.pose(IDENTITY)
    base = IDENTITY
    face: int | None = None
    source = "file"
    if request.face is not None:
        if not 1 <= request.face <= len(faces):
            raise ValueError(f"face {request.face} does not exist; this model has faces 1-{len(faces)}")
        face = request.face
        base = faces[face - 1].rotation
        source = "face"
    elif request.mode == "auto":
        face = 1
        base = faces[0].rotation
        source = "auto"
    rotation = clean_matrix(matmul(request.extra_rotation(), base))
    pose = file_pose if is_identity(rotation) else analyzer.pose(rotation)
    warning = None
    if not request.changes_pose and not file_pose.stable and faces and faces[0].stable:
        best = faces[0]
        warning = (
            f"rests on a point or an edge as exported ({file_pose.contact_area:.0f} mm² on the bed); "
            f"auto would lay it on face 1 ({best.contact_area:.0f} mm² on the bed, "
            f"{best.height:.1f} mm tall)"
        )
    return OrientPlan(
        rotation=rotation,
        translation=placement(mesh, rotation),
        pose=pose,
        file_pose=file_pose,
        faces=faces,
        face=face if face is not None else face_number_for(faces, rotation),
        source=source,
        warning=warning,
    )
