"""Test-side writer for OrcaSlicer 2.5.x packed preset caches (``<vendor>.opc``).

``bambu_cli.slicer.packed_profiles`` only reads the format, so tests need their
own writer to put a small cache under ``tmp_path``. It follows the same layout
(``src/libslic3r/PresetCacheFormat.cpp`` at v2.5.2): a 20-byte header, then a
cereal binary body. Every knob a test needs to build a *wrong* cache is a
keyword, so no test has to patch bytes by offset.

A preset is a plain dict::

    {"name": "Bambu Lab P1P 0.4 nozzle", "inherits": "fdm_machine_common",
     "includes": ["... template machine_start_gcode"], "config": {...}}

Config values are strings (a ``coString``) or lists of strings (``coStrings``)
unless ``key_types`` names another ``ConfigOptionType`` code for the key, in
which case the value is the native Python form listed in ``_encode_value``.
"""

from __future__ import annotations

import json
import os
import struct
import zlib

MAGIC = 0x4F52435A
VECTOR = 0x4000
FLOAT, INT, STRING, PERCENT, FLOAT_OR_PERCENT, POINT, POINT3, BOOL, ENUM = range(1, 10)
POINTS_GROUPS = 10 + VECTOR
INTS_GROUPS = 11 + VECTOR
INT_NIL = 0x7FFFFFFF
BOOL_NIL = 255
KINDS = ("process", "filament", "machine")


