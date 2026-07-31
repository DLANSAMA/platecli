"""Front-end-agnostic core shared by the ``plate go`` wizard and ``plate tui``.

Everything here is plain synchronous Python over plain data — no prompts, no
Textual, no printing. Both front-ends (the linear wizard in ``session.py`` and
the Textual screens in ``bambu_cli/tui/``) import these helpers instead of
duplicating the pipeline logic, so a fix lands in one place (plan §4.2).

Nothing in this module talks to the terminal: it validates a source, reads the
AMS, sequences download → extract → slice, builds the ``job`` namespace, and
computes the preview rows. Terminal conditions raise ``BambuError`` via
``abort`` — domain code never terminates the process itself (that lives only in
``cli.py``; CI greps for it).
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from bambu_cli import utils
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    EXIT_FILE_ERROR,
    PRINT_READY_EXTENSIONS,
    SLICEABLE_EXTENSIONS,
)
from bambu_cli.errors import BambuError, abort
from bambu_cli.interactive.presets import parse_args_or_abort, preset_to_job_args
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.slicer.estimate import format_estimate, read_3mf_estimate

MATERIAL_CHOICES = ["PLA", "PETG", "ABS", "TPU"]
MATERIAL_GUIDANCE = {
    "PLA": "easy, rigid, most models",
    "PETG": "tougher, slightly stringy",
    "ABS": "strong, needs an enclosure",
    "TPU": "flexible; print slow",
}
QUALITY_CHOICES = ["draft", "standard", "fine"]
QUALITY_GUIDANCE = {
    "draft": "fastest, visible layers",
    "standard": "the right default",
    "fine": "slowest, smoothest",
}
QUALITY_LAYER_HEIGHTS = {"draft": "0.28mm", "standard": "0.20mm", "fine": "0.12mm"}

SUPPORTED_SOURCE_EXTS = SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS

DETECTED_AMS_TAG = "(detected in AMS)"
PRESLICED_MATERIAL_LINE = "(pre-sliced — material settings not applied)"


@dataclass
class WizardState:
    """Plain state carried between wizard steps (and between TUI screens)."""

    source: str | None = None
    material: str = "PLA"
    quality: str = "standard"
    supports: bool = False
    workdir: str | None = None
    printable_path: str | None = None
    kept_path: str | None = None
    sliced: bool = False
    # The material the wizard detected loaded in the AMS (None when detection
    # failed / no AMS / external spool). Used to decide use_ams at print time.
    detected_ams_material: str | None = None
    # The absolute AMS slot index (unit*4+slot) of the active tray the material
    # was detected from, or None. Used as the ams_mapping when feeding from AMS.
    detected_ams_slot: int | None = None


# ---------------------------------------------------------------------------
# Injectable pipeline collaborators
# ---------------------------------------------------------------------------


def _default_setup() -> Callable[..., Any]:
    from bambu_cli.commands import cmd_setup

    return cmd_setup


def _default_download() -> Callable[..., Any]:
    from bambu_cli.commands import cmd_download

    return cmd_download


def _default_slice() -> Callable[..., Any]:
    from bambu_cli.commands import cmd_slice

    return cmd_slice


def _default_job() -> Callable[..., Any]:
    from bambu_cli.commands import cmd_job

    return cmd_job


def _default_ams_material() -> Callable[..., Any]:
    return read_loaded_ams_material


@dataclass
class GoSteps:
    """Injectable pipeline collaborators for the wizard (mirrors ``JobSteps``)."""

    setup: Callable[..., Any] | None = None
    download: Callable[..., Any] | None = None
    slice: Callable[..., Any] | None = None
    job: Callable[..., Any] | None = None
    ams_material: Callable[..., Any] | None = None

    def _resolve(
        self, value: Callable[..., Any] | None, factory: Callable[[], Callable[..., Any]]
    ) -> Callable[..., Any]:
        return value if value is not None else factory()

    def get_setup(self) -> Callable[..., Any]:
        return self._resolve(self.setup, _default_setup)

    def get_download(self) -> Callable[..., Any]:
        return self._resolve(self.download, _default_download)

    def get_slice(self) -> Callable[..., Any]:
        return self._resolve(self.slice, _default_slice)

    def get_job(self) -> Callable[..., Any]:
        return self._resolve(self.job, _default_job)

    def get_ams_material(self) -> Callable[..., Any]:
        return self._resolve(self.ams_material, _default_ams_material)


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


def validate_source(raw: str) -> tuple[str | None, str | None]:
    """Return ``(normalized_source, error)`` — exactly one is non-None."""
    from bambu_cli.download import _is_http_url, _looks_like_url, _normalize_url_input, _validate_http_url_or_exit
    from bambu_cli.download.naming import _file_extension
    from bambu_cli.slicer import _is_directory_input

    value = (raw or "").strip()
    if not value:
        return None, "Please enter a URL or file path."

    source = _normalize_url_input(value)

    # A leading dash would be parsed as an option flag by the downstream job
    # parser (mirrors _run_job's guard at orchestrate.py). Reject it up front so
    # a file literally named "-foo.stl" cannot detonate argparse mid-wizard.
    if source.startswith("-"):
        return None, f"Source cannot start with '-': {source}"

    if _looks_like_url(source) or _is_http_url(source):
        try:
            _validate_http_url_or_exit(source)
        except BambuError as exc:
            return None, str(exc) or "That URL is not valid."
        return source, None

    # Local path.
    path = _expand_path(source)
    if not os.path.exists(path):
        return None, f"File not found: {source}"
    if _is_directory_input(path):
        return None, "That is a directory; point at a model file."
    ext = _file_extension(path)
    if ext not in SUPPORTED_SOURCE_EXTS:
        supported = ", ".join(SUPPORTED_SOURCE_EXTS)
        return None, f"Unsupported file type '{ext or 'none'}'. Supported: {supported}"
    return source, None


# ---------------------------------------------------------------------------
# AMS detection
# ---------------------------------------------------------------------------


def read_loaded_ams_material(args: argparse.Namespace, on_active_slot=None) -> str | None:
    """Best-effort read of the currently-loaded AMS filament type.

    Returns a ``MATERIAL_PRESETS`` key (e.g. ``"PLA"``) matching the active AMS
    tray's material, or ``None`` on ANY failure — MQTT/timeout error, no AMS,
    empty active slot, or an unknown material. NEVER raises and never invents a
    timeout: it relies on the existing ``printer.status()`` machinery (which
    carries its own ``mqtt_timeout``) so the wizard is not perceptibly slowed.
    """
    try:
        from bambu_cli.ams import parse_ams
        from bambu_cli.context import RuntimeContext

        ctx = RuntimeContext.for_request(args)
        data = ctx.printer().status()
        if not data:
            return None
        ams = parse_ams(data)
        if not ams or not ams.get("units"):
            return None
        active = ams.get("active_tray")
        loaded_type: str | None = None
        if active is not None:
            # An active slot is reported: trust ONLY the tray marked active, and
            # scan ALL units to find it (the active tray may be in a later unit
            # than a non-active spool in an earlier unit — never let an earlier
            # unit's fallback shadow the real active tray). The active tray's
            # absolute slot index equals ``active`` (parse_ams sets it that way),
            # so record it for the ams_mapping used when feeding from the AMS.
            for unit in ams["units"]:
                for tray in unit.get("trays", []):
                    if tray.get("empty"):
                        continue
                    if tray.get("active"):
                        loaded_type = tray.get("type")
                        if on_active_slot is not None:
                            on_active_slot(active)
                        break
                if loaded_type is not None:
                    break
        else:
            # Nothing is marked active (no tray_now, or an external-spool
            # sentinel): fall back to the first non-empty tray. Do NOT record a
            # slot — without a firm active tray we won't feed from the AMS.
            for unit in ams["units"]:
                for tray in unit.get("trays", []):
                    if tray.get("empty"):
                        continue
                    loaded_type = tray.get("type")
                    break
                if loaded_type is not None:
                    break
        return match_material_preset(loaded_type)
    except Exception:
        # AMS detection is a nicety — never let it block or break the wizard.
        return None


def match_material_preset(loaded_type: str | None) -> str | None:
    """Map a reported AMS filament type onto a MATERIAL_PRESETS key, or None."""
    if not loaded_type:
        return None
    normalized = str(loaded_type).strip().upper()
    for key in MATERIAL_CHOICES:
        if normalized == key:
            return key
    return None


def detect_ams_material(detector: Callable[..., Any], args: argparse.Namespace) -> tuple[str | None, int | None]:
    """Run an AMS detector and return ``(material, active_slot)``.

    ``material`` is None unless the detector reported one of ``MATERIAL_CHOICES``;
    ``active_slot`` is None unless a material was detected from a firm active
    tray. The real detector accepts an ``on_active_slot`` callback so we can
    capture the active AMS slot for the ams_mapping; injected test seams take
    only ``args``, so fall back to the single-arg call for them.
    """
    slot_holder: dict[str, int] = {}

    def _record_slot(slot: int) -> None:
        slot_holder["slot"] = slot

    try:
        detected = detector(args, _record_slot)
    except TypeError:
        detected = detector(args)

    material = detected if detected in MATERIAL_CHOICES else None
    slot = slot_holder.get("slot") if material is not None else None
    return material, slot


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def slicer_preflight_problem(settings: Any) -> str | None:
    """Return a message describing an unusable slicer/profiles setup, or None.

    Shared by the wizard's step 0 and the TUI's pre-form preflight: the checks
    that do not need a prompt (OrcaSlicer executable, profiles directory).
    """
    from bambu_cli.slicer.profiles import _profiles_dir_diagnostic, _slicer_executable_problem

    problem = _slicer_executable_problem(settings.orca_slicer)
    if problem:
        return problem

    if not os.path.isdir(os.path.join(settings.profiles_dir, "process")):
        hint, _detected = _profiles_dir_diagnostic(settings.profiles_dir)
        return hint or (
            f"OrcaSlicer profiles not found under {settings.profiles_dir}. "
            "Run 'plate setup' to configure a profiles directory."
        )
    return None


def preflight_problem(args: argparse.Namespace) -> str | None:
    """Return the first blocking configuration problem, or None when ready.

    The non-interactive form of the wizard's step 0, for front-ends that cannot
    prompt mid-check (the TUI points the user at ``plate setup`` instead of
    embedding it).
    """
    from bambu_cli.context import current_settings

    settings = current_settings()
    if settings.printer_ip == "0.0.0.0" and not bool(getattr(args, "sim", False)):
        return "No printer configured yet. Run 'plate setup', then start the TUI again."
    return slicer_preflight_problem(settings)


# ---------------------------------------------------------------------------
# Prepare pipeline: download → extract → slice
# ---------------------------------------------------------------------------


def make_workdir(prefix: str = "bambu-go-") -> str:
    """Create the temp directory a prepare run owns (deleted by cleanup_workdir)."""
    return tempfile.mkdtemp(prefix=prefix)


def run_prepare_pipeline(steps: Any, state: WizardState, workdir: str) -> str:
    """Download (if needed), extract a zip member (if needed), then slice.

    Mutates ``state.printable_path`` / ``state.sliced`` and returns the path of
    the *model* the printable came from (the preview names that file). Raises
    ``BambuError`` through ``abort`` on any unusable intermediate result.
    """
    from bambu_cli.download import _extract_zip_model, _is_http_url
    from bambu_cli.download.naming import _file_extension
    from bambu_cli.job.predict import _slice_args_for_job

    assert state.source is not None  # set by the source step  # noqa: S101
    source = state.source

    if _is_http_url(source):
        utils._LAST_ERROR_PAYLOAD = None
        utils._LAST_DOWNLOAD_PAYLOAD = None
        model_path = steps.get_download()(
            argparse.Namespace(
                url=source,
                output=workdir,
                name=None,
                max_download_mb=DEFAULT_MAX_DOWNLOAD_MB,
                json=False,
                progress=True,
            )
        )
    else:
        model_path = _expand_path(source)

    if not model_path or not isinstance(model_path, str):
        abort(
            "Download did not produce a usable model file.",
            exit_code=EXIT_FILE_ERROR,
            failed_step="download",
        )

    # A local .zip never went through the download layer's extraction, so extract
    # its model member the same way _run_job does — reusing _extract_zip_model —
    # so the extracted model flows through the normal slice path below with the
    # user's preset namespace. Without this the raw .zip would fall to the
    # "printer-ready" branch and later be sliced by cmd_job at PLA defaults,
    # silently discarding the chosen material (see plan §11 Q3).
    if _file_extension(model_path) in ARCHIVE_DOWNLOAD_EXTENSIONS:
        utils._LAST_ERROR_PAYLOAD = None
        try:
            extracted_path, _filename, _entry, _size = _extract_zip_model(
                model_path,
                workdir,
                argparse.Namespace(name=None, max_download_mb=DEFAULT_MAX_DOWNLOAD_MB),
            )
        except ValueError as exc:
            abort(str(exc), exit_code=EXIT_FILE_ERROR, failed_step="extract")
        model_path = extracted_path

    ext = _file_extension(model_path)
    preset_ns = preset_to_job_args(state.material, state.quality, state.supports, source)

    if ext in SLICEABLE_EXTENSIONS:
        utils._LAST_ERROR_PAYLOAD = None
        try:
            printable_path = steps.get_slice()(_slice_args_for_job(model_path, preset_ns, workdir))
        except BambuError as exc:
            if not exc.next_command:
                exc.next_command = ["slice", model_path, "-v"]
            raise
        state.printable_path = printable_path
        state.sliced = True
    else:
        # .3mf / .gcode: already printer-ready, no slicing. The preset material
        # was NOT applied — the preview flags this so we don't imply otherwise.
        state.printable_path = model_path
        state.sliced = False

    return model_path


def preview_rows(state: WizardState, model_path: str) -> list[tuple[str, str]]:
    """Return the ``(label, value)`` rows of the pre-print preview.

    Pure formatting over the state plus the current settings — both front-ends
    render the same four rows, including the pre-sliced caveat that stops us
    implying the chosen material was applied to a file we did not slice.
    """
    from bambu_cli.config import MODEL_MAPPING
    from bambu_cli.context import current_settings
    from bambu_cli.download.naming import _file_extension, _portable_basename

    settings = current_settings()
    model_info = MODEL_MAPPING.get(settings.printer_model, {})
    full_name = model_info.get("full_name", settings.printer_model or "printer")
    quality_layer = QUALITY_LAYER_HEIGHTS.get(state.quality, "")

    name = _portable_basename(model_path)
    ext = _file_extension(model_path)

    if ext == ".gcode":
        estimate_line = "estimate unavailable (pre-sliced file)"
    else:
        assert state.printable_path is not None  # noqa: S101
        est = read_3mf_estimate(state.printable_path)
        if est.seconds is None and est.grams is None:
            estimate_line = "Couldn't read a time estimate from the sliced file"
        else:
            estimate_line = format_estimate(est)

    supports_label = "yes" if state.supports else "no"
    if state.sliced:
        material_line = (
            f"{state.material}  ·  Quality: {state.quality} ({quality_layer})  ·  Supports: {supports_label}"
        )
    else:
        material_line = PRESLICED_MATERIAL_LINE

    return [
        ("Model", name),
        ("Printer", f"{full_name}, {settings.nozzle_size}mm nozzle"),
        ("Material", material_line),
        ("Estimate", estimate_line),
    ]


# ---------------------------------------------------------------------------
# Job namespace
# ---------------------------------------------------------------------------


def build_job_namespace(state: WizardState, args: argparse.Namespace, *, confirm: bool) -> argparse.Namespace:
    """Build the ``job`` namespace for the prepared printable file.

    ``confirm=True`` is what actually starts a print, so only an explicit
    user confirmation path passes it.
    """
    from bambu_cli.cli import build_parser

    assert state.printable_path is not None  # noqa: S101
    parser = build_parser()
    # parse_args raises SystemExit(2) on failure; parse_args_or_abort converts
    # that to a BambuError so the wizard never bypasses the abort/BambuError
    # contract (domain code must not terminate the process). The path here is a
    # slicer-produced or already-validated file, so a failure would be an
    # internal bug, but we surface it cleanly regardless.
    job_ns = parse_args_or_abort(parser, ["job", state.printable_path])
    job_ns.confirm = confirm
    # Feed from the AMS only when the wizard detected an AMS-loaded filament with
    # a known active slot AND the user kept that detected material. In that case
    # the material sitting in that AMS slot is exactly what we're about to print,
    # so telling the printer to feed from the external spool (the --use-ams=false
    # default) would stall or feed the wrong filament on an AMS-only machine. The
    # job pipeline requires an explicit ams_mapping whenever use_ams is set (it
    # refuses to let firmware pick a default tray), so we supply the detected
    # active slot. If detection failed, the user picked a different material, or
    # we have no firm active slot, keep the conservative external-spool default.
    if (
        state.detected_ams_material is not None
        and state.detected_ams_slot is not None
        and state.material == state.detected_ams_material
    ):
        job_ns.use_ams = True
        job_ns.ams_mapping = str(state.detected_ams_slot)
    # Carry global request flags (sim / verbose) through so the same simulated
    # printer path runs end to end.
    if bool(getattr(args, "sim", False)):
        job_ns.sim = True
    if bool(getattr(args, "verbose", False)):
        job_ns.verbose = True
    return job_ns


# ---------------------------------------------------------------------------
# Temp-workdir hygiene
# ---------------------------------------------------------------------------


def under_workdir(path: str, workdir: str | None) -> bool:
    """Return True iff ``path`` lives inside ``workdir`` (the temp dir we own)."""
    if not workdir:
        return False
    abs_path = os.path.abspath(path)
    abs_workdir = os.path.abspath(workdir)
    return abs_path == abs_workdir or abs_path.startswith(abs_workdir + os.sep)


def preserve_printable(state: WizardState) -> str | None:
    """Return the path where the printable file will survive cleanup.

    Only files that live *inside our temp workdir* are relocated into cwd — a
    user-supplied pre-sliced .3mf/.gcode that never entered the workdir stays
    exactly where it is (we just report its existing path; never move or
    overwrite the user's own file). When relocating from the workdir we pick a
    non-clobbering name so a same-named file already in cwd is never overwritten.
    If the move fails, we keep the whole workdir (return None so the message
    omits a path rather than naming a doomed one).
    """
    from bambu_cli.protocols.ftps import _noncolliding_path

    if not state.printable_path or not os.path.exists(state.printable_path):
        return None

    # Pre-existing user file outside our workdir: leave it in place.
    if not under_workdir(state.printable_path, state.workdir):
        state.kept_path = state.printable_path
        return state.printable_path

    try:
        dest = _noncolliding_path(os.path.join(os.getcwd(), os.path.basename(state.printable_path)))
        # _noncolliding_path already created (reserved) dest as an empty file, so
        # this move replaces our own placeholder — never a pre-existing user file.
        shutil.move(state.printable_path, dest)
        state.kept_path = dest
        return dest
    except OSError:
        # Could not move it out; keep the workdir so the file is not deleted.
        state.kept_path = state.printable_path
        return state.printable_path


def decline_message(state: WizardState) -> str:
    """Preserve the printable file and return the wizard's "Nothing sent" line.

    Declining must never silently delete work: the sliced file is moved out of
    the temp workdir (or left in place when it was the user's own file) and the
    message names where it landed. Shared so the wizard and the TUI say — and
    keep — exactly the same thing.
    """
    kept = preserve_printable(state)
    if kept:
        label = "File kept at" if not state.sliced else "Sliced file kept at"
        return f"Nothing sent. {label} {kept}"
    return "Nothing sent."


def cleanup_workdir(state: WizardState) -> None:
    """Delete the temp workdir unless we deliberately preserved a file in it."""
    if os.environ.get("BAMBU_KEEP_WORKDIR") == "1":
        return
    workdir = state.workdir
    if not workdir or not os.path.isdir(workdir):
        return
    # If we deliberately preserved a file, do not delete it along with the workdir.
    if state.kept_path and os.path.abspath(state.kept_path).startswith(os.path.abspath(workdir) + os.sep):
        return
    shutil.rmtree(workdir, ignore_errors=True)
