from __future__ import annotations

import math
import os
import struct

import pytest

from bambu_cli.mesh import formats, geometry
from bambu_cli.mesh.formats import load_obj, load_stl, write_stl
from bambu_cli.mesh.geometry import (
    IDENTITY,
    Mesh,
    MeshError,
    apply,
    clean_matrix,
    is_identity,
    matmul,
    normalize,
    rotation_about,
    rotation_between,
)
from tests.mesh_shapes import box, make_mesh

# ---------------------------------------------------------------- geometry


def test_quarter_turns_are_exact():
    assert rotation_about("z", 90) == ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert rotation_about("x", 180)[1][1] == -1.0
    assert rotation_about("y", -90)[0][2] == -1.0
    assert rotation_about("x", 360) == IDENTITY
    assert apply(rotation_about("z", 90), (1.0, 0.0, 0.0)) == (0.0, 1.0, 0.0)


def test_non_quarter_turn_and_bad_axis():
    m = rotation_about("z", 45)
    assert m[0][0] == pytest.approx(math.sqrt(0.5))
    with pytest.raises(ValueError, match="unknown axis"):
        rotation_about("w", 10)


@pytest.mark.parametrize(
    ("u", "v"),
    [
        ((0, 0, 1), (0, 0, 1)),
        ((0, 0, 1), (0, 0, -1)),
        ((1, 0, 0), (-1, 0, 0)),
        ((0, 1, 0), (0, -1, 0)),
        ((1, 2, 3), (-3, 1, 2)),
        ((0.3, -0.5, 0.8), (0, 0, -1)),
    ],
)
def test_rotation_between_maps_u_to_v(u, v):
    r = rotation_between(u, v)
    got = apply(r, normalize(u))
    for g, w in zip(got, normalize(v), strict=True):
        assert g == pytest.approx(w, abs=1e-9)
    # proper rotation: R R^T = I
    assert is_identity(matmul(r, tuple(zip(*r, strict=True))), 1e-9)


def test_normalize_zero_raises():
    with pytest.raises(ValueError, match="zero"):
        normalize((0.0, 0.0, 0.0))


def test_matmul_applies_right_operand_first():
    a = rotation_about("z", 90)
    b = rotation_about("x", 90)
    assert apply(matmul(a, b), (0.0, 1.0, 0.0)) == apply(a, apply(b, (0.0, 1.0, 0.0)))
    assert apply(matmul(a, b), (0.0, 1.0, 0.0)) != apply(matmul(b, a), (0.0, 1.0, 0.0))
    assert matmul(IDENTITY, a) == a


def test_clean_matrix_and_is_identity():
    noisy = ((1 - 1e-14, 1e-15, 0.0), (0.0, -1 + 1e-14, 0.0), (0.0, 0.0, 0.5))
    assert clean_matrix(noisy) == ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 0.5))
    assert is_identity(IDENTITY)
    assert is_identity(((1 + 1e-12, 0, 0), (0, 1, 0), (0, 0, 1)))
    assert not is_identity(rotation_about("z", 1))


def test_mesh_bounds_size_rotate_translate_projection():
    m = box(10, 20, 30, origin=(1, 2, 3))
    assert m.bounds() == ((1, 2, 3), (11, 22, 33))
    assert m.size() == (10, 20, 30)
    assert m.vertex_count == 8
    assert m.triangle_count == 12
    assert m.projection((0, 0, 1))[:2] == [3, 3]
    t = m.translated((-1, -2, -3))
    assert t.bounds()[0] == (0, 0, 0)
    r = m.rotated(rotation_about("z", 90))
    assert r.size() == (20, 10, 30)
    assert m.size() == (10, 20, 30)  # original untouched


def test_empty_mesh_bounds_raises():
    assert Mesh.empty().triangle_count == 0
    with pytest.raises(MeshError, match="no vertices"):
        Mesh.empty().bounds()


def test_facets_closed_box_centroid_and_outward_normals():
    f = box(10, 20, 30, origin=(5, 5, 5)).facets()
    assert f.closed
    assert f.total_area == pytest.approx(2 * (200 + 300 + 600))
    assert f.centroid == pytest.approx((10, 15, 20))
    assert box().facets() is not None
    m = box(10, 20, 30, origin=(5, 5, 5))
    assert m.facets() is m.facets()  # cached
    # first triangle is the bottom face: outward normal is -z
    assert (f.nx[0], f.ny[0], f.nz[0]) == pytest.approx((0, 0, -1))


