"""Hermetic fake OrcaSlicer harness (roadmap C.4).

Provides:

* ``orca_stub.py`` — a standalone fake-slicer script (invoked as the binary).
* ``make_orca_launcher`` — write a cross-platform launcher for that script so
  ``settings.orca_slicer`` can point at a single directly-executable path that
  ``subprocess.Popen`` can run on Linux, macOS, and Windows (no shebang/exec-bit
  reliance on Windows).
* ``build_profiles_dir`` — materialise a real OrcaSlicer-style ``profiles_dir``
  (machine/process/filament JSONs) under a tmp dir so ``cmd_slice`` finds real
  files instead of mocking ``os.path.exists``/``os.listdir``.
* ``write_stl`` — write a tiny real ASCII STL model input.
"""

from __future__ import annotations

import json
import os
import stat
import sys

STUB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orca_stub.py")


def make_orca_launcher(dest_dir: str, name: str = "orca-slicer") -> str:
    """Write a launcher for ``orca_stub.py`` and return its path.

    The launcher is what ``settings.orca_slicer`` points at. ``cmd_slice`` runs
    it via ``subprocess.Popen([launcher, ...args])`` and
    ``_slicer_executable_problem`` checks it exists + is X_OK on POSIX, so the
    launcher must be a single directly-executable file on every OS:

    * POSIX: a ``sh`` wrapper that ``exec``s ``<python> orca_stub.py "$@"``,
      chmod +x. (The X_OK check itself requires this on POSIX.)
    * Windows: a ``.cmd`` batch file ``@<python> orca_stub.py %*`` — Popen can
      launch a ``.cmd`` directly; there is no exec bit to set.

    Both embed the current interpreter (``sys.executable``) so no shebang or
    ``PATH`` python lookup is needed.
    """
    python = sys.executable
    if os.name == "nt":
        launcher = os.path.join(dest_dir, name + ".cmd")
        # %* forwards all args; quote python + script for spaced paths.
        content = f'@"{python}" "{STUB_PATH}" %*\r\n'
        with open(launcher, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return launcher

    launcher = os.path.join(dest_dir, name)
    content = f'#!/bin/sh\nexec "{python}" "{STUB_PATH}" "$@"\n'
    with open(launcher, "w", encoding="utf-8") as fh:
        fh.write(content)
    mode = os.stat(launcher).st_mode
    os.chmod(launcher, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


# ---- Minimal but realistic profile JSONs ------------------------------------
# cmd_slice looks for, in ``profiles_dir``:
#   machine/<full_model_name> <nozzle> nozzle.json  (default P1P 0.4)
#   process/<layer> Standard @BBL P1P.json          (quality-derived name)
#   filament/*<requested>*@base*.json               (fuzzy match, default PLA Basic)
# The names below match the default P1P / 0.4 / standard / PLA Basic path.

_MACHINE_JSON = {
    "type": "machine",
    "name": "Bambu Lab P1P 0.4 nozzle",
    "nozzle_diameter": ["0.4"],
    "printer_model": "Bambu Lab P1P",
}

_PROCESS_JSON = {
    "type": "process",
    "name": "0.20mm Standard @BBL P1P",
    "layer_height": "0.2",
    "compatible_printers": ["Bambu Lab P1P 0.4 nozzle"],
}

_FILAMENT_JSON = {
    "type": "filament",
    "name": "Bambu PLA Basic @base",
    "filament_type": ["PLA"],
}


def build_profiles_dir(root: str) -> str:
    """Create a real profiles_dir tree under *root* and return its path.

    Matches the default slice path (P1P, 0.4 nozzle, standard quality, PLA
    Basic) so ``cmd_slice`` resolves every profile from real files on disk.
    """
    profiles_dir = os.path.join(root, "profiles")
    for sub, name, payload in (
        ("machine", "Bambu Lab P1P 0.4 nozzle.json", _MACHINE_JSON),
        ("process", "0.20mm Standard @BBL P1P.json", _PROCESS_JSON),
        ("filament", "Bambu PLA Basic @base.json", _FILAMENT_JSON),
    ):
        d = os.path.join(profiles_dir, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    return profiles_dir


# A minimal valid ASCII STL (single degenerate triangle) — enough for the stub
# to receive a real model input path; the stub never actually parses it.
_STL = """solid cube
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 1 0
  endloop
endfacet
endsolid cube
"""


def write_stl(path: str) -> str:
    """Write a tiny real ASCII STL to *path* and return it."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_STL)
    return path


__all__ = [
    "STUB_PATH",
    "make_orca_launcher",
    "build_profiles_dir",
    "write_stl",
]
