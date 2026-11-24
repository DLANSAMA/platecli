"""The ``plate preview`` PNG: the chosen pose from two sides, then every ranked face."""

from __future__ import annotations

from bambu_cli.mesh.geometry import Mesh
from bambu_cli.mesh.orient import OrientPlan, OrientRequest, Pose, face_number_for
from bambu_cli.mesh.render import BACKGROUND, RGB, Image, draw_text, render, text_width

TILE_W = 300
TILE_H = 250
LABEL_H = 40
COLUMNS = 4
HEADER_H = 36

TEXT: RGB = (226, 230, 235)
MUTED: RGB = (150, 158, 168)
ACCENT: RGB = (244, 196, 64)
WARN: RGB = (232, 110, 80)


def _pose_line(pose: Pose) -> str:
    return f"{pose.height:.1f}MM TALL {pose.contact_area:.0f}MM2 BED"


def _tile(mesh: Mesh, pose: Pose, title: str, *, view: str, selected: bool) -> Image:
    tile = Image.blank(TILE_W, TILE_H, BACKGROUND)
    picture = render(mesh, pose.rotation, TILE_W - 8, TILE_H - LABEL_H - 8, view=view)
    tile.blit(picture, 4, 4)
    y = TILE_H - LABEL_H + 2
    draw_text(tile, 8, y, title[:24], ACCENT if selected else TEXT, 2)
    line = _pose_line(pose) if pose.stable else "TIPS OVER " + _pose_line(pose)
    draw_text(tile, 8, y + 20, line[:48], MUTED if pose.stable else WARN, 1)
    if selected:
        for x0, y0, x1, y1 in (
            (0, 0, TILE_W, 3),
            (0, TILE_H - 3, TILE_W, TILE_H),
            (0, 0, 3, TILE_H),
            (TILE_W - 3, 0, TILE_W, TILE_H),
        ):
            tile.fill(x0, y0, x1, y1, ACCENT)
    return tile


def render_sheet(mesh: Mesh, plan: OrientPlan, request: OrientRequest, name: str) -> tuple[Image, int]:
    """Return the sheet and how many ranked faces it shows."""
    chosen = plan.pose
    selected_face = face_number_for(plan.faces, plan.rotation)
    tiles = [
        _tile(mesh, chosen, f"PICK: {request.describe().replace('°', '')}", view="iso", selected=False),
        _tile(mesh, chosen, "SAME, FROM THE FRONT", view="front", selected=False),
    ]
    for n, pose in enumerate(plan.faces, 1):
        label = f"FACE {n}" + (" AUTO" if n == 1 else "")
        tiles.append(_tile(mesh, pose, label, view="iso", selected=n == selected_face))
    rows = (len(tiles) + COLUMNS - 1) // COLUMNS
    sheet = Image.blank(COLUMNS * TILE_W, HEADER_H + rows * TILE_H, (14, 16, 19))
    header = f"PLATE PREVIEW  {name}"
    while text_width(header, 2) > sheet.width - 16 and len(header) > 20:
        header = header[:-2]
    draw_text(sheet, 10, 10, header, TEXT, 2)
    for i, tile in enumerate(tiles):
        sheet.blit(tile, (i % COLUMNS) * TILE_W, HEADER_H + (i // COLUMNS) * TILE_H)
    return sheet, len(plan.faces)
