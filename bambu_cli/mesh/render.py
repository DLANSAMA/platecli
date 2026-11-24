"""A small software rasterizer: draw a posed model on its bed, shaded, into RGB pixels.

Used for the terminal preview (half-block cells, or a real image where the
terminal can show one) and the ``plate preview`` PNG sheet. Orthographic, flat
shaded, z-buffered; down-facing surface that would need support is tinted so
the person can see what a pose costs, not only what it looks like.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

from bambu_cli.mesh.geometry import IDENTITY, Matrix, Mesh, matmul, rotation_about
from bambu_cli.mesh.orient import CONTACT_EPS_MM, OVERHANG_COS

RGB = tuple[int, int, int]

BACKGROUND: RGB = (22, 25, 29)
BED: RGB = (44, 49, 56)
BED_GRID: RGB = (60, 67, 76)
MODEL: RGB = (126, 170, 214)
OVERHANG: RGB = (232, 110, 80)
_LIGHT = (-0.35, 0.45, 0.82)

# Camera yaw/pitch per named view (degrees). "iso" looks from the front-right, above.
VIEWS: dict[str, tuple[float, float]] = {
    "iso": (-35.0, -60.0),
    "front": (0.0, -90.0),
    "side": (-90.0, -90.0),
    "top": (0.0, 0.0),
}


@dataclass
class Image:
    width: int
    height: int
    pixels: bytearray  # RGB, row-major

    @classmethod
    def blank(cls, width: int, height: int, color: RGB = BACKGROUND) -> Image:
        return cls(width, height, bytearray(bytes(color) * (width * height)))

    def get(self, x: int, y: int) -> RGB:
        o = 3 * (y * self.width + x)
        p = self.pixels
        return (p[o], p[o + 1], p[o + 2])

    def put(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            o = 3 * (y * self.width + x)
            self.pixels[o : o + 3] = bytes(color)

    def fill(self, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        x0, x1 = max(0, x0), min(self.width, x1)
        row = bytes(color) * max(0, x1 - x0)
        for y in range(max(0, y0), min(self.height, y1)):
            o = 3 * (y * self.width + x0)
            self.pixels[o : o + len(row)] = row

    def blit(self, other: Image, x: int, y: int) -> None:
        for row in range(other.height):
            ty = y + row
            if not 0 <= ty < self.height:
                continue
            src = other.pixels[3 * row * other.width : 3 * (row + 1) * other.width]
            x0 = max(0, x)
            x1 = min(self.width, x + other.width)
            if x1 <= x0:
                continue
            o = 3 * (ty * self.width + x0)
            self.pixels[o : o + 3 * (x1 - x0)] = src[3 * (x0 - x) : 3 * (x1 - x)]

    def png(self) -> bytes:
        raw = bytearray()
        stride = 3 * self.width
        for y in range(self.height):
            raw.append(0)
            raw += self.pixels[y * stride : (y + 1) * stride]

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b"")
        )


def _view_matrix(view: str) -> Matrix:
    yaw, pitch = VIEWS[view]
    return matmul(rotation_about("x", pitch), rotation_about("z", yaw))


def _shade(color: RGB, light: float) -> RGB:
    k = 0.32 + 0.68 * max(0.0, light)
    return (min(255, int(color[0] * k)), min(255, int(color[1] * k)), min(255, int(color[2] * k)))


def render(
    mesh: Mesh,
    pose: Matrix = IDENTITY,
    width: int = 320,
    height: int = 240,
    *,
    view: str = "iso",
    background: RGB = BACKGROUND,
    margin: float = 0.08,
    grid: bool = True,
) -> Image:
    """Draw ``mesh`` rotated by ``pose`` and set on a bed at z=0.

    ``grid`` draws 10 mm lines on the bed; leave it off for a picture only a
    few dozen pixels wide, where one-pixel lines break up into noise.
    """
    img = Image.blank(width, height, background)
    if width < 2 or height < 2 or mesh.triangle_count == 0:
        return img
    view_m = _view_matrix(view)
    full = matmul(view_m, pose)
    xs, ys, zs = mesh.xs, mesh.ys, mesh.zs

    # Pose frame first, to drop the model on the bed and size the bed square.
    pz = mesh.projection(pose[2])
    zmin = min(pz)
    px = mesh.projection(pose[0])
    py = mesh.projection(pose[1])
    cx = (min(px) + max(px)) / 2.0
    cy = (min(py) + max(py)) / 2.0
    half = max(max(px) - min(px), max(py) - min(py)) * 0.62 + 1.0
    del px, py

    r0, r1, r2 = full
    v0, v1, v2 = view_m
    # Screen coordinates (view x right, view y up, view z toward the camera) of
    # view @ (pose @ v - off): the pose-frame offset centres the model over the
    # origin with its lowest point on the bed.
    off = (cx, cy, zmin)
    ox = v0[0] * off[0] + v0[1] * off[1] + v0[2] * off[2]
    oy = v1[0] * off[0] + v1[1] * off[1] + v1[2] * off[2]
    oz = v2[0] * off[0] + v2[1] * off[1] + v2[2] * off[2]
    sx = [r0[0] * x + r0[1] * y + r0[2] * z - ox for x, y, z in zip(xs, ys, zs, strict=True)]
    sy = [r1[0] * x + r1[1] * y + r1[2] * z - oy for x, y, z in zip(xs, ys, zs, strict=True)]
    sz = [r2[0] * x + r2[1] * y + r2[2] * z - oz for x, y, z in zip(xs, ys, zs, strict=True)]

    bed = [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)]
    bed_s = [(v0[0] * a + v0[1] * b + v0[2] * c, v1[0] * a + v1[1] * b + v1[2] * c) for a, b, c in bed]
    min_x = min(min(sx), *(p[0] for p in bed_s))
    max_x = max(max(sx), *(p[0] for p in bed_s))
    min_y = min(min(sy), *(p[1] for p in bed_s))
    max_y = max(max(sy), *(p[1] for p in bed_s))
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    usable_w = width * (1.0 - 2 * margin)
    usable_h = height * (1.0 - 2 * margin)
    scale = min(usable_w / span_x, usable_h / span_y)
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    hw, hh = width / 2.0, height / 2.0

    def to_screen(x: float, y: float) -> tuple[float, float]:
        return hw + (x - mid_x) * scale, hh - (y - mid_y) * scale

    zbuf = [-math.inf] * (width * height)
    pixels = img.pixels

    def tri(p0, p1, p2, d0, d1, d2, color: RGB) -> None:  # noqa: ANN001 -- hot path, plain tuples
        x0, y0 = p0
        x1, y1 = p1
        x2, y2 = p2
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        bx0 = max(0, int(min(x0, x1, x2)))
        bx1 = min(width - 1, int(max(x0, x1, x2)))
        by0 = max(0, int(min(y0, y1, y2)))
        by1 = min(height - 1, int(max(y0, y1, y2)))
        if bx1 < bx0 or by1 < by0:
            return
        cbytes = bytes(color)
        span_x = max(x0, x1, x2) - min(x0, x1, x2)
        span_y = max(y0, y1, y2) - min(y0, y1, y2)
        if abs(area) < 1.0 or (bx0 == bx1 and by0 == by1) or span_x < 1.0 or span_y < 1.0:
            # Thinner than a pixel one way (a 3 mm slab seen edge-on in a small
            # picture): sampling at pixel centres can miss it altogether, so draw
            # it as a one-pixel line through its middle (a dot when it is small
            # both ways) and the slab still shows.
            depth = (d0 + d1 + d2) / 3.0
            if span_y < 1.0 <= span_x:
                row_y = int((min(y0, y1, y2) + max(y0, y1, y2)) / 2.0)
                cells = [(px_, row_y) for px_ in range(bx0, bx1 + 1)]
            elif span_x < 1.0 <= span_y:
                col_x = int((min(x0, x1, x2) + max(x0, x1, x2)) / 2.0)
                cells = [(col_x, py_) for py_ in range(by0, by1 + 1)]
            else:
                cells = [(int((x0 + x1 + x2) / 3.0), int((y0 + y1 + y2) / 3.0))]
            for px_, py_ in cells:
                if 0 <= px_ < width and 0 <= py_ < height:
                    idx = py_ * width + px_
                    if depth > zbuf[idx]:
                        zbuf[idx] = depth
                        pixels[3 * idx : 3 * idx + 3] = cbytes
            return
        inv = 1.0 / area
        for py_ in range(by0, by1 + 1):
            fy = py_ + 0.5
            row = py_ * width
            for px_ in range(bx0, bx1 + 1):
                fx = px_ + 0.5
                w0 = ((x1 - fx) * (y2 - fy) - (x2 - fx) * (y1 - fy)) * inv
                if w0 < 0:
                    continue
                w1 = ((x2 - fx) * (y0 - fy) - (x0 - fx) * (y2 - fy)) * inv
                if w1 < 0:
                    continue
                w2 = 1.0 - w0 - w1
                if w2 < 0:
                    continue
                depth = w0 * d0 + w1 * d1 + w2 * d2
                idx = row + px_
                if depth > zbuf[idx]:
                    zbuf[idx] = depth
                    pixels[3 * idx : 3 * idx + 3] = cbytes

    # Bed: a square under the model with a 10 mm grid, drawn as far-back quads.
    bed_depth = [v2[0] * a + v2[1] * b + v2[2] * c for a, b, c in bed]
    bed_px = [to_screen(*p) for p in bed_s]
    for i0, i1, i2 in ((0, 1, 2), (0, 2, 3)):
        tri(bed_px[i0], bed_px[i1], bed_px[i2], bed_depth[i0] - 1e6, bed_depth[i1] - 1e6, bed_depth[i2] - 1e6, BED)
    step = 10.0 if half < 150 else 50.0
    g = -math.floor(half / step) * step
    while grid and g <= half:
        for start, end in (((g, -half), (g, half)), ((-half, g), (half, g))):
            ax_, ay_ = to_screen(v0[0] * start[0] + v0[1] * start[1], v1[0] * start[0] + v1[1] * start[1])
            bx_, by_ = to_screen(v0[0] * end[0] + v0[1] * end[1], v1[0] * end[0] + v1[1] * end[1])
            n = int(max(abs(bx_ - ax_), abs(by_ - ay_))) + 1
            for k in range(n + 1):
                t = k / n
                gx = int(ax_ + (bx_ - ax_) * t)
                gy = int(ay_ + (by_ - ay_) * t)
                if 0 <= gx < width and 0 <= gy < height and zbuf[gy * width + gx] < -1e5:
                    o = 3 * (gy * width + gx)
                    pixels[o : o + 3] = bytes(BED_GRID)
        g += step

    facets = mesh.facets()
    nxs, nys, nzs = facets.nx, facets.ny, facets.nz
    pr = pose[2]
    lx, ly, lz = _LIGHT
    ll = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ll, ly / ll, lz / ll
    screen_x = [hw + (v - mid_x) * scale for v in sx]
    screen_y = [hh - (v - mid_y) * scale for v in sy]
    tris = mesh.tris
    cull = facets.closed
    for t in range(mesh.triangle_count):
        nx, ny, nz = nxs[t], nys[t], nzs[t]
        vz = r2[0] * nx + r2[1] * ny + r2[2] * nz
        if cull and vz <= 0.0:
            continue
        vx = r0[0] * nx + r0[1] * ny + r0[2] * nz
        vy = r1[0] * nx + r1[1] * ny + r1[2] * nz
        if vz < 0.0:
            vx, vy, vz = -vx, -vy, -vz
        up = pr[0] * nx + pr[1] * ny + pr[2] * nz
        i, j, k = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
        base = MODEL
        # The analyzer's rule: steep and down-facing, unless it lies on the bed.
        if up < -OVERHANG_COS and max(pz[i], pz[j], pz[k]) - zmin > CONTACT_EPS_MM:
            base = OVERHANG
        color = _shade(base, vx * lx + vy * ly + vz * lz)
        tri(
            (screen_x[i], screen_y[i]),
            (screen_x[j], screen_y[j]),
            (screen_x[k], screen_y[k]),
            sz[i],
            sz[j],
            sz[k],
            color,
        )
    return img


# --- a 5x7 bitmap font for sheet labels -------------------------------------------

_FONT_ROWS = {
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001",
    "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111",
    "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101",
    "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010",
    "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100",
    "Z": "11111 00001 00010 00100 01000 10000 11111",
    ".": "00000 00000 00000 00000 00000 01100 01100",
    ",": "00000 00000 00000 00000 01100 00100 01000",
    "-": "00000 00000 00000 11111 00000 00000 00000",
    "+": "00000 00100 00100 11111 00100 00100 00000",
    ":": "00000 01100 01100 00000 01100 01100 00000",
    "/": "00001 00010 00010 00100 01000 01000 10000",
    "(": "00010 00100 01000 01000 01000 00100 00010",
    ")": "01000 00100 00010 00010 00010 00100 01000",
    "#": "01010 01010 11111 01010 11111 01010 01010",
    " ": "00000 00000 00000 00000 00000 00000 00000",
}
_FONT = {ch: [[c == "1" for c in row] for row in rows.split()] for ch, rows in _FONT_ROWS.items()}
GLYPH_W, GLYPH_H = 6, 8  # advance, line height (5x7 glyph plus spacing)


def ansi_half_blocks(image: Image) -> list[str]:
    """``image`` as terminal lines of ``▀`` cells in 24-bit ANSI colour, two pixels per cell.

    The foreground is the upper pixel and the background the lower one; each
    line ends with a reset, so nothing bleeds into the text after it.
    """
    lines = []
    for y in range(0, image.height, 2):
        lower_y = y + 1 if y + 1 < image.height else y
        parts: list[str] = []
        last: tuple[RGB, RGB] | None = None
        for x in range(image.width):
            pair = (image.get(x, y), image.get(x, lower_y))
            if pair != last:
                (tr, tg, tb), (br, bg, bb) = pair
                parts.append(f"\x1b[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m")
                last = pair
            parts.append("\u2580")
        parts.append("\x1b[0m")
        lines.append("".join(parts))
    return lines


def draw_text(img: Image, x: int, y: int, text: str, color: RGB, scale: int = 1) -> int:
    """Draw ``text`` (upper-cased; unknown characters become spaces). Returns the end x."""
    for ch in text.upper():
        glyph = _FONT.get(ch, _FONT[" "])
        for gy, row in enumerate(glyph):
            for gx, on in enumerate(row):
                if on:
                    img.fill(x + gx * scale, y + gy * scale, x + (gx + 1) * scale, y + (gy + 1) * scale, color)
        x += GLYPH_W * scale
    return x


def text_width(text: str, scale: int = 1) -> int:
    return len(text) * GLYPH_W * scale