def test_facets_reversed_winding_still_outward():
    f = box(10, 20, 30, origin=(5, 5, 5), reverse=True).facets()
    assert f.closed
    assert f.centroid == pytest.approx((10, 15, 20))
    assert (f.nx[0], f.ny[0], f.nz[0]) == pytest.approx((0, 0, -1))


def test_facets_open_mesh_uses_area_centroid():
    m = make_mesh([(0, 0, 0), (4, 0, 0), (0, 4, 0)], [(0, 1, 2)])
    f = m.facets()
    assert not f.closed
    assert f.centroid == pytest.approx((4 / 3, 4 / 3, 0))
    assert f.nz[0] == pytest.approx(1)


def test_facets_translated_open_planar_mesh_stays_open_and_unflipped():
    for z in (-10.0, 10.0):
        m = make_mesh([(0, 0, z), (4, 0, z), (0, 4, z)], [(0, 1, 2)])
        f = m.facets()
        assert not f.closed
        assert f.nz[0] == pytest.approx(1)
        assert f.centroid == pytest.approx((4 / 3, 4 / 3, z))


def test_facets_open_nonplanar_tiny_negative_vol6_not_flipped():
    # Two triangles: first defines ref_origin (0,0,0), second gives v6 = -1e-10
    # which is negative yet below the closed-mesh threshold (~1e-9).
    m = make_mesh(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1e-10)],
        [(0, 1, 2), (1, 3, 2)],
    )
    f = m.facets()
    assert not f.closed
    assert f.nz[0] == pytest.approx(1)
    assert f.nz[1] == pytest.approx(1)


def test_facets_closed_box_far_from_origin():
    origin = (10000.0, -10000.0, 10000.0)
    expected_centroid = (10005.0, -9990.0, 10015.0)
    for reverse in (False, True):
        f = box(10, 20, 30, origin=origin, reverse=reverse).facets()
        assert f.closed
        assert f.centroid == pytest.approx(expected_centroid)
        assert (f.nx[0], f.ny[0], f.nz[0]) == pytest.approx((0, 0, -1))


def test_facets_degenerate_triangle_has_zero_normal_and_all_degenerate_raises():
    m = make_mesh([(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0)], [(0, 1, 2), (0, 1, 3)])
    f = m.facets()
    assert (f.nx[0], f.ny[0], f.nz[0]) == (0.0, 0.0, 0.0)
    flat = make_mesh([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 2)])
    with pytest.raises(MeshError, match="degenerate"):
        flat.facets()


def test_mesh_error_carries_exit_code():
    err = MeshError("boom")
    assert err.exit_code == geometry.EXIT_FILE_ERROR
    assert "boom" in str(err)


# ---------------------------------------------------------------- formats


def _same_triangles(a: Mesh, b: Mesh) -> bool:
    def tris(m):
        return sorted(
            tuple(sorted((m.xs[i], m.ys[i], m.zs[i]) for i in m.tris[t : t + 3])) for t in range(0, len(m.tris), 3)
        )

    return tris(a) == tris(b)


def test_binary_stl_round_trip_and_dedup(tmp_path):
    src = box(10, 20, 30)
    path = str(tmp_path / "box.stl")
    write_stl(path, src)
    assert os.path.getsize(path) == 84 + 50 * 12
    back = load_stl(path)
    assert back.vertex_count == 8
    assert back.triangle_count == 12
    assert back.size() == (10, 20, 30)
    assert _same_triangles(src, back)


def test_write_stl_header_truncated_to_80(tmp_path):
    path = str(tmp_path / "h.stl")
    write_stl(path, box(), header=b"x" * 200)
    with open(path, "rb") as fh:
        head = fh.read(84)
    assert head[:80] == b"x" * 80
    assert struct.unpack("<I", head[80:])[0] == 12


ASCII_TRI = """solid t

 facet normal 0 0 1
  outer loop
   vertex 0 0 0
   vertex 1 0 0
   vertex 0 1 0
  endloop
 endfacet
endsolid t
"""


