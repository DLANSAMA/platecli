"""OrcaSlicer 2.5.x packed preset caches (``resources/profiles/<vendor>.opc``).

From 2.5.0 the release builds no longer ship a vendor's presets as loose JSON
under ``profiles/BBL/machine|process|filament``; they ship one binary cache per
vendor next to that folder (``profiles/BBL.opc``), and ``profiles/BBL/`` keeps
only a few lookup tables. The OrcaSlicer CLI has no option that names a system
preset or exports one: ``--load-settings`` still takes file paths. So the cache
is read here and written out once, as the loose tree the rest of ``slicer/``
already understands, into a per-user cache directory.

Layout of the file (``src/libslic3r/PresetCacheFormat.cpp`` at v2.5.2), all
little-endian:

* 20-byte header: magic ``ZCRO``, format version, body size, CRC32 of the body.
* body, a cereal binary archive: cache version, vendor name and version, a
  dictionary (setting keys, their ``ConfigOptionType`` codes, enum value names),
  the vendor's printer models, then the process, filament and machine presets,
  then a parse-error count.

The reader fails closed. Anything it does not recognise (another magic, format
or cache version, a size or checksum mismatch, an unknown value type, bytes
left over) raises :class:`PackedProfilesError`, which tells the user this
OrcaSlicer version is not supported yet and names the newest one that is. A
half-read cache must never become a slice with generic start G-code.

Preset files are named after the validated preset name and never after the
``sub_path`` the cache carries, so a crafted cache cannot write outside the
unpack directory.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from bambu_cli.errors import ConfigError
from bambu_cli.logging_utils import logger
from bambu_cli.paths import display_path as _display_path
from bambu_cli.paths import expand_path as _expand_path

__all__ = [
    "NEWEST_SUPPORTED_ORCA",
    "SUPPORTED_ORCA_VERSIONS",
    "PackedPreset",
    "PackedProfiles",
    "PackedProfilesError",
    "default_cache_root",
    "packed_cache_path",
    "read_packed_profiles",
    "resolve_profiles_dir",
]

# OrcaSlicer releases a real ``plate slice`` has been run against (2026-11-24,
# Linux AppImages). 2.4.x was never tried, so it is not listed.
SUPPORTED_ORCA_VERSIONS = ("2.3.1", "2.5.0", "2.5.1", "2.5.2")
NEWEST_SUPPORTED_ORCA = SUPPORTED_ORCA_VERSIONS[-1]

_MAGIC = 0x4F52435A  # the bytes "ZCRO"
_HEADER = struct.Struct("<IIQI")
_FORMAT_VERSION = 2
_CACHE_VERSION = 2
# The real BBL cache is 2.4 MB; this only stops a wrong path (a disk image, a
# video) from being read into memory whole.
_MAX_CACHE_BYTES = 256 * 1024 * 1024
# Bump when the unpacked files change shape, so an old unpack is not reused.
_UNPACK_LAYOUT = 1
_MARKER = "plate-unpacked.json"
# Order the three preset lists appear in the body.
_KINDS = ("process", "filament", "machine")

# ConfigOptionType codes (src/libslic3r/Config.hpp); a vector adds _VECTOR.
_VECTOR = 0x4000
_FLOAT, _INT, _STRING, _PERCENT, _FLOAT_OR_PERCENT = 1, 2, 3, 4, 5
_POINT, _POINT3, _BOOL, _ENUM = 6, 7, 8, 9
_POINTS_GROUPS, _INTS_GROUPS = 10 + _VECTOR, 11 + _VECTOR
_INT_NIL = 0x7FFFFFFF
_BOOL_NIL = 255
_NIL = "nil"


class PackedProfilesError(ConfigError):
    """A packed profile cache that cannot be read or unpacked."""

    failed_step = "profiles"


class _Malformed(ValueError):
    """Internal: the bytes do not follow the format this reader knows."""


def _unsupported(path: str, reason: str) -> PackedProfilesError:
    return PackedProfilesError(
        f"This OrcaSlicer version is not supported yet: its packed profile cache "
        f"{_display_path(path)} could not be read ({reason}). The newest OrcaSlicer plate "
        f"supports is {NEWEST_SUPPORTED_ORCA}; install that version and run `plate setup`.",
        extra={"packed_profiles": path, "newest_supported_orca": NEWEST_SUPPORTED_ORCA},
    )


@dataclass(frozen=True)
class PackedPreset:
    """One preset from a cache: its own settings, not yet merged with parents."""

    name: str
    config: dict[str, Any]
    inherits: str = ""
    includes: tuple[str, ...] = ()
    instantiation: str = ""
    setting_id: str = ""
    filament_id: str = ""
    description: str = ""


@dataclass(frozen=True)
class PackedProfiles:
    """Everything read from one ``<vendor>.opc``."""

    vendor: str
    vendor_version: str
    crc32: int
    body_size: int
    presets: dict[str, tuple[PackedPreset, ...]] = field(default_factory=dict)


class _Cursor:
    """Bounds-checked little-endian reader over the cache body."""

    __slots__ = ("_buf", "_pos")

    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self._pos = 0

    def remaining(self) -> int:
        return len(self._buf) - self._pos

    def take(self, size: int) -> bytes:
        if size < 0 or size > self.remaining():
            raise _Malformed("the data ends early")
        chunk = self._buf[self._pos : self._pos + size]
        self._pos += size
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int(struct.unpack("<H", self.take(2))[0])

    def u32(self) -> int:
        return int(struct.unpack("<I", self.take(4))[0])

    def i32(self) -> int:
        return int(struct.unpack("<i", self.take(4))[0])

    def u64(self) -> int:
        return int(struct.unpack("<Q", self.take(8))[0])

    def f64(self) -> float:
        return float(struct.unpack("<d", self.take(8))[0])

    def _checked_count(self, count: int) -> int:
        # Every element takes at least one byte, so a count larger than what is
        # left is corrupt; refusing it here also bounds every list allocation.
        if count > self.remaining():
            raise _Malformed("a list is longer than the data")
        return count

    def count(self) -> int:
        """A cereal size tag (strings, vectors, maps, sets)."""
        return self._checked_count(self.u64())

    def count32(self) -> int:
        """The 32-bit counts the cache writes for preset lists and settings."""
        return self._checked_count(self.u32())

    def text(self) -> str:
        try:
            return self.take(self.count()).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Malformed("a string is not UTF-8") from exc

    def texts(self) -> list[str]:
        return [self.text() for _ in range(self.count())]


def _float_text(value: float) -> str:
    """A double the way OrcaSlicer's profile JSON spells it (``0.2``, ``220``)."""
    if math.isnan(value):
        return _NIL
    if math.isinf(value):
        raise _Malformed("a setting is infinite")
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _percent_text(value: float) -> str:
    # A nil percent is plain "nil": OrcaSlicer reads "nil%" as 0%.
    return _NIL if math.isnan(value) else f"{_float_text(value)}%"


