#!/usr/bin/env python3
"""Hermetic fake OrcaSlicer CLI (roadmap C.4).

This script stands in for the real ``orca-slicer`` binary so slice tests can
exercise the REAL subprocess-invocation, stdout-pump, and output-parsing code in
``bambu_cli/slicer/`` (``orca._run_orcaslicer`` and ``output._finalize_slice``)
instead of mocking ``subprocess.Popen`` and ``os.path.exists``.

It reproduces only the narrow slice of the OrcaSlicer CLI surface our code drives
(see ``bambu_cli/slicer/orca._build_orcaslicer_cmd``)::

    orca --load-settings "<machine>;<process>" \
         --load-filaments <filament> \
         --slice 0 --export-3mf <outfile> --outputdir <outdir> \
         [--arrange 1] [--threads N] <model> [<model> ...]

Behaviour is selected via the ``ORCA_STUB_SCENARIO`` environment variable so a
test can point ``settings.orca_slicer`` at a generated launcher for this script
and choose a scenario without patching internals:

    success          write a plausible, valid sliced .3mf; exit 0 (default)
    benign_gl        write a valid .3mf but emit GL/thumbnail noise on stderr and
                     exit non-zero (the "headless GL failed but slice is fine"
                     path ``_finalize_slice`` treats as benign)
    empty_output     write a zero-byte output file; exit 0
    corrupt_output   write non-zip garbage to the output path; exit 0
    missing_output   write nothing; exit 0 (parser sees no output file)
    fail             emit an ``[error]`` line and exit non-zero; write nothing
    fail_real_gl     emit GL noise AND a real "slicing error" line, exit non-zero,
                     write a valid .3mf (must NOT be treated as benign)
    garbage_stdout   print unparseable progress-ish noise, still succeed
    hang             sleep longer than the test's timeout (write nothing)
    progress         print several ``NN%`` / "slicing" lines then succeed
                     (drives the stdout progress-parsing branch)

Extra knobs (all optional):
    ORCA_STUB_SLEEP    float seconds to sleep before finishing (default 0)
    ORCA_STUB_EXIT     override the exit code
    ORCA_STUB_STDOUT   extra literal text to print to stdout
    ORCA_STUB_STDERR   extra literal text to print to stderr
    ORCA_STUB_MARKER   if set, a file at this path is appended to when the stub
                       runs (lets a test assert the real binary was invoked)

The stub never touches anything outside the ``--outputdir`` it is told to use
(plus the optional marker file), so tests stay confined to ``tmp_path``.
"""

from __future__ import annotations

import os
import sys
import time
import zipfile

# ---- Minimal valid sliced-3MF payload ---------------------------------------
# ``bambu_cli.slicer.output._is_valid_sliced_3mf`` requires a non-corrupt OPC zip
# containing ``[Content_Types].xml`` plus either ``3D/3dmodel.model`` or a
# ``Metadata/plate_*.gcode`` plate. We ship both so the fixture resembles a real
# Bambu/Orca export.

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)

_MODEL_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
    "<resources></resources><build></build></model>"
)

_PLATE_GCODE = "; sliced by orca_stub (fake OrcaSlicer)\nG28\nG1 Z0.2 F600\n"