def _u8(value: int) -> bytes:
    return struct.pack("<B", value)


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _i32(value: int) -> bytes:
    return struct.pack("<i", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _f64(value: float) -> bytes:
    return struct.pack("<d", value)


def _text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _u64(len(raw)) + raw


def _texts(values) -> bytes:
    return _u64(len(values)) + b"".join(_text(v) for v in values)


def _point(value) -> bytes:
    return _f64(value[0]) + _f64(value[1])


class _Dictionary:
    """Setting keys, their type codes, and enum value names (index 0 is unnamed)."""

    def __init__(self, key_types: dict[str, int]) -> None:
        self.key_types = key_types
        self.keys: list[str] = []
        self.types: list[int] = []
        self.enums: list[str] = [""]

    def key_index(self, key: str, value) -> int:
        if key not in self.keys:
            self.keys.append(key)
            self.types.append(self.key_types.get(key, STRING + VECTOR if isinstance(value, list) else STRING))
        return self.keys.index(key)

    def enum_index(self, name: str) -> int:
        if name not in self.enums:
            self.enums.append(name)
        return self.enums.index(name)


def _enum_items(dictionary: _Dictionary, values) -> bytes:
    out = _u32(len(values))
    for value in values:
        if isinstance(value, int):  # a value with no name: index 0, then the raw integer
            out += _u16(0) + _i32(value)
        else:
            out += _u16(dictionary.enum_index(value))
    return out


def _encode_value(dictionary: _Dictionary, type_code: int, value) -> bytes:
    """Native forms: float/int/str/bool scalars; ``(number, is_percent)`` for a
    float-or-percent; ``(x, y)`` for a point; an enum name (or a raw int)."""
    scalar = type_code & ~VECTOR
    if type_code == POINTS_GROUPS:
        return _u64(len(value)) + b"".join(_u64(len(g)) + b"".join(_point(p) for p in g) for g in value)
    if type_code == INTS_GROUPS:
        return _u64(len(value)) + b"".join(_u64(len(g)) + b"".join(_i32(i) for i in g) for g in value)
    if scalar == ENUM:
        return _enum_items(dictionary, value if type_code & VECTOR else [value])
    encoders = {
        FLOAT: _f64,
        PERCENT: _f64,
        INT: _i32,
        STRING: _text,
        FLOAT_OR_PERCENT: lambda v: _f64(v[0]) + _u8(1 if v[1] else 0),
        POINT: _point,
        POINT3: lambda v: b"".join(_f64(c) for c in v),
        BOOL: lambda v: _u8(int(v)),
    }
    if scalar not in encoders:
        # An unknown code: write nothing for it, the reader must refuse the key.
        return b""
    encode = encoders[scalar]
    if type_code & VECTOR:
        return _u64(len(value)) + b"".join(encode(v) for v in value)
    return encode(value)


def _vendor_map(vendor: str) -> bytes:
    """One vendor with one printer model, so the reader's skip code is exercised."""
    model = (
        _text("P1P")
        + _text("Bambu Lab P1P")
        + _text("C12")
        + _u8(0)
        + _text("BBL-3DP")
        + _texts(["0.4", "0.2"])
        + _texts(["Bambu PLA Basic @BBL P1P"])
        + _texts([])
        + b"".join(_text(v) for v in ("bed.stl", "bed.svg", "", "", "", "", "", "", "hotend.stl"))
    )
    profile = (
        b"".join(_text(v) for v in (vendor, vendor, "02.00.00.00", "", ""))
        + _u64(1)
        + model
        + _texts(["Bambu PLA Basic @BBL P1P"])
        + _texts([])
    )
    return _u64(1) + _text(vendor) + profile


def build_body(
    presets: dict[str, list[dict]],
    *,
    vendor: str = "BBL",
    vendor_version: str = "02.00.00.00",
    cache_version: int = 2,
    parse_errors: int = 0,
    key_types: dict[str, int] | None = None,
    bad_key_index: bool = False,
) -> bytes:
    """The body of a cache. ``bad_key_index`` points one setting past the dictionary."""
    dictionary = _Dictionary(key_types or {})
    lists = b""
    for kind in KINDS:
        entries = presets.get(kind, [])
        lists += _u32(len(entries))
        for preset in entries:
            config = preset.get("config", {})
            settings = b""
            for key, value in config.items():
                index = dictionary.key_index(key, value)
                settings += _u16(index) + _encode_value(dictionary, dictionary.types[index], value)
            if bad_key_index:
                settings += _u16(60000) + _text("x")
            count = len(config) + (1 if bad_key_index else 0)
            lists += (
                _text(preset["name"])
                + _text(preset.get("sub_path", f"{kind}/{preset['name']}.json"))
                + _u32(count)
                + settings
                + _text(preset.get("inherits", ""))
                + _texts(preset.get("includes", []))
                + _text(preset.get("description", ""))
                + _text(preset.get("instantiation", "true"))
                + _text(preset.get("setting_id", ""))
                + _text(preset.get("filament_id", ""))
                + _texts(preset.get("renamed_from", []))
            )
    head = (
        _u32(cache_version)
        + _text(vendor)
        + _text(vendor_version)
        + _texts(dictionary.keys)
        + _u64(len(dictionary.types))
        + b"".join(_u16(t) for t in dictionary.types)
        + _texts(dictionary.enums)
        + _vendor_map(vendor)
    )
    return head + lists + _u64(parse_errors)


def wrap(body: bytes, *, magic: int = MAGIC, format_version: int = 2, size: int | None = None, crc: int | None = None):
    """Put the 20-byte header on *body*; override a field to make it wrong."""
    return (
        struct.pack(
            "<IIQI",
            magic,
            format_version,
            len(body) if size is None else size,
            zlib.crc32(body) if crc is None else crc,
        )
        + body
    )


def build_opc(presets: dict[str, list[dict]], **body_options) -> bytes:
    """A complete, valid cache file."""
    return wrap(build_body(presets, **body_options))


# The smallest set that slices the default path (P1P, 0.4 nozzle, 0.20 mm
# Standard, PLA Basic) and has what makes 2.5.x different: the machine takes its
# start G-code from an included template, not from itself or its parent.
START_GCODE_TEMPLATE = "Bambu Lab P1P 0.4 nozzle template machine_start_gcode"
P1P_PRESETS: dict[str, list[dict]] = {
    "machine": [
        {
            "name": "fdm_machine_common",
            "instantiation": "false",
            "config": {"machine_start_gcode": "G28 ; generic", "printable_height": "250", "gcode_flavor": "marlin"},
        },
        {
            "name": START_GCODE_TEMPLATE,
            "instantiation": "false",
            "config": {"machine_start_gcode": ";===== machine: P1P-0.4 =====\nG28"},
        },
        {
            "name": "Bambu Lab P1P 0.4 nozzle",
            "inherits": "fdm_machine_common",
            "includes": [START_GCODE_TEMPLATE],
            "setting_id": "GM014",
            "config": {
                "nozzle_diameter": ["0.4"],
                "printer_model": "Bambu Lab P1P",
                "layer_change_gcode": "G92 E0\n",
            },
        },
    ],
    "process": [
        {"name": "fdm_process_common", "instantiation": "false", "config": {"wall_loops": "2"}},
        {
            "name": "0.20mm Standard @BBL P1P",
            "inherits": "fdm_process_common",
            "config": {"layer_height": "0.2", "compatible_printers": ["Bambu Lab P1P 0.4 nozzle"]},
        },
    ],
    "filament": [
        {"name": "fdm_filament_pla", "instantiation": "false", "config": {"nozzle_temperature": ["220"]}},
        {
            "name": "Bambu PLA Basic @base",
            "inherits": "fdm_filament_pla",
            "filament_id": "GFA00",
            "instantiation": "false",
            "config": {"filament_type": ["PLA"]},
        },
    ],
}


def build_packed_install(root: str, data: bytes | None = None, *, vendor: str = "BBL") -> str:
    """Lay out ``<root>/profiles/<vendor>.opc`` beside a 2.5.x-style ``profiles/<vendor>/``.

    That folder holds what a real 2.5.x release keeps loose: lookup tables under
    ``filament/`` that are *not* filament presets, and no ``machine/`` at all.
    Returns the folder a user would set as ``profiles_dir``.
    """
    profiles_dir = os.path.join(root, "profiles", vendor)
    tables = os.path.join(profiles_dir, "filament")
    os.makedirs(tables, exist_ok=True)
    for name in ("filaments_color_codes.json", "filament_id_map.json"):
        with open(os.path.join(tables, name), "w", encoding="utf-8") as handle:
            json.dump({"version": "1", "data": []}, handle)
    with open(f"{profiles_dir}.opc", "wb") as handle:
        handle.write(build_opc(P1P_PRESETS, vendor=vendor) if data is None else data)
    return profiles_dir