def _float_or_percent_text(cursor: _Cursor) -> str:
    value = cursor.f64()
    percent = cursor.u8()
    if math.isnan(value):
        return _NIL
    return f"{_float_text(value)}%" if percent else _float_text(value)


def _point_text(cursor: _Cursor) -> str:
    return f"{_float_text(cursor.f64())}x{_float_text(cursor.f64())}"


def _enum_texts(cursor: _Cursor, enums: list[str]) -> list[str]:
    out: list[str] = []
    for _ in range(cursor.count32()):
        index = cursor.u16()
        if index == 0:
            # No name for this value: the cache stores the raw integer instead.
            raw = cursor.i32()
            out.append(_NIL if raw == _INT_NIL else str(raw))
        elif index < len(enums):
            out.append(enums[index])
        else:
            raise _Malformed("an enum value is not in the dictionary")
    return out


def _read_value(cursor: _Cursor, type_code: int, enums: list[str]) -> Any:
    """One setting value, as the string or list of strings a profile JSON holds."""
    if type_code == _FLOAT:
        return _float_text(cursor.f64())
    if type_code == _FLOAT + _VECTOR:
        return [_float_text(cursor.f64()) for _ in range(cursor.count())]
    if type_code == _INT:
        return str(cursor.i32())
    if type_code == _INT + _VECTOR:
        ints = [cursor.i32() for _ in range(cursor.count())]
        return [_NIL if v == _INT_NIL else str(v) for v in ints]
    if type_code == _STRING:
        return cursor.text()
    if type_code == _STRING + _VECTOR:
        return cursor.texts()
    if type_code == _PERCENT:
        return _percent_text(cursor.f64())
    if type_code == _PERCENT + _VECTOR:
        return [_percent_text(cursor.f64()) for _ in range(cursor.count())]
    if type_code == _FLOAT_OR_PERCENT:
        return _float_or_percent_text(cursor)
    if type_code == _FLOAT_OR_PERCENT + _VECTOR:
        return [_float_or_percent_text(cursor) for _ in range(cursor.count())]
    if type_code == _POINT:
        return _point_text(cursor)
    if type_code == _POINT + _VECTOR:
        return [_point_text(cursor) for _ in range(cursor.count())]
    if type_code == _POINT3:
        return ",".join(_float_text(cursor.f64()) for _ in range(3))
    if type_code == _BOOL:
        return "1" if cursor.u8() else "0"
    if type_code == _BOOL + _VECTOR:
        return [_NIL if b == _BOOL_NIL else ("1" if b else "0") for b in cursor.take(cursor.count())]
    if type_code == _ENUM:
        values = _enum_texts(cursor, enums)
        if len(values) != 1:
            raise _Malformed("a single-value enum setting holds several values")
        return values[0]
    if type_code == _ENUM + _VECTOR:
        return _enum_texts(cursor, enums)
    if type_code == _POINTS_GROUPS:
        return [",".join(_point_text(cursor) for _ in range(cursor.count())) for _ in range(cursor.count())]
    if type_code == _INTS_GROUPS:
        return [",".join(str(cursor.i32()) for _ in range(cursor.count())) for _ in range(cursor.count())]
    raise _Malformed(f"unknown setting type {type_code}")


