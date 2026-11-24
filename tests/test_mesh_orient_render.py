from __future__ import annotations

import struct
import zlib

import pytest

from bambu_cli.mesh import (
    LoadedModel,
    MeshError,
    OrientRequest,
    load_model,
    orient,
    plan_orientation,
    write_oriented,
)
from bambu_cli.mesh.formats import load_stl, write_stl
from bambu_cli.mesh.geometry import IDENTITY, Mesh, rotation_about
from bambu_cli.mesh.orient import MAX_ROTATION_DEGREES, Analyzer, face_number_for, parse_rotation, placement
from bambu_cli.mesh.render import (
    BACKGROUND,
    MODEL,
    OVERHANG,
    VIEWS,
    Image,
    draw_text,
    render,
    text_width,
)
from bambu_cli.mesh.sheet import COLUMNS, TILE_W, render_sheet
from bambu_cli.mesh.threemf import load_3mf
from tests.mesh_shapes import box, flared_on_stem, pyramid_down, pyramid_up, thin_plate, write_3mf

# ---------------------------------------------------------------- orient


def test_inverted_pyramid_auto_lays_on_base():
    m = pyramid_down(20, 20)
    plan = plan_orientation(m, OrientRequest(mode="auto"))
    assert not plan.file_pose.stable
    assert plan.pose.stable
    assert plan.pose.height == pytest.approx(20)
    assert plan.pose.contact_area == pytest.approx(400, rel=0.02)
    assert plan.source == "auto" and plan.face == 1 and plan.changed
    placed = m.rotated(plan.rotation).translated(plan.translation)
    assert placed.bounds()[0][2] == pytest.approx(0, abs=1e-9)
    assert placed.size()[2] == pytest.approx(20)


def test_pyramid_up_keeps_file_pose():
    plan = plan_orientation(pyramid_up(), OrientRequest(mode="auto"))
    assert not plan.changed and plan.faces[0].stable


def test_thin_plate_face_one_lays_flat():
    plan = plan_orientation(thin_plate(), OrientRequest(face=1))
    assert plan.pose.height == pytest.approx(3)
    assert plan.source == "face"


def test_box_keeps_file_pose_as_face_one():
    m = box(20, 20, 20)
    plan = plan_orientation(m, OrientRequest(mode="auto"))
    assert not plan.changed
    assert plan.faces[0].rotation == IDENTITY
    assert plan.warning is None


def test_keep_mode_warns_on_inverted_pyramid():
    plan = plan_orientation(pyramid_down(), OrientRequest())
    assert plan.source == "file" and not plan.changed
    assert plan.warning and "auto would lay it on face 1" in plan.warning


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("x=90", ("x", 90.0)),
        ("Z:-45", ("z", -45.0)),
        ("y30", ("y", 30.0)),
        (" x = 10° ", ("x", 10.0)),
        ("z=5deg", ("z", 5.0)),
    ],
)
def test_parse_rotation_spellings(text, want):
    assert parse_rotation(text) == want


@pytest.mark.parametrize("text", ["", "w=90", "x=abc", "x=nan", "x=inf", f"x={MAX_ROTATION_DEGREES + 1}", "90"])
def test_parse_rotation_rejects(text):
    with pytest.raises(ValueError):
        parse_rotation(text)


def test_request_describe_changes_pose_extra_rotation():
    assert OrientRequest().describe() == "as exported"
    assert not OrientRequest().changes_pose
    assert OrientRequest(mode="auto").describe() == "auto"
    assert OrientRequest(face=3, rotations=(("z", 90.0),)).describe() == "face 3, z+90°"
    assert OrientRequest(mode="auto", face=2).describe() == "face 2"
    assert OrientRequest(rotations=(("x", 90.0),)).changes_pose
    assert not OrientRequest(rotations=(("x", 360.0),)).changes_pose
    # rotations apply in order: x then z
    r = OrientRequest(rotations=(("x", 90.0), ("z", 90.0))).extra_rotation()
    expect = orient.matmul(rotation_about("z", 90), rotation_about("x", 90))
    assert r == expect