def _write_valid_3mf(path: str) -> None:
    """Write a small but structurally valid sliced .3mf zip package."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("3D/3dmodel.model", _MODEL_XML)
        zf.writestr("Metadata/plate_1.gcode", _PLATE_GCODE)


def _resolve_output_path(argv: list[str]) -> str | None:
    """Recover the sliced-output path the way the real CLI would use it.

    Mirrors ``_build_orcaslicer_cmd``: ``--export-3mf <outfile>`` (a bare
    filename) resolved against ``--outputdir <outdir>``.
    """
    outfile = None
    outdir = None
    for i, tok in enumerate(argv):
        if tok == "--export-3mf" and i + 1 < len(argv):
            outfile = argv[i + 1]
        elif tok == "--outputdir" and i + 1 < len(argv):
            outdir = argv[i + 1]
    if outfile is None:
        return None
    if outdir:
        return os.path.join(outdir, outfile)
    return outfile


def main(argv: list[str]) -> int:
    scenario = os.environ.get("ORCA_STUB_SCENARIO", "success")

    marker = os.environ.get("ORCA_STUB_MARKER")
    if marker:
        # Record that the real binary path was invoked (used by tests to assert
        # the launcher/DI seam actually reached this script).
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write(scenario + "\n")

    outpath = _resolve_output_path(argv)

    # A hang must block regardless of the sleep knob so timeout handling in
    # ``cmd_slice`` (subprocess.TimeoutExpired -> EXIT_TIMEOUT) is exercised.
    if scenario == "hang":
        time.sleep(float(os.environ.get("ORCA_STUB_SLEEP", "3600")))
        return 0

    if scenario == "spawn_child_then_hang":
        # Model xvfb-run's behaviour: spawn a long-lived child (stand-in for the
        # backgrounded Xvfb + OrcaSlicer) then block. The child records its PID so
        # a test can assert the timeout kill reaped the WHOLE process group (child
        # included), not just this top process. Requires start_new_session so the
        # child shares our group.
        import subprocess as _sp

        child_pid_file = os.environ.get("ORCA_STUB_CHILD_PIDFILE")
        sleep_s = os.environ.get("ORCA_STUB_SLEEP", "3600")
        child = _sp.Popen([sys.executable, "-c", f"import time; time.sleep({float(sleep_s)})"])
        if child_pid_file:
            with open(child_pid_file, "w", encoding="utf-8") as fh:
                fh.write(str(child.pid))
        time.sleep(float(sleep_s))
        return 0

    sleep_s = float(os.environ.get("ORCA_STUB_SLEEP", "0"))
    if sleep_s > 0:
        time.sleep(sleep_s)

    stdout = sys.stdout
    stderr = sys.stderr

    if scenario == "success":
        print("Loading configuration...", file=stdout)
        print("slicing plate 1", file=stdout)
        print("100%", file=stdout)
        if outpath:
            _write_valid_3mf(outpath)
        rc = 0

    elif scenario == "progress":
        for pct in (0, 17, 42, 73, 99):
            print(f"exporting {pct}% ...", file=stdout)
        print("slicing complete", file=stdout)
        if outpath:
            _write_valid_3mf(outpath)
        rc = 0

    elif scenario == "garbage_stdout":
        # Non-progress noise that must not crash the stdout line handler.
        print("\x00\x01 not-a-percentage ~~~ \r\r partial", file=stdout)
        print("gibberish line without newline", file=stdout, end="")
        if outpath:
            _write_valid_3mf(outpath)
        rc = 0

    elif scenario == "benign_gl":
        # OrcaSlicer wrote a valid .3mf but the headless GL/thumbnail step failed
        # and it exited non-zero. ``_finalize_slice`` should treat this as success.
        if outpath:
            _write_valid_3mf(outpath)
        print("slicing plate 1", file=stdout)
        print("[GLFW] init OpenGL failed; skip thumbnail generation", file=stderr)
        print("glew: no usable GL context", file=stderr)
        rc = 1

    elif scenario == "fail_real_gl":
        # GL noise present but so is a real slicing error: must NOT be benign.
        if outpath:
            _write_valid_3mf(outpath)
        print("[GLFW] init opengl failed", file=stderr)
        print("[error] slicing error: model exceeds build volume", file=stderr)
        rc = 1

    elif scenario == "benign_gl_no_write":
        # GL/thumbnail noise + non-zero exit but NO new output written. If a stale
        # valid .3mf already sits at outpath, _finalize_slice must NOT accept it
        # as this run's output (the stale-output guard).
        print("slicing plate 1", file=stdout)
        print("[GLFW] init OpenGL failed; skip thumbnail generation", file=stderr)
        rc = 1

    elif scenario == "empty_output":
        if outpath:
            open(outpath, "w").close()  # zero bytes
        print("done", file=stdout)
        rc = 0

    elif scenario == "corrupt_output":
        if outpath:
            with open(outpath, "wb") as fh:
                fh.write(b"this is not a zip file, just garbage bytes" * 8)
        print("done", file=stdout)
        rc = 0

    elif scenario == "missing_output":
        # Report success but never write the file.
        print("slicing plate 1", file=stdout)
        print("100%", file=stdout)
        rc = 0

    elif scenario == "fail":
        print("Loading configuration...", file=stdout)
        print("[error] nothing to be sliced, please check your model", file=stderr)
        rc = 1

    else:
        print(f"orca_stub: unknown scenario {scenario!r}", file=stderr)
        rc = 2

    extra_out = os.environ.get("ORCA_STUB_STDOUT")
    if extra_out:
        print(extra_out, file=stdout)
    extra_err = os.environ.get("ORCA_STUB_STDERR")
    if extra_err:
        print(extra_err, file=stderr)

    override = os.environ.get("ORCA_STUB_EXIT")
    if override is not None:
        rc = int(override)

    stdout.flush()
    stderr.flush()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