def _skip_vendor_profiles(cursor: _Cursor) -> None:
    """Step over the vendor's printer-model list; nothing in it is used here."""
    for _ in range(cursor.count()):
        cursor.text()  # map key
        for _ in range(5):  # name, id, config_version, config_update_url, changelog_url
            cursor.text()
        for _ in range(cursor.count()):  # models
            for _ in range(3):  # id, name, model_id
                cursor.text()
            cursor.u8()  # technology
            cursor.text()  # family
            cursor.texts()  # variants
            cursor.texts()  # default_materials
            cursor.texts()  # not_support_bed_types
            for _ in range(9):  # bed model/texture names and texture rectangles
                cursor.text()
        cursor.texts()  # default_filaments
        cursor.texts()  # default_sla_materials


def _read_presets(cursor: _Cursor, keys: list[str], types: list[int], enums: list[str]) -> tuple[PackedPreset, ...]:
    presets: list[PackedPreset] = []
    for _ in range(cursor.count32()):
        name = cursor.text()
        cursor.text()  # sub_path: where the JSON sat upstream; never used as a path here
        config: dict[str, Any] = {}
        for _ in range(cursor.count32()):
            index = cursor.u16()
            if index >= len(keys):
                raise _Malformed("a setting is not in the dictionary")
            config[keys[index]] = _read_value(cursor, types[index], enums)
        inherits = cursor.text()
        includes = tuple(cursor.texts())
        description = cursor.text()
        instantiation = cursor.text()
        setting_id = cursor.text()
        filament_id = cursor.text()
        cursor.texts()  # renamed_from
        presets.append(
            PackedPreset(
                name=name,
                config=config,
                inherits=inherits,
                includes=includes,
                instantiation=instantiation,
                setting_id=setting_id,
                filament_id=filament_id,
                description=description,
            )
        )
    return tuple(presets)


def _checked_body(data: bytes) -> tuple[bytes, int]:
    """Validate the header against the body; return ``(body, crc32)``."""
    if len(data) < _HEADER.size:
        raise _Malformed("the file is shorter than its header")
    magic, format_version, body_size, crc = _HEADER.unpack_from(data)
    if magic != _MAGIC:
        raise _Malformed("it is not an OrcaSlicer preset cache")
    if format_version != _FORMAT_VERSION:
        raise _Malformed(f"cache format {format_version}, plate reads format {_FORMAT_VERSION}")
    body = data[_HEADER.size :]
    if body_size != len(body):
        raise _Malformed("the size in the header does not match the file")
    if zlib.crc32(body) != crc:
        raise _Malformed("the checksum does not match")
    return body, crc


