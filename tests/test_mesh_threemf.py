from __future__ import annotations

import zipfile

import pytest

from bambu_cli.mesh import threemf
from bambu_cli.mesh.geometry import rotation_about
from bambu_cli.mesh.threemf import (
    BuildItem,
    compose,
    format_transform,
    is_3mf_sliced,
    is_unsliced_3mf,
    item_affine_mm,
    load_3mf,
    parse_transform,
    rewrite_item_transforms,
)
from tests.mesh_shapes import MODEL_REL, box, model_xml, write_3mf

IDENT = "1 0 0 0 1 0 0 0 1 0 0 0"
SHIFT = "1 0 0 0 1 0 0 0 1 100 50 5"


def test_sliced_and_unsliced_detection(tmp_path):
    plain = write_3mf(tmp_path / "plain.3mf", {"1": box()}, [("1", None)])
    sliced = write_3mf(tmp_path / "sliced.3mf", {"1": box()}, [("1", None)], gcode=True)
    assert is_unsliced_3mf(plain) and not is_3mf_sliced(plain)
    assert is_3mf_sliced(sliced) and not is_unsliced_3mf(sliced)
    notzip = tmp_path / "x.3mf"
    notzip.write_bytes(b"not a zip")
    assert not is_3mf_sliced(str(notzip)) and not is_unsliced_3mf(str(notzip))
    assert not is_3mf_sliced(str(tmp_path / "missing.3mf"))
    nomodel = tmp_path / "nomodel.3mf"
    with zipfile.ZipFile(nomodel, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert not is_unsliced_3mf(str(nomodel))
    assert not is_3mf_sliced(str(nomodel))


def test_load_plain_item_and_transform(tmp_path):
    p = write_3mf(tmp_path / "a.3mf", {"1": box(10, 10, 10)}, [("1", SHIFT)])
    t = load_3mf(p)
    assert t.root_path == "3D/3dmodel.model"
    assert t.unit_mm == 1.0
    assert t.mesh.bounds() == ((100, 50, 5), (110, 60, 15))
    assert len(t.item_meshes) == 1 and t.items[0].printable


def test_item_printable_flag_and_namespaced_attrs(tmp_path):
    xml = (
        '<m:model xmlns:m="http://schemas.microsoft.com/3dmanufacturing/2013/01"><m:resources>'
        '<m:object id="1"><m:mesh><m:vertices><m:vertex x="0" y="0" z="0"/><m:vertex x="1" y="0" z="0"/>'
        '<m:vertex x="0" y="1" z="0"/></m:vertices><m:triangles><m:triangle v1="0" v2="1" v3="2"/>'
        "</m:triangles></m:mesh></m:object></m:resources><m:build>"
        '<m:item m:objectid="1" printable="0"/></m:build></m:model>'
    )
    p = write_3mf(tmp_path / "ns.3mf", {}, [], xml=xml)
    t = load_3mf(p)
    assert not t.items[0].printable
    assert t.mesh.triangle_count == 1


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("inch", 25.4), ("centimeter", 10.0), ("micron", 0.001), ("meter", 1000.0), ("foot", 304.8), (None, 1.0)],
)
def test_unit_scaling(tmp_path, unit, factor):
    p = write_3mf(tmp_path / "u.3mf", {"1": box(1, 2, 3)}, [("1", None)], unit=unit)
    t = load_3mf(p)
    assert t.unit_mm == factor
    assert t.mesh.size() == pytest.approx((1 * factor, 2 * factor, 3 * factor))


def test_unknown_unit_refused(tmp_path):
    p = write_3mf(tmp_path / "u.3mf", {"1": box()}, [("1", None)], unit="parsec")
    with pytest.raises(threemf.MeshError, match="Unknown 3MF unit"):
        load_3mf(p)


def test_components_compose_with_item_transform(tmp_path):
    comp_t = "1 0 0 0 1 0 0 0 1 10 0 0"
    objs = {"1": box(1, 1, 1), "2": [("1", comp_t, None)]}
    p = write_3mf(tmp_path / "c.3mf", objs, [("2", "1 0 0 0 1 0 0 0 1 0 20 0")])
    t = load_3mf(p)
    assert t.mesh.bounds() == ((10, 20, 0), (11, 21, 1))


