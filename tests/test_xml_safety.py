"""Tests for XML entity expansion protection and parsing in slicer/estimate.py."""

import zipfile

from bambu_cli.slicer.estimate import _parse_slice_info, read_3mf_estimate


def test_parse_slice_info_rejects_doctype_and_entity():
    # DTD entity bomb attempt
    malicious_xml = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
     <!ENTITY lol "lol">
     <!ELEMENT lolz (#PCDATA)>
     <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <config>
        <metadata key="prediction" value="120"/>
    </config>"""

    seconds, grams = _parse_slice_info(malicious_xml)
    assert seconds is None
    assert grams is None


def test_parse_slice_info_valid_metadata():
    valid_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <config>
        <metadata key="prediction" value="3600"/>
        <metadata key="weight" value="45.5"/>
    </config>"""

    seconds, grams = _parse_slice_info(valid_xml)
    assert seconds == 3600
    assert grams == 45.5


def test_read_3mf_estimate_rejects_entity_bomb_in_3mf(tmp_path):
    path = tmp_path / "entity_bomb.3mf"
    bomb_xml = """<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <config><metadata key="prediction" value="100"/></config>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", bomb_xml)

    est = read_3mf_estimate(str(path))
    assert est.seconds is None
    assert est.grams is None