def _parse_body(body: bytes, crc: int) -> PackedProfiles:
    cursor = _Cursor(body)
    cache_version = cursor.u32()
    if cache_version != _CACHE_VERSION:
        raise _Malformed(f"preset cache version {cache_version}, plate reads version {_CACHE_VERSION}")
    vendor = cursor.text()
    vendor_version = cursor.text()
    keys = cursor.texts()
    types = [cursor.u16() for _ in range(cursor.count())]
    if len(types) != len(keys):
        raise _Malformed("the setting dictionary is inconsistent")
    enums = cursor.texts()
    _skip_vendor_profiles(cursor)
    presets = {kind: _read_presets(cursor, keys, types, enums) for kind in _KINDS}
    parse_errors = cursor.u64()
    if cursor.remaining():
        raise _Malformed("there is data after the last preset")
    if parse_errors:
        # OrcaSlicer built this cache from presets it could not all read, so a
        # parent or a G-code template may be missing from it.
        raise _Malformed(f"OrcaSlicer recorded {parse_errors} preset errors when it built the cache")
    return PackedProfiles(vendor=vendor, vendor_version=vendor_version, crc32=crc, body_size=len(body), presets=presets)


def _read_bytes(path: str) -> bytes:
    try:
        if os.path.getsize(path) > _MAX_CACHE_BYTES:
            raise _unsupported(path, "the file is far larger than a preset cache")
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise _unsupported(path, f"it could not be opened: {exc.strerror or exc}") from exc


def read_packed_profiles(path: str) -> PackedProfiles:
    """Read and fully validate a ``<vendor>.opc``; raise ``PackedProfilesError`` otherwise."""
    data = _read_bytes(path)
    try:
        body, crc = _checked_body(data)
        return _parse_body(body, crc)
    except _Malformed as exc:
        raise _unsupported(path, str(exc)) from exc


def _file_stem(name: str) -> str:
    """File name (without ``.json``) for a preset called *name*.

    Upstream writes ``Bambu Support For PA/PET @base`` to
    ``Bambu Support For PA PET @base.json``; the same rule is kept so the loose
    tree looks like the one OrcaSlicer 2.3.x shipped. Anything that could not be
    a plain file name is refused.
    """
    stem = name.replace("/", " ").replace("\\", " ")
    if not stem.strip() or stem.strip(". ") == "" or any(ord(ch) < 32 or ord(ch) == 127 for ch in stem):
        raise _Malformed(f"a preset has an unusable name: {name!r}")
    return stem


def _preset_document(kind: str, preset: PackedPreset) -> dict[str, Any]:
    """The loose-JSON form of one preset, parents and includes left as references."""
    document: dict[str, Any] = dict(preset.config)
    # Bookkeeping comes from the cache's own fields and wins over any setting
    # that happens to share the name.
    for key in ("inherits", "include"):
        document.pop(key, None)
    document["type"] = kind
    document["name"] = preset.name
    document["from"] = "system"
    document["instantiation"] = preset.instantiation
    for key, value in (
        ("setting_id", preset.setting_id),
        ("filament_id", preset.filament_id),
        ("description", preset.description),
    ):
        if value:
            document[key] = value
    if preset.inherits:
        document["inherits"] = preset.inherits
    if preset.includes:
        document["include"] = list(preset.includes)
    return document


def _write_tree(packed: PackedProfiles, root: str, source: str) -> None:
    for kind in _KINDS:
        kind_dir = os.path.join(root, kind)
        os.mkdir(kind_dir)
        written: set[str] = set()
        for preset in packed.presets.get(kind, ()):
            stem = _file_stem(preset.name)
            # Two names that differ only in case are one file on macOS and Windows.
            folded = stem.casefold()
            if folded in written:
                raise _Malformed(f"two {kind} presets share the file name {stem!r}")
            written.add(folded)
            with open(os.path.join(kind_dir, f"{stem}.json"), "w", encoding="utf-8") as handle:
                json.dump(_preset_document(kind, preset), handle)
    with open(os.path.join(root, _MARKER), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "source": source,
                "vendor": packed.vendor,
                "vendor_version": packed.vendor_version,
                "crc32": f"{packed.crc32:08x}",
                "layout": _UNPACK_LAYOUT,
                "counts": {kind: len(packed.presets.get(kind, ())) for kind in _KINDS},
            },
            handle,
        )