def test_plan_errors_and_rotation_composition():
    m = thin_plate()
    with pytest.raises(ValueError, match="face 99 does not exist"):
        plan_orientation(m, OrientRequest(face=99))
    with pytest.raises(ValueError):
        plan_orientation(m, OrientRequest(face=0))
    an = Analyzer(m)
    base = plan_orientation(m, OrientRequest(face=1), an)
    plan = plan_orientation(m, OrientRequest(face=1, rotations=(("z", 90.0),)), an)
    expect = orient.clean_matrix(orient.matmul(rotation_about("z", 90), base.rotation))
    assert plan.rotation == expect
    assert plan.pose.height == pytest.approx(3)
    assert plan.pose.size[0] == pytest.approx(base.pose.size[1])


def test_rotation_only_request_and_face_number_for():
    m = pyramid_down()
    plan = plan_orientation(m, OrientRequest(rotations=(("x", 180.0),)))
    assert plan.source == "file"
    assert plan.pose.stable
    assert plan.face is not None
    assert face_number_for(plan.faces, plan.rotation) == plan.face
    assert face_number_for([], IDENTITY) is None


def test_placement_keeps_xy_centre_and_floors_z():
    m = box(10, 20, 30, origin=(100, -50, 7))
    rot = rotation_about("x", 90)
    t = placement(m, rot)
    placed = m.rotated(rot).translated(t)
    lo, hi = placed.bounds()
    assert lo[2] == pytest.approx(0, abs=1e-9)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(105)
    assert (lo[1] + hi[1]) / 2 == pytest.approx(-40)


def test_pose_down_and_rank_limit():
    an = Analyzer(box())
    ranked = an.rank(3)
    assert len(ranked) <= 3
    assert ranked[0].down == pytest.approx((0, 0, -1))


def test_degenerate_normal_bins_and_hull_edges():
    assert orient._hull([(0, 0), (1, 1)]) == [(0, 0), (1, 1)]
    assert not orient._inside([(0, 0), (1, 1)], (0.5, 0.5))
    # opposite-facing triangles of equal area in one bin cancel to a zero normal
    m = Mesh.empty()
    for p in [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]:
        m.xs.append(p[0]), m.ys.append(p[1]), m.zs.append(p[2])
    for t in [(0, 1, 2), (0, 1, 3), (0, 3, 2), (1, 2, 3)]:
        m.tris.extend(t)
    assert Analyzer(m).rank()


# ---------------------------------------------------------------- render


def _count(img: Image, color) -> int:
    px = img.pixels
    return sum(1 for i in range(0, len(px), 3) if _near(tuple(px[i : i + 3]), color))


def _near(p, color) -> bool:
    """Shaded pixel: same hue direction as the base colour, any brightness."""
    k = max(p) / max(max(color), 1)
    return k > 0 and all(abs(p[i] - color[i] * k) < 3 for i in range(3))


def test_render_box_has_model_pixels():
    img = render(box(), IDENTITY, 120, 90)
    assert (img.width, img.height) == (120, 90) and len(img.pixels) == 120 * 90 * 3
    assert _count(img, MODEL) > 200
    assert _count(img, OVERHANG) == 0
    assert img.get(0, 0) == BACKGROUND


def test_render_inverted_pyramid_tips_tinted():
    # Its slopes touch the bed only at the apex: they still need support.
    img = render(pyramid_down(40, 10), IDENTITY, 160, 120, view="front")
    assert _count(img, OVERHANG) > 50


def test_render_flared_part_shows_overhang_tint():
    # Down-facing slopes are back faces from above, so look from the side.
    img = render(flared_on_stem(), IDENTITY, 160, 120, view="front")
    assert _count(img, OVERHANG) > 50


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_every_view_renders(view):
    img = render(thin_plate(), IDENTITY, 100, 80, view=view)
    assert _count(img, MODEL) > 0


def test_render_open_mesh_and_degenerate_sizes():
    open_mesh = Mesh.empty()
    for p in [(0, 0, 0), (10, 0, 0), (0, 10, 0)]:
        open_mesh.xs.append(p[0]), open_mesh.ys.append(p[1]), open_mesh.zs.append(p[2])
    open_mesh.tris.extend((0, 1, 2))
    img = render(open_mesh, IDENTITY, 80, 60, view="top")
    assert _count(img, MODEL) > 0
    assert render(box(), IDENTITY, 1, 1).pixels == bytearray(bytes(BACKGROUND))
    assert render(Mesh.empty(), IDENTITY, 10, 10).pixels == bytearray(bytes(BACKGROUND) * 100)


