"""Hold G-code heater commands to the same limits slice overrides obey.

``plate gcode`` sends one line straight to the printer, and a pre-sliced 3MF
(downloaded, or exported by another slicer) carries G-code that platecli never
generated. Both must stay under the 350 °C nozzle / 150 °C bed ceilings that
``slice --set`` already enforces: a typo such as ``M104 S2200`` must not reach
the heater because it came in by another path.

Rank 10: stdlib and ``constants`` only.
"""

import re
import zipfile
import zlib

from bambu_cli.constants import MAX_BED_TEMP_C, MAX_NOZZLE_TEMP_C

__all__ = [
    "MAX_LINE_BYTES",
    "file_heater_problem",
    "heater_problem",
    "peak_heater_targets",
    "zip_member_heater_problem",
]

# One G-code word: a letter and the numeric text right after it.
_WORD = re.compile(r"([A-Za-z])[ \t]*([-+]?[0-9.]*)", re.ASCII)
_PLAIN_TEMPERATURE = re.compile(r"\+?[0-9]+(?:\.[0-9]+)?", re.ASCII)
# Heater commands and the limit their S (and Marlin's R) temperature must stay under.
_HEATERS = {
    104: ("nozzle", MAX_NOZZLE_TEMP_C),
    109: ("nozzle", MAX_NOZZLE_TEMP_C),
    140: ("bed", MAX_BED_TEMP_C),
    190: ("bed", MAX_BED_TEMP_C),
}
# A file line whose command is a heater command (after an optional line number).
# Firmware runs the first word of a line, so only those lines can set a heater.
_HEATER_LINE = re.compile(rb"[ \t]*(?:[Nn][0-9]+[ \t]*)?[Mm][ \t]*0*1(?:04|09|40|90)(?![0-9])")


def heater_problem(gcode):
    """Why ``gcode`` would set a heater past the limits, or None.

    Deliberately conservative: comments are scanned too, and a temperature
    that is not a plain decimal (``S2e3``, ``S0x3E8``, ``S250T0``) is refused
    because firmware parsers read those forms differently.
    """
    if not gcode.isascii():
        return "G-code must be plain ASCII."
    heater = None
    for match in _WORD.finditer(gcode):
        letter, value = match.group(1).upper(), match.group(2)
        # T is a parameter here, not a new command: "M104 T0 S3000" heats tool 0.
        if letter in ("G", "M"):
            heater = None
            if letter == "M" and _PLAIN_TEMPERATURE.fullmatch(value):
                heater = _HEATERS.get(int(float(value)))
            continue
        if heater is None or letter not in ("S", "R"):
            continue
        label, limit = heater
        end = match.end()
        if not _PLAIN_TEMPERATURE.fullmatch(value) or (end < len(gcode) and gcode[end].isalpha()):
            token = gcode[match.start() :].split(None, 1)[0].split(";", 1)[0]
            return f"Unreadable {label} temperature '{token}'; write it as a plain number such as {letter}220."
        if float(value) > limit:
            return f"{label.capitalize()} temperature {value} °C is above the {limit} °C limit."
    return None


# Firmware ends a line at LF, CR or CRLF. Files are read in bounded chunks so a
# member with no line breaks cannot pull gigabytes into memory.
_EOL = re.compile(rb"\r\n|\r|\n")
_CHUNK = 1 << 20
MAX_LINE_BYTES = 1 << 20
_READ_ERRORS = (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error)


class _LineTooLong(ValueError):
    pass


def _lines(fh):
    """Every line of the binary stream ``fh``; a line over MAX_LINE_BYTES raises _LineTooLong."""
    pending = b""
    count = 0
    while True:
        chunk = fh.read(_CHUNK)
        if not chunk:
            break
        data = pending + chunk
        # Hold back a trailing CR: the LF that would make it CRLF may start the next chunk.
        held = b"\r" if data.endswith(b"\r") else b""
        parts = _EOL.split(data[:-1] if held else data)
        pending = parts.pop() + held
        count += len(parts)
        yield from parts
        if len(pending) > MAX_LINE_BYTES:
            raise _LineTooLong(count + 1)
    if pending:
        yield from _EOL.split(pending)


