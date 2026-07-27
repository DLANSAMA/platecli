"""Parse time/filament estimates from OrcaSlicer-produced .3mf files."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

_MAX_SECONDS = 2592000  # 30 days
_MAX_GRAMS = 10000.0

GCODE_READ_BYTES = 65536  # 64 KB


@dataclass(frozen=True)
class Estimate:
    seconds: int | None
    grams: float | None


def _parse_slice_info(xml_text: str) -> tuple[int | None, float | None]:
    """Parse prediction (seconds) and weight (grams) from slice_info.config XML.

    Uses xml.etree.ElementTree which is safe for local files we produced;
    this is not parsing untrusted network XML.
    """
    try:
        root = ET.fromstring(xml_text)  # nosec B314 — local file produced by OrcaSlicer, not network input
    except ET.ParseError:
        return None, None

    seconds: int | None = None
    grams: float | None = None

    for elem in root.iter("metadata"):
        key = elem.get("key", "")
        value = elem.get("value", "")
        if key == "prediction":
            try:
                v = int(value)
                if 0 < v <= _MAX_SECONDS:
                    seconds = v
            except (ValueError, TypeError):
                pass
        elif key == "weight":
            try:
                v_f = float(value)
                if 0 < v_f <= _MAX_GRAMS:
                    grams = v_f
            except (ValueError, TypeError):
                pass

    return seconds, grams


_TIME_PATTERN = re.compile(r"^(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")


def _parse_gcode_time(text: str) -> int | None:
    """Convert a gcode time string like '1h 42m 15s' to total seconds."""
    m = _TIME_PATTERN.match(text.strip())
    if not m or not any(m.groups()):
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    total = hours * 3600 + minutes * 60 + secs
    if 0 < total <= _MAX_SECONDS:
        return total
    return None


def _parse_gcode_header(gcode_bytes: bytes) -> tuple[int | None, float | None]:
    """Parse estimates from the first 64 KB of a gcode file."""
    text = gcode_bytes[:GCODE_READ_BYTES].decode("utf-8", errors="replace")
    seconds: int | None = None
    grams: float | None = None

    for line in text.splitlines():
        if seconds is None and line.startswith("; model printing time:"):
            time_str = line.split(":", 1)[1].strip()
            seconds = _parse_gcode_time(time_str)
        elif grams is None and line.startswith("; total filament weight [g] :"):
            weight_str = line.split(":", 1)[1].strip()
            try:
                v = float(weight_str)
                if 0 < v <= _MAX_GRAMS:
                    grams = v
            except (ValueError, TypeError):
                pass

    return seconds, grams


def read_3mf_estimate(path: str) -> Estimate:
    """Parse time/filament estimate from a .3mf file.

    Returns Estimate(None, None) on any failure — never raises.
    Primary source: Metadata/slice_info.config (XML).
    Fallback: Metadata/plate_N.gcode header comments (first 64 KB).
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()

            # Primary: Metadata/slice_info.config
            slice_info_name: str | None = None
            for n in names:
                normalised = n.replace("\\", "/")
                parts = normalised.split("/")
                if len(parts) == 2 and parts[0] == "Metadata" and parts[1] == "slice_info.config":
                    slice_info_name = n
                    break

            if slice_info_name is not None:
                xml_text = zf.read(slice_info_name).decode("utf-8", errors="replace")
                seconds, grams = _parse_slice_info(xml_text)
                return Estimate(seconds, grams)

            # Fallback: first .gcode file under Metadata/
            for n in names:
                normalised = n.replace("\\", "/")
                parts = normalised.split("/")
                if len(parts) == 2 and parts[0] == "Metadata" and parts[1].endswith(".gcode"):
                    gcode_bytes = zf.read(n)
                    seconds, grams = _parse_gcode_header(gcode_bytes)
                    return Estimate(seconds, grams)

    except Exception:  # noqa: BLE001 — never raise, degrade gracefully
        pass

    return Estimate(None, None)


def format_estimate(est: Estimate) -> str:
    """Return a human-readable string for an Estimate.

    Examples: '1h 42m, ~13 g', '42m', '~25.5 g', 'estimate unavailable'.
    """
    parts: list[str] = []

    if est.seconds is not None:
        h = est.seconds // 3600
        m = (est.seconds % 3600) // 60
        if h > 0:
            parts.append(f"{h}h {m:02d}m")
        else:
            parts.append(f"{m}m")

    if est.grams is not None:
        parts.append(f"~{est.grams:.0f} g")

    if not parts:
        return "estimate unavailable"

    return ", ".join(parts)