def test_render_tiny_and_huge_models_exercise_subpixel_and_wide_grid():
    assert render(box(0.01, 0.01, 0.01), IDENTITY, 40, 30).width == 40
    big = render(box(400, 400, 5), IDENTITY, 80, 60, view="top")
    assert _count(big, MODEL) > 0


def test_image_primitives():
    img = Image.blank(6, 4, (1, 2, 3))
    img.put(1, 1, (9, 9, 9))
    img.put(-1, 0, (5, 5, 5))
    img.put(6, 0, (5, 5, 5))
    assert img.get(1, 1) == (9, 9, 9)
    img.fill(-3, -3, 2, 2, (7, 7, 7))
    assert img.get(0, 0) == (7, 7, 7) and img.get(2, 0) == (1, 2, 3)
    other = Image.blank(3, 3, (50, 60, 70))
    img.blit(other, 4, 2)
    assert img.get(5, 3) == (50, 60, 70)
    img.blit(other, -1, -1)  # clipped on top/left
    img.blit(other, 10, 0)  # fully outside horizontally
    img.blit(other, 0, 99)  # fully outside vertically


def _chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        yield kind, body
        pos += 12 + n


def test_png_is_valid():
    img = render(box(), IDENTITY, 37, 21)
    chunks = list(_chunks(img.png()))
    assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    w, h, depth, ctype = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (w, h, depth, ctype) == (37, 21, 8, 2)
    assert len(zlib.decompress(chunks[1][1])) == 21 * (1 + 3 * 37)


def test_draw_text_and_width():
    img = Image.blank(80, 20, (0, 0, 0))
    end = draw_text(img, 2, 2, "Ab 1?", (255, 255, 255), 2)
    assert end == 2 + text_width("Ab 1?", 2) == 2 + 5 * 6 * 2
    assert any(img.pixels)
    blank = Image.blank(20, 10, (0, 0, 0))
    draw_text(blank, 0, 0, "~", (255, 255, 255))  # unknown glyph renders as a space
    assert not any(blank.pixels)


# ---------------------------------------------------------------- sheet


def test_render_sheet_dimensions_and_faces():
    m = thin_plate()
    request = OrientRequest(mode="auto")
    plan = plan_orientation(m, request)
    sheet, shown = render_sheet(m, plan, request, "plate-" + "x" * 200 + ".stl")
    assert shown == len(plan.faces)
    assert sheet.width == COLUMNS * TILE_W
    assert sheet.height > 0 and len(sheet.pixels) == sheet.width * sheet.height * 3
    assert sheet.png()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_sheet_tipping_pose_label_path():
    m = pyramid_down()
    request = OrientRequest(face=2, rotations=(("z", 30.0),))
    plan = plan_orientation(m, request)
    sheet, shown = render_sheet(m, plan, request, "p.stl")
    assert shown == len(plan.faces)


# ---------------------------------------------------------------- __init__


def test_load_model_dispatch(tmp_path):
    stl = str(tmp_path / "b.STL")
    write_stl(stl, box())
    m = load_model(stl)
    assert m.kind == "stl" and m.object_count == 1 and m.project is None
    obj = tmp_path / "t.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    assert load_model(str(obj)).kind == "obj"
    t = write_3mf(tmp_path / "p.3mf", {"1": box()}, [("1", None)])
    p = load_model(t)
    assert p.kind == "3mf" and p.project is not None and p.object_count == 1
    with pytest.raises(MeshError, match="orients .stl, .obj and .3mf"):
        load_model(str(tmp_path / "x.step"))


def test_write_oriented_stl(tmp_path):
    src = str(tmp_path / "pyr.stl")
    write_stl(src, pyramid_down(20, 20))
    model = load_model(src)
    plan = plan_orientation(model.mesh, OrientRequest(mode="auto"))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = write_oriented(model, plan, str(out_dir))
    assert out == str(out_dir / "pyr.stl")
    back = load_stl(out)
    lo, _ = back.bounds()
    assert lo[2] == pytest.approx(0, abs=1e-4)
    assert back.size()[2] == pytest.approx(plan.pose.height, abs=1e-4)