def _heater_codes(fh):
    """``(line number, code)`` for every heater-command line: the part before ``;``.

    Comments in a sliced file are prose ("; set nozzle temperature"), not
    parameters; only the code before ";" reaches the firmware.
    """
    for number, raw in enumerate(_lines(fh), 1):
        if _HEATER_LINE.match(raw):
            yield number, raw.split(b";", 1)[0].decode("utf-8", errors="replace").strip()


def _lines_problem(fh, member):
    try:
        for number, code in _heater_codes(fh):
            problem = heater_problem(code)
            if problem:
                return f"{member} line {number}: {problem}"
    except _LineTooLong as exc:
        return f"{member} line {exc.args[0]} is over {MAX_LINE_BYTES >> 20} MiB, too long to check"
    return None


def _gcode_members(archive):
    # infolist, not namelist: a name can appear twice, and open(name) returns
    # only the last entry with it, so the first would never be read.
    return [info for info in archive.infolist() if info.filename.lower().endswith(".gcode")]


def _archive_problem(source):
    try:
        archive = zipfile.ZipFile(source)
    except _READ_ERRORS as exc:
        return f"could not open it as a 3MF to check its temperatures ({type(exc).__name__})"
    with archive:
        for info in _gcode_members(archive):
            try:
                with archive.open(info) as fh:
                    problem = _lines_problem(fh, info.filename)
            except _READ_ERRORS as exc:
                return f"could not read {info.filename} to check its temperatures ({type(exc).__name__})"
            if problem:
                return problem
    return None


def file_heater_problem(path):
    """Why the G-code in ``path`` (a .gcode file or a sliced 3MF) breaks the limits, or None.

    Every ``.gcode`` entry of a 3MF is checked, not only the plate about to be
    printed: the plate can be chosen later, and a file on the printer can be
    started from its own screen. Anything that cannot be read is refused, since
    it cannot be checked.
    """
    name = str(path).lower()
    if name.endswith(".gcode"):
        try:
            with open(path, "rb") as fh:
                return _lines_problem(fh, _basename(path))
        except OSError as exc:
            return f"could not read it to check its temperatures ({type(exc).__name__})"
    if name.endswith(".3mf"):
        return _archive_problem(path)
    return None


def zip_member_heater_problem(archive_path, info):
    """``file_heater_problem`` for the ZIP entry ``info`` without extracting it (job --dry-run)."""
    name = info.filename.lower()
    if not name.endswith((".gcode", ".3mf")):
        return None
    try:
        with zipfile.ZipFile(archive_path) as outer, outer.open(info) as fh:
            if name.endswith(".gcode"):
                return _lines_problem(fh, _basename(info.filename))
            return _archive_problem(fh)
    except _READ_ERRORS as exc:
        return f"could not read {info.filename} to check its temperatures ({type(exc).__name__})"


def _targets(code):
    heater = None
    for match in _WORD.finditer(code):
        letter, value = match.group(1).upper(), match.group(2)
        if letter in ("G", "M"):
            heater = None
            if letter == "M" and _PLAIN_TEMPERATURE.fullmatch(value):
                heater = _HEATERS.get(int(float(value)))
        elif heater is not None and letter in ("S", "R") and _PLAIN_TEMPERATURE.fullmatch(value):
            yield heater[0], float(value)


def _peaks_in(fh, peaks):
    for _number, code in _heater_codes(fh):
        for label, value in _targets(code):
            peaks[label] = max(value, peaks.get(label, value))


def peak_heater_targets(path):
    """The highest nozzle and bed targets ``path``'s G-code sets: ``{"nozzle": 220.0, ...}``.

    For a confirmation screen, so the person approving a print sees what the
    file will actually heat to. ``{}`` when nothing is set; None when the
    G-code cannot be read.
    """
    peaks: dict[str, float] = {}
    name = str(path).lower()
    try:
        if name.endswith(".gcode"):
            with open(path, "rb") as fh:
                _peaks_in(fh, peaks)
        elif name.endswith(".3mf"):
            with zipfile.ZipFile(path) as archive:
                for info in _gcode_members(archive):
                    with archive.open(info) as fh:
                        _peaks_in(fh, peaks)
    except (*_READ_ERRORS, ValueError):
        return None
    return peaks


def _basename(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]