def test_rotated_component_world_coordinates(tmp_path):
    # 90 degrees about z in 3MF row-vector order: x' = [x y z 1] M
    rot = "0 1 0 -1 0 0 0 0 1 0 0 0"
    p = write_3mf(tmp_path / "r.3mf", {"1": box(10, 2, 1)}, [("1", rot)])
    assert load_3mf(p).mesh.size() == pytest.approx((2, 10, 1))


def test_root_model_from_rels(tmp_path):
    p = write_3mf(tmp_path / "r.3mf", {"1": box()}, [("1", None)], root="Custom/main.model", rels=True)
    assert load_3mf(p).root_path == "Custom/main.model"
    assert is_unsliced_3mf(p)


def test_rels_pointing_nowhere_falls_back_to_default(tmp_path):
    p = tmp_path / "r.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "_rels/.rels", f'<Relationships><Relationship Target="/gone.model" Type="{MODEL_REL}"/></Relationships>'
        )
        zf.writestr("3D/3dmodel.model", model_xml({"1": box()}, [("1", None)]))
    assert load_3mf(str(p)).root_path == "3D/3dmodel.model"


def test_external_component_part(tmp_path):
    p = tmp_path / "e.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml({"2": [("1", None, "/3D/Objects/o.model")]}, [("2", None)]))
        zf.writestr("3D/Objects/o.model", model_xml({"1": box(4, 4, 4)}, []))
    assert load_3mf(str(p)).mesh.size() == (4, 4, 4)


def test_doctype_entity_refused(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        '<model xmlns="x"><resources/><build/></model>'
    )
    p = write_3mf(tmp_path / "b.3mf", {}, [], xml=bomb)
    with pytest.raises(threemf.MeshError, match="DTDs"):
        load_3mf(p)


def test_malformed_xml_refused(tmp_path):
    p = write_3mf(tmp_path / "b.3mf", {}, [], xml="<model><oops></model>")
    with pytest.raises(threemf.MeshError, match="not well-formed"):
        load_3mf(p)


def test_oversized_member_refused(tmp_path, monkeypatch):
    p = write_3mf(tmp_path / "big.3mf", {"1": box()}, [("1", None)])
    monkeypatch.setattr(threemf, "MAX_MODEL_FILE_BYTES", 50)
    with pytest.raises(threemf.MeshError, match="larger than"):
        load_3mf(p)


def test_not_a_zip_and_no_model(tmp_path):
    bad = tmp_path / "bad.3mf"
    bad.write_bytes(b"nope")
    with pytest.raises(threemf.MeshError, match="Not a readable 3MF"):
        load_3mf(str(bad))
    empty = tmp_path / "empty.3mf"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("a.txt", "x")
    with pytest.raises(threemf.MeshError, match="no 3D model part"):
        load_3mf(str(empty))


def test_cycle_and_depth_refused(tmp_path):
    p = write_3mf(tmp_path / "cyc.3mf", {"1": [("2", None, None)], "2": [("1", None, None)]}, [("1", None)])
    with pytest.raises(threemf.MeshError, match="cyclic or too deep"):
        load_3mf(p)
    chain = {str(i): [(str(i + 1), None, None)] for i in range(1, 20)}
    chain["20"] = box()
    p2 = write_3mf(tmp_path / "deep.3mf", chain, [("1", None)])
    with pytest.raises(threemf.MeshError, match="cyclic or too deep"):
        load_3mf(p2)


def test_structural_errors(tmp_path):
    p = write_3mf(tmp_path / "nb.3mf", {"1": box()}, [])
    with pytest.raises(threemf.MeshError, match="no build items"):
        load_3mf(p)
    p = write_3mf(tmp_path / "missing.3mf", {"1": box()}, [("9", None)])
    with pytest.raises(threemf.MeshError, match="does not exist"):
        load_3mf(p)
    p = write_3mf(tmp_path / "nomesh.3mf", {"1": []}, [("1", None)])
    with pytest.raises(threemf.MeshError, match="no triangles"):
        load_3mf(p)