def test_write_oriented_3mf_keeps_other_members(tmp_path):
    import zipfile

    unit = "inch"
    src = write_3mf(
        tmp_path / "pyr.3mf",
        {"1": pyramid_down(1.0, 1.0)},
        [("1", "1 0 0 0 1 0 0 0 1 2 3 4")],
        unit=unit,
    )
    model = load_model(src)
    plan = plan_orientation(model.mesh, OrientRequest(mode="auto"))
    assert plan.changed
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = write_oriented(model, plan, str(out_dir))
    assert out.endswith("pyr.3mf")
    back = load_3mf(out)
    lo, _ = back.mesh.bounds()
    assert lo[2] == pytest.approx(0, abs=1e-6)
    assert back.mesh.size()[2] == pytest.approx(plan.pose.height, abs=1e-6)
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        for name in a.namelist():
            if name != "3D/3dmodel.model":
                assert a.read(name) == b.read(name)


def test_write_oriented_3mf_with_two_items_refused(tmp_path):
    src = write_3mf(tmp_path / "two.3mf", {"1": box()}, [("1", None), ("1", None)])
    model = load_model(src)
    plan = plan_orientation(model.mesh, OrientRequest())
    with pytest.raises(MeshError, match="exactly one"):
        write_oriented(model, plan, str(tmp_path))
    assert isinstance(model, LoadedModel)


def test_rank_is_computed_once_per_analyzer(monkeypatch):
    from bambu_cli.mesh.orient import Analyzer

    analyzer = Analyzer(thin_plate())
    first = analyzer.rank()
    monkeypatch.setattr(analyzer, "candidate_rotations", lambda: pytest.fail("ranked twice"))
    assert analyzer.rank() == first
    assert analyzer.rank(2) == first[:2]


def test_render_without_grid_draws_a_plain_bed():
    from bambu_cli.mesh.render import BED_GRID

    def exact(img: Image) -> int:
        # _count matches any brightness of a hue, and the bed shares the grid's.
        px = img.pixels
        return sum(1 for i in range(0, len(px), 3) if tuple(px[i : i + 3]) == BED_GRID)

    assert exact(render(box(), IDENTITY, 160, 120)) > 0
    assert exact(render(box(), IDENTITY, 160, 120, grid=False)) == 0


@pytest.mark.parametrize(
    ("turns", "said"),
    [
        ((), None),
        ((("x", 90.0),), "90° about X"),
        ((("x", -90.0),), "-90° about X"),
        ((("y", 180.0),), "180° about Y"),
        ((("y", -180.0),), "180° about Y"),
        ((("z", 45.0),), "45° about Z"),
        ((("z", 0.2),), None),
        ((("x", 90.0), ("z", 90.0)), "120°"),
    ],
)
def test_describe_turn(turns, said):
    from bambu_cli.mesh import describe_turn

    assert describe_turn(OrientRequest(rotations=turns).extra_rotation()) == said


def test_ansi_half_blocks_pairs_rows_and_resets():
    from bambu_cli.mesh.render import Image, ansi_half_blocks

    img = Image.blank(3, 3, (0, 0, 0))
    img.put(0, 0, (255, 0, 0))
    img.put(0, 1, (0, 0, 255))
    lines = ansi_half_blocks(img)
    assert len(lines) == 2  # an odd last row pairs with itself
    assert lines[0].startswith("\x1b[38;2;255;0;0;48;2;0;0;255m\u2580")
    assert lines[0].endswith("\x1b[0m") and lines[0].count("\u2580") == 3
    # One colour change per run, not per cell.
    assert lines[1].count("\x1b[38;2;") == 1


def test_a_slab_thinner_than_a_pixel_still_shows_edge_on():
    # A 60 x 40 x 3 mm plate seen from the front in a 29 x 14 picture is under a
    # pixel tall; it must still draw as a line, not vanish between pixel centres.
    from bambu_cli.mesh import IDENTITY
    from bambu_cli.mesh.render import BACKGROUND, BED, render

    img = render(box(60, 40, 3), IDENTITY, 29, 14, view="front", grid=False)
    drawn = [
        (x, y) for y in range(img.height) for x in range(img.width) if img.get(x, y) not in (BACKGROUND, BED)
    ]
    assert len(drawn) >= 15
    assert {y for _x, y in drawn} <= {6, 7}