def default_cache_root() -> str:
    """Per-user directory that unpacked caches live under.

    ``PLATE_CACHE_DIR`` overrides it on every platform.
    """
    override = os.environ.get("PLATE_CACHE_DIR")
    if override:
        return os.path.join(_expand_path(override) or override, "orca-profiles")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join("~", "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join("~", "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join("~", ".cache")
    return os.path.join(_expand_path(base) or base, "platecli", "orca-profiles")


def packed_cache_path(profiles_dir: str | None) -> str | None:
    """The ``<vendor>.opc`` beside *profiles_dir* when that folder is a packed install.

    A packed install is a profiles folder with no ``machine/`` in it and a
    sibling file of the same name plus ``.opc``. A folder that has ``machine/``
    is a loose tree and is used as it is, cache or no cache.
    """
    if not profiles_dir:
        return None
    base = os.path.normpath(profiles_dir)
    if os.path.isdir(os.path.join(base, "machine")):
        return None
    candidate = f"{base}.opc"
    return candidate if os.path.isfile(candidate) else None


@lru_cache(maxsize=8)
def _unpacked_dir(cache_file: str, mtime_ns: int, size: int, cache_root: str) -> str:
    """Unpack *cache_file* under *cache_root* (once) and return the loose tree.

    ``mtime_ns`` and ``size`` only key the in-process memo; the directory is
    keyed by the checksum, which is verified against the body on every call.
    """
    del mtime_ns, size
    data = _read_bytes(cache_file)
    try:
        body, crc = _checked_body(data)
    except _Malformed as exc:
        raise _unsupported(cache_file, str(exc)) from exc
    vendor = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(os.path.basename(cache_file))[0]) or "vendor"
    dest = os.path.join(cache_root, f"{vendor}-{crc:08x}-{len(body)}-v{_UNPACK_LAYOUT}")
    if os.path.isfile(os.path.join(dest, _MARKER)):
        return dest
    try:
        packed = _parse_body(body, crc)
    except _Malformed as exc:
        raise _unsupported(cache_file, str(exc)) from exc
    staging = None
    try:
        os.makedirs(cache_root, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=".unpack-", dir=cache_root)
        _write_tree(packed, staging, cache_file)
        try:
            # The tree appears under its final name only when it is complete.
            os.rename(staging, dest)
            staging = None
        except OSError:
            # Another plate process finished the same unpack first.
            if not os.path.isfile(os.path.join(dest, _MARKER)):
                raise
    except _Malformed as exc:
        raise _unsupported(cache_file, str(exc)) from exc
    except OSError as exc:
        raise PackedProfilesError(
            f"Could not unpack OrcaSlicer's packed profiles {_display_path(cache_file)} into "
            f"{_display_path(cache_root)}: {exc.strerror or exc}. Set PLATE_CACHE_DIR to a folder you can write to.",
            extra={"packed_profiles": cache_file},
        ) from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    logger.debug(f"Unpacked OrcaSlicer profile cache {cache_file} to {dest}")
    return dest


def resolve_profiles_dir(profiles_dir: str, *, cache_root: str | None = None) -> str:
    """The folder that really holds ``machine/``, ``process/`` and ``filament/``.

    The one seam every profile lookup goes through. A loose tree (OrcaSlicer up
    to 2.4.x) comes back unchanged, and so does a folder that does not exist:
    callers report those as before. A packed install comes back as its unpacked
    copy. A cache this reader cannot read raises :class:`PackedProfilesError`;
    the lookup tables left in ``profiles/BBL/filament`` are never used instead.
    """
    cache_file = packed_cache_path(profiles_dir)
    if cache_file is None:
        return profiles_dir
    try:
        stat = os.stat(cache_file)
    except OSError as exc:
        raise _unsupported(cache_file, f"it could not be opened: {exc.strerror or exc}") from exc
    return _unpacked_dir(cache_file, stat.st_mtime_ns, stat.st_size, cache_root or default_cache_root())
