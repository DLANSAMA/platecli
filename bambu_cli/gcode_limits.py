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

__all__ = ["file_heater_problem", "heater_problem", "peak_heater_targets"]

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


def _lines_problem(lines, member):
    for number, raw in enumerate(lines, 1):
        if not _HEATER_LINE.match(raw):
            continue
        # Comments in a sliced file are prose ("; set nozzle temperature"), not
        # parameters; only the code before ";" reaches the firmware.
        code = raw.split(b";", 1)[0].decode("utf-8", errors="replace").strip()
        problem = heater_problem(code)
        if problem:
            return f"{member} line {number}: {problem}"
    return None


def file_heater_problem(path):
    """Why the G-code in ``path`` (a .gcode file or a sliced 3MF) breaks the limits, or None.

    Every ``.gcode`` member of a 3MF is checked, not only the plate about to be
    printed: the plate can be chosen later, and a file on the printer can be
    started from its own screen. A 3MF that cannot be opened is left to the
    other checks; this one only reads G-code.
    """
    name = str(path).lower()
    if name.endswith(".gcode"):
        try:
            with open(path, "rb") as fh:
                return _lines_problem(fh, _basename(path))
        except OSError as exc:
            return f"could not read it to check its temperatures ({type(exc).__name__})"
    if not name.endswith(".3mf"):
        return None
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None
    with archive:
        for member in archive.namelist():
            if not member.lower().endswith(".gcode"):
                continue
            try:
                with archive.open(member) as fh:
                    problem = _lines_problem(fh, member)
            except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as exc:
                # Fail closed: G-code that cannot be read here cannot be checked.
                return f"could not read {member} to check its temperatures ({type(exc).__name__})"
            if problem:
                return problem
    return None


def _heater_codes(path):
    """The code part of every heater-command line in ``path``'s G-code."""
    name = str(path).lower()
    if name.endswith(".gcode"):
        with open(path, "rb") as fh:
            yield from _codes(fh)
    elif name.endswith(".3mf"):
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if member.lower().endswith(".gcode"):
                    with archive.open(member) as fh:
                        yield from _codes(fh)


def _codes(lines):
    for raw in lines:
        if _HEATER_LINE.match(raw):
            yield raw.split(b";", 1)[0].decode("utf-8", errors="replace").strip()


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


def peak_heater_targets(path):
    """The highest nozzle and bed targets ``path``'s G-code sets: ``{"nozzle": 220.0, ...}``.

    For a confirmation screen, so the person approving a print sees what the
    file will actually heat to. ``{}`` when nothing is set or it cannot be read.
    """
    peaks: dict[str, float] = {}
    try:
        for code in _heater_codes(path):
            for label, value in _targets(code):
                peaks[label] = max(value, peaks.get(label, value))
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error):
        return {}
    return peaks


def _basename(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]
