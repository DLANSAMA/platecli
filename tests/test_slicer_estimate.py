"""Tests for bambu_cli.slicer.estimate."""

from __future__ import annotations

import os
import zipfile

import pytest

from bambu_cli.slicer.estimate import Estimate, format_estimate, read_3mf_estimate

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


# ---------------------------------------------------------------------------
# read_3mf_estimate — fixture-based
# ---------------------------------------------------------------------------


def test_full_estimate():
    est = read_3mf_estimate(fixture("estimate_full.3mf"))
    assert est == Estimate(seconds=6120, grams=13.05)


def test_gcode_only_estimate():
    # 2h 5m 30s = 7200 + 300 + 30 = 7530
    est = read_3mf_estimate(fixture("estimate_gcode_only.3mf"))
    assert est == Estimate(seconds=7530, grams=25.50)


def test_corrupt_never_raises():
    est = read_3mf_estimate(fixture("estimate_corrupt.3mf"))
    assert est == Estimate(None, None)


def test_missing_sources():
    est = read_3mf_estimate(fixture("estimate_missing.3mf"))
    assert est == Estimate(None, None)


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
