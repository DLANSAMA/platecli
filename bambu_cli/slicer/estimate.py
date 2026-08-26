"""Parse time/filament estimates from OrcaSlicer-produced .3mf files."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

_MAX_SECONDS = 2592000  # 30 days
_MAX_GRAMS = 10000.0

GCODE_READ_BYTES = 65536  # 64 KB
SLICE_INFO_READ_BYTES = 10 * 1024 * 1024  # 10 MB safety limit for XML config (Zip Bomb protection)


@dataclass(frozen=True)
class Estimate:
    seconds: int | None
    grams: float | None


def _parse_slice_info(xml_text: str) -> tuple[int | None, float | None]:
    """Parse prediction (seconds) and weight (grams) from slice_info.config XML.

    Uses xml.etree.ElementTree to parse bounded metadata XML from .3mf packages.
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
                info = zf.getinfo(slice_info_name)
                # Security: Enforce size limit to prevent memory exhaustion / Zip Bomb DoS on untrusted .3mf files
                if info.file_size <= SLICE_INFO_READ_BYTES:
                    with zf.open(slice_info_name) as fh:
                        xml_bytes = fh.read(SLICE_INFO_READ_BYTES)
                    xml_text = xml_bytes.decode("utf-8", errors="replace")
                    seconds, grams = _parse_slice_info(xml_text)
                    if seconds is not None or grams is not None:
                        return Estimate(seconds, grams)
                # slice_info.config was present but yielded nothing usable
                # (malformed XML, or only implausible values).  Fall through to
                # the gcode header rather than reporting "unknown" -- a truncated
                # config next to a perfectly readable gcode header is exactly the
                # case the fallback exists for.

            # Fallback: gcode members under Metadata/, in plate order.  Keep
            # trying later plates if an earlier one carries no usable header.
            gcode_members = sorted(
                n
                for n in names
                if len(n.replace("\\", "/").split("/")) == 2
                and n.replace("\\", "/").split("/")[0] == "Metadata"
                and n.replace("\\", "/").split("/")[1].endswith(".gcode")
            )
            for n in gcode_members:
                # Read only the header window; a real plate gcode can be tens of MB.
                with zf.open(n) as fh:
                    gcode_bytes = fh.read(GCODE_READ_BYTES)
                seconds, grams = _parse_gcode_header(gcode_bytes)
                if seconds is not None or grams is not None:
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