def test_ascii_stl(tmp_path):
    path = tmp_path / "a.stl"
    path.write_text(ASCII_TRI)
    m = load_stl(str(path))
    assert (m.vertex_count, m.triangle_count) == (3, 1)


def test_ascii_stl_polygon_loop_is_fanned(tmp_path):
    body = "solid q\nouter loop\n" + "".join(f"vertex {x} {y} 0\n" for x, y in [(0, 0), (1, 0), (1, 1), (0, 1)])
    path = tmp_path / "q.stl"
    path.write_text(body + "endloop\nendsolid\n")
    assert load_stl(str(path)).triangle_count == 2


def test_binary_stl_with_solid_header_is_binary(tmp_path):
    path = str(tmp_path / "s.stl")
    write_stl(path, box(), header=b"solid exported by a cad tool")
    assert load_stl(path).triangle_count == 12


def test_truncated_binary_stl(tmp_path):
    path = str(tmp_path / "t.stl")
    write_stl(path, box())
    with open(path, "rb") as fh:
        data = fh.read()
    with open(path, "wb") as fh:
        fh.write(data[:-60])
    with pytest.raises(MeshError, match="truncated"):
        load_stl(path)


def test_binary_stl_with_trailing_bytes_still_loads(tmp_path):
    path = str(tmp_path / "t.stl")
    write_stl(path, box())
    with open(path, "ab") as fh:
        fh.write(b"\0" * 7)
    assert load_stl(path).triangle_count == 12


def test_short_file_falls_to_ascii_and_has_no_triangles(tmp_path):
    path = tmp_path / "tiny.stl"
    path.write_bytes(b"\x00" * 40)
    with pytest.raises(MeshError, match="no triangles"):
        load_stl(str(path))


def test_malformed_ascii_vertices(tmp_path):
    p1 = tmp_path / "m1.stl"
    p1.write_text("solid x\nvertex 1 2\n")
    with pytest.raises(MeshError, match="malformed vertex"):
        load_stl(str(p1))
    p2 = tmp_path / "m2.stl"
    p2.write_text("solid x\nvertex 1 2 abc\n")
    with pytest.raises(MeshError, match="malformed vertex"):
        load_stl(str(p2))


def test_triangle_limit_binary_ascii_obj(tmp_path, monkeypatch):
    path = str(tmp_path / "b.stl")
    write_stl(path, box())
    monkeypatch.setattr(formats, "MAX_TRIANGLES", 5)
    with pytest.raises(MeshError, match="more than 5 triangles"):
        load_stl(path)
    a = tmp_path / "a.stl"
    a.write_text(ASCII_TRI * 8)
    with pytest.raises(MeshError, match="more than"):
        load_stl(str(a))
    o = tmp_path / "o.obj"
    o.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\n" + "f 1 2 3\n" * 8)
    with pytest.raises(MeshError, match="more than"):
        load_obj(str(o))


def test_file_size_limit(tmp_path, monkeypatch):
    path = str(tmp_path / "b.stl")
    write_stl(path, box())
    monkeypatch.setattr(formats, "MAX_MODEL_FILE_BYTES", 100)
    with pytest.raises(MeshError, match="larger than"):
        load_stl(path)
    o = tmp_path / "o.obj"
    o.write_text("v 0 0 0\n" * 50)
    with pytest.raises(MeshError, match="larger than"):
        load_obj(str(o))


def test_obj_quads_negative_indices_and_slash_tokens(tmp_path):
    p = tmp_path / "q.obj"
    p.write_text(
        "# comment\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvn 0 0 1\nf 1/1/1 2/1/1 3/1/1 4//1\nf -4 -3 -2\n"
    )
    m = load_obj(str(p))
    assert (m.vertex_count, m.triangle_count) == (4, 3)
    assert list(m.tris[-3:]) == [0, 1, 2]


@pytest.mark.parametrize(
    ("body", "msg"),
    [
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n", "does not exist"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n", "does not exist"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 -4\n", "does not exist"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 x\n", "malformed face"),
        ("v 0 0\nf 1 1 1\n", "malformed vertex"),
        ("v 0 0 q\n", "malformed vertex"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\n", "no faces"),
    ],
)
def test_obj_errors(tmp_path, body, msg):
    p = tmp_path / "bad.obj"
    p.write_text(body)
    with pytest.raises(MeshError, match=msg):
        load_obj(str(p))
