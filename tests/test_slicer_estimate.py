"""Tests for bambu_cli.slicer.estimate."""

from __future__ import annotations

import zipfile

import pytest

from bambu_cli.slicer.estimate import Estimate, format_estimate, read_3mf_estimate

# Sample .3mf files are BUILT AT TEST TIME rather than committed: privacy_smoke
# rejects any committed .3mf as a generated printer-ready artifact, and that guard
# is worth more than the convenience of checked-in binaries. They are tiny
# hand-written zips anyway, and building them here keeps the expected values
# visible next to the assertions instead of hidden inside an opaque file.

# ---------------------------------------------------------------------------
# read_3mf_estimate — whole-file behavior
# ---------------------------------------------------------------------------


def _write_zip(path, members: dict) -> str:
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return str(path)


SLICE_INFO_FULL = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<config>\n  <plate>\n"
    b'    <metadata key="prediction" value="6120"/>\n'
    b'    <metadata key="weight" value="13.05"/>\n'
    b"  </plate>\n</config>\n"
)


def test_full_estimate(tmp_path):
    path = _write_zip(tmp_path / "full.3mf", {"Metadata/slice_info.config": SLICE_INFO_FULL})
    assert read_3mf_estimate(path) == Estimate(seconds=6120, grams=13.05)


def test_gcode_only_estimate(tmp_path):
    # 2h 5m 30s = 7200 + 300 + 30 = 7530
    gcode = b"; model printing time: 2h 5m 30s\n; total filament weight [g] : 25.50\nG28\n"
    path = _write_zip(tmp_path / "gcode_only.3mf", {"Metadata/plate_1.gcode": gcode})
    assert read_3mf_estimate(path) == Estimate(seconds=7530, grams=25.50)


def test_corrupt_never_raises(tmp_path):
    path = tmp_path / "corrupt.3mf"
    path.write_bytes(b"not a zip")
    assert read_3mf_estimate(str(path)) == Estimate(None, None)


def test_missing_sources(tmp_path):
    """A valid zip carrying no estimate-bearing member."""
    path = _write_zip(tmp_path / "missing.3mf", {"3D/3dmodel.model": b"<model/>"})
    assert read_3mf_estimate(path) == Estimate(None, None)


def test_unparseable_slice_info_falls_back_to_gcode(tmp_path):
    """Malformed XML must not shadow a readable gcode header."""
    path = _write_zip(
        tmp_path / "broken_xml.3mf",
        {
            "Metadata/slice_info.config": b"<config><plate>truncated",
            "Metadata/plate_1.gcode": b"; model printing time: 45m 0s\n",
        },
    )
    assert read_3mf_estimate(path).seconds == 2700


def test_slice_info_with_only_implausible_values_falls_back_to_gcode(tmp_path):
    """A well-formed config carrying junk must not shadow a good gcode header."""
    bad_config = (
        b"<config>\n  <plate>\n"
        b'    <metadata key="prediction" value="0"/>\n'
        b'    <metadata key="weight" value="-5"/>\n'
        b"  </plate>\n</config>\n"
    )
    path = _write_zip(
        tmp_path / "implausible.3mf",
        {
            "Metadata/slice_info.config": bad_config,
            "Metadata/plate_1.gcode": b"; model printing time: 1h 0m 0s\n",
        },
    )
    assert read_3mf_estimate(path).seconds == 3600


def test_later_plate_used_when_first_has_no_header(tmp_path):
    """An early plate with no usable header must not end the search."""
    path = _write_zip(
        tmp_path / "multiplate.3mf",
        {
            "Metadata/plate_1.gcode": b"G28\nG1 X0 Y0\n",
            "Metadata/plate_2.gcode": b"; model printing time: 30m 0s\n",
        },
    )
    assert read_3mf_estimate(path).seconds == 1800


def test_nonexistent_file_never_raises():
    est = read_3mf_estimate("/nonexistent/path/file.3mf")
    assert est == Estimate(None, None)


# ---------------------------------------------------------------------------
# read_3mf_estimate — implausible value handling
# ---------------------------------------------------------------------------


def _make_slice_info_zip(tmp_path, prediction: str | None, weight: str | None) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>", "  <plate>"]
    if prediction is not None:
        lines.append(f'    <metadata key="prediction" value="{prediction}"/>')
    if weight is not None:
        lines.append(f'    <metadata key="weight" value="{weight}"/>')
    lines += ["  </plate>", "</config>"]
    xml = "\n".join(lines).encode()
    path = str(tmp_path / "test.3mf")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", xml)
    return path


def test_implausible_seconds_zero(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="0", weight="10.0")
    assert read_3mf_estimate(path).seconds is None


def test_implausible_seconds_negative(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="-100", weight="10.0")
    assert read_3mf_estimate(path).seconds is None


def test_implausible_seconds_too_large(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="2592001", weight="10.0")
    assert read_3mf_estimate(path).seconds is None


def test_implausible_grams_zero(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="600", weight="0")
    assert read_3mf_estimate(path).grams is None


def test_implausible_grams_negative(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="600", weight="-5.0")
    assert read_3mf_estimate(path).grams is None


def test_implausible_grams_too_large(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="600", weight="10001.0")
    assert read_3mf_estimate(path).grams is None


def test_only_prediction_no_weight(tmp_path):
    path = _make_slice_info_zip(tmp_path, prediction="600", weight=None)
    est = read_3mf_estimate(path)
    assert est.seconds == 600
    assert est.grams is None


def test_gcode_fallback_used_when_no_slice_info(tmp_path):
    """When slice_info.config is absent, gcode header is used as fallback."""
    path = str(tmp_path / "gcode_only.3mf")
    gcode = b"; model printing time: 1h 0m 0s\n; total filament weight [g] : 5.00\nM104 S220\n"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", gcode)
    est = read_3mf_estimate(path)
    assert est.seconds == 3600
    assert est.grams == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# format_estimate
# ---------------------------------------------------------------------------


def test_format_both():
    result = format_estimate(Estimate(6120, 13.05))
    assert "1h 42m" in result
    assert "~13 g" in result


def test_format_none_none():
    assert format_estimate(Estimate(None, None)) == "estimate unavailable"


def test_format_time_only():
    result = format_estimate(Estimate(3661, None))
    assert "g" not in result
    assert "1h" in result or "m" in result


def test_format_grams_only():
    result = format_estimate(Estimate(None, 25.5))
    assert "~26 g" in result or "~25" in result
    assert "h" not in result