@pytest.mark.parametrize(
    ("fragment", "msg"),
    [
        ('<vertex x="a" y="0" z="0"/>', "Malformed vertex"),
        ('<vertex x="0" y="0"/>', "Malformed vertex"),
        ('<triangle v1="0" v2="x" v3="0"/>', "Malformed triangle"),
        ('<triangle v1="0" v2="1"/>', "Malformed triangle"),
        ('<triangle v1="0" v2="1" v3="7"/>', "vertex that does not exist"),
    ],
)
def test_malformed_mesh_records(tmp_path, fragment, msg):
    verts = '<vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>'
    tri = '<triangle v1="0" v2="1" v3="2"/>'
    inner = (verts + tri + fragment) if fragment.startswith("<triangle") else (fragment + verts + tri)
    xml = (
        '<model xmlns="x"><resources><object id="1"><mesh><vertices>'
        + inner.split("<triangle")[0]
        + "</vertices><triangles><triangle"
        + "<triangle".join(inner.split("<triangle")[1:])
        + '</triangles></mesh></object></resources><build><item objectid="1"/></build></model>'
    )
    if fragment.startswith("<vertex"):
        xml = (
            f'<model xmlns="x"><resources><object id="1"><mesh><vertices>{fragment}{verts}</vertices>'
            f'<triangles>{tri}</triangles></mesh></object></resources><build><item objectid="1"/></build></model>'
        )
    p = write_3mf(tmp_path / "m.3mf", {}, [], xml=xml)
    with pytest.raises(threemf.MeshError, match=msg):
        load_3mf(p)


@pytest.mark.parametrize(
    "build_xml",
    ["<item/>", ""],
)
def test_attribute_errors(tmp_path, build_xml):
    xml = f'<model xmlns="x"><resources><object><mesh/></object></resources><build>{build_xml}</build></model>'
    p = write_3mf(tmp_path / "o.3mf", {}, [], xml=xml)
    with pytest.raises(threemf.MeshError, match="no id"):
        load_3mf(p)


def test_component_and_item_without_objectid(tmp_path):
    xml = (
        '<model xmlns="x"><resources><object id="1"><components><component/></components></object></resources></model>'
    )
    with pytest.raises(threemf.MeshError, match="no objectid"):
        load_3mf(write_3mf(tmp_path / "c.3mf", {}, [], xml=xml))
    xml = '<model xmlns="x"><resources/><build><item/></build></model>'
    with pytest.raises(threemf.MeshError, match="no objectid"):
        load_3mf(write_3mf(tmp_path / "i.3mf", {}, [], xml=xml))


def test_triangle_budget(tmp_path, monkeypatch):
    p = write_3mf(tmp_path / "t.3mf", {"1": box()}, [("1", None)])
    real = threemf._PartParser.__init__

    def tiny(self, part, budget):
        real(self, part, [5])

    monkeypatch.setattr(threemf._PartParser, "__init__", tiny)
    with pytest.raises(threemf.MeshError, match="more than"):
        load_3mf(p)


def test_two_items_merge(tmp_path):
    p = write_3mf(tmp_path / "two.3mf", {"1": box(1, 1, 1)}, [("1", None), ("1", "1 0 0 0 1 0 0 0 1 5 0 0")])
    t = load_3mf(p)
    assert len(t.items) == 2 and t.mesh.triangle_count == 24
    assert t.mesh.bounds() == ((0, 0, 0), (6, 1, 1))


def test_transform_parse_format_compose():
    assert parse_transform(None) == threemf.IDENTITY_AFFINE
    assert parse_transform("  ") == threemf.IDENTITY_AFFINE
    aff = parse_transform("0 1 0 -1 0 0 0 0 1 3 4 5")
    assert aff[1] == (3, 4, 5)
    assert parse_transform(format_transform(aff)) == aff
    assert format_transform(threemf.IDENTITY_AFFINE) == IDENT
    for bad in ("1 2 3", "a b c d e f g h i j k l"):
        with pytest.raises(threemf.MeshError, match="Malformed 3MF transform"):
            parse_transform(bad)
    shift = ((rotation_about("z", 0)), (1.0, 0.0, 0.0))
    turn = (rotation_about("z", 90), (0.0, 0.0, 0.0))
    # turn first, then shift: translation untouched
    assert compose(shift, turn)[1] == (1.0, 0.0, 0.0)
    # shift first, then turn: (1,0,0) -> (0,1,0)
    assert compose(turn, shift)[1] == (0.0, 1.0, 0.0)


def test_format_transform_trims_noise():
    text = format_transform((rotation_about("z", 45), (0.1 + 0.2, -1e-15, 2.0)))
    assert text.split()[9:] == ["0.3", "0", "2"]


def test_item_affine_mm():
    item = BuildItem("1", (rotation_about("z", 0), (1.0, 2.0, 3.0)), True)
    assert item_affine_mm(item, 25.4)[1] == pytest.approx((25.4, 50.8, 76.2))


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def test_rewrite_replaces_existing_transform(tmp_path):
    src = write_3mf(tmp_path / "s.3mf", {"1": box()}, [("1", SHIFT)])
    dst = str(tmp_path / "d.3mf")
    new = (rotation_about("z", 0), (7.0, 8.0, 9.0))
    rewrite_item_transforms(src, dst, "3D/3dmodel.model", {0: new})
    text = _members(dst)["3D/3dmodel.model"].decode()
    assert 'transform="1 0 0 0 1 0 0 0 1 7 8 9"' in text
    assert SHIFT not in text
    assert load_3mf(dst).mesh.bounds()[0] == (7, 8, 9)


def test_rewrite_inserts_missing_transform_and_keeps_other_members(tmp_path):
    src = write_3mf(tmp_path / "s.3mf", {"1": box()}, [("1", None)], gcode=False, extra={"Extra/blob.bin": bytes(300)})
    dst = str(tmp_path / "d.3mf")
    rewrite_item_transforms(src, dst, "3D/3dmodel.model", {0: (rotation_about("z", 0), (1.0, 2.0, 3.0))})
    a, b = _members(src), _members(dst)
    assert list(a) == list(b)
    for name in a:
        if name != "3D/3dmodel.model":
            assert a[name] == b[name]
    assert b'<item transform="1 0 0 0 1 0 0 0 1 1 2 3" objectid="1"/>' in b["3D/3dmodel.model"]


def test_rewrite_only_selected_item(tmp_path):
    src = write_3mf(tmp_path / "s.3mf", {"1": box()}, [("1", None), ("1", SHIFT)])
    dst = str(tmp_path / "d.3mf")
    rewrite_item_transforms(src, dst, "3D/3dmodel.model", {1: (rotation_about("z", 0), (0.0, 0.0, 0.0))})
    text = _members(dst)["3D/3dmodel.model"].decode()
    assert text.count("transform=") == 1


def test_rewrite_errors(tmp_path):
    src = write_3mf(tmp_path / "s.3mf", {"1": box()}, [("1", None)])
    dst = str(tmp_path / "d.3mf")
    ident = (rotation_about("z", 0), (0.0, 0.0, 0.0))
    with pytest.raises(threemf.MeshError, match="fewer items"):
        rewrite_item_transforms(src, dst, "3D/3dmodel.model", {3: ident})
    with pytest.raises(threemf.MeshError, match="missing"):
        rewrite_item_transforms(src, dst, "nope.model", {0: ident})
    nobuild = write_3mf(tmp_path / "nb.3mf", {}, [], xml="<model/>")
    with pytest.raises(threemf.MeshError, match="no <build>"):
        rewrite_item_transforms(nobuild, dst, "3D/3dmodel.model", {0: ident})
    binary = tmp_path / "bin.3mf"
    with zipfile.ZipFile(binary, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"\xff\xfe\x00")
    with pytest.raises(threemf.MeshError, match="not UTF-8"):
        rewrite_item_transforms(str(binary), dst, "3D/3dmodel.model", {0: ident})


def test_rewrite_refuses_oversized_other_member(tmp_path, monkeypatch):
    src = write_3mf(tmp_path / "s.3mf", {"1": box()}, [("1", None)], extra={"big.bin": bytes(5000)})
    monkeypatch.setattr(threemf, "MAX_MODEL_FILE_BYTES", 4000)
    # model part itself is under the cap, the extra member is not
    with pytest.raises(threemf.MeshError, match="too large to copy"):
        rewrite_item_transforms(
            src, str(tmp_path / "d.3mf"), "3D/3dmodel.model", {0: (rotation_about("z", 0), (0.0, 0.0, 0.0))}
        )
