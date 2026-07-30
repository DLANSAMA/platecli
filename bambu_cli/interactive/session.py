"""The ``plate go`` interactive guided-print wizard.

A new front-end over existing machinery, not new machinery. The wizard collects
answers through the injectable ``prompts`` layer, builds the same
``argparse.Namespace`` the ``job`` command uses, and drives the existing
download/slice/print pipeline via ``cmd_download`` / ``cmd_slice`` / ``cmd_job``.

Design (see docs/plans/interactive-mode-plan.md §5):

* Linear state machine, steps 0-7, each a small function over a plain
  ``WizardState`` dataclass so steps are unit-testable without a TTY.
* Prompts are injected (``GoDeps.prompts``) so tests script answers.
* Pipeline collaborators are injected (``GoDeps.steps``) mirroring ``JobSteps``.
* Domain code never calls ``sys.exit`` — terminal conditions raise ``BambuError``
  via ``abort`` (or the cancellation path, which ``cli.main`` turns into exit 5).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from bambu_cli import utils
from bambu_cli.constants import (
    ARCHIVE_DOWNLOAD_EXTENSIONS,
    DEFAULT_MAX_DOWNLOAD_MB,
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_FILE_ERROR,
    PRINT_READY_EXTENSIONS,
    SLICEABLE_EXTENSIONS,
)
from bambu_cli.errors import BambuError, abort
from bambu_cli.interactive.presets import parse_args_or_abort, preset_to_job_args
from bambu_cli.interactive.prompts import Prompts, is_cancelled
from bambu_cli.paths import expand_path as _expand_path
from bambu_cli.slicer.estimate import format_estimate, read_3mf_estimate

_NON_TTY_MESSAGE = "plate go is interactive; use 'plate job <url> --confirm' for scripts."
_MAX_URL_ATTEMPTS = 3

_MATERIAL_CHOICES = ["PLA", "PETG", "ABS", "TPU"]
_MATERIAL_GUIDANCE = {
    "PLA": "easy, rigid, most models",
    "PETG": "tougher, slightly stringy",
    "ABS": "strong, needs an enclosure",
    "TPU": "flexible; print slow",
}
_QUALITY_CHOICES = ["draft", "standard", "fine"]
_QUALITY_GUIDANCE = {
    "draft": "fastest, visible layers",
    "standard": "the right default",
    "fine": "slowest, smoothest",
}


class _WizardCancelled(Exception):
    """Raised internally when the user aborts a prompt; converted to exit 5."""


class _GoDone(Exception):
    """Raised to end the wizard cleanly (exit 0) — e.g. the user declined."""

    def __init__(self, code: int = 0) -> None:
        super().__init__()
        self.code = code


@dataclass
class WizardState:
    """Plain state carried between wizard steps."""

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
    return _read_loaded_ams_material


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


@dataclass
class GoDeps:
    """Everything the wizard needs, injectable for tests."""

    prompts: Any = None
    steps: GoSteps = field(default_factory=GoSteps)

    def get_prompts(self) -> Any:
        if self.prompts is not None:
            return self.prompts
        return Prompts()

    def get_steps(self) -> GoSteps:
        return self.steps


def _check(value: Any) -> Any:
    """Return the answer, or raise _WizardCancelled if the prompt was aborted."""
    if is_cancelled(value):
        raise _WizardCancelled()
    return value


# ---------------------------------------------------------------------------
# Step 0 — preflight
# ---------------------------------------------------------------------------


def _step_preflight(args: argparse.Namespace, deps: GoDeps) -> None:
    """Verify printer config, OrcaSlicer, and profiles before collecting answers."""
    from bambu_cli.config import load_config
    from bambu_cli.context import current_settings
    from bambu_cli.slicer.profiles import _profiles_dir_diagnostic, _slicer_executable_problem

    prompts = deps.get_prompts()
    simulation = bool(getattr(args, "sim", False))

    settings = current_settings()
    if settings.printer_ip == "0.0.0.0" and not simulation:
        prompts.print("No printer configured yet — let's set one up.")
        answer = _check(prompts.confirm("Run setup now?", default=True))
        if answer:
            deps.get_steps().get_setup()(args)
            load_config(exit_on_fail=False)
            settings = current_settings()
            # Re-check rather than trusting cmd_setup to have aborted on failure:
            # if setup returned without configuring an IP, stop here with a clear
            # message instead of proceeding into the pipeline with the sentinel.
            if settings.printer_ip == "0.0.0.0":
                abort(
                    "Setup did not configure a printer. Run 'plate setup', then 'plate go'.",
                    exit_code=EXIT_CONFIG_ERROR,
                    failed_step="config",
                )
        else:
            abort(
                "Run 'plate setup' when ready, then 'plate go'.",
                exit_code=EXIT_CONFIG_ERROR,
                failed_step="config",
            )

    # OrcaSlicer must be usable before we ask for a URL — never collect answers we
    # cannot act on.
    problem = _slicer_executable_problem(settings.orca_slicer)
    if problem:
        abort(problem, exit_code=EXIT_CONFIG_ERROR, failed_step="config")

    if not os.path.isdir(os.path.join(settings.profiles_dir, "process")):
        hint, _detected = _profiles_dir_diagnostic(settings.profiles_dir)
        message = hint or (
            f"OrcaSlicer profiles not found under {settings.profiles_dir}. "
            "Run 'plate setup' to configure a profiles directory."
        )
        abort(message, exit_code=EXIT_CONFIG_ERROR, failed_step="config")


# ---------------------------------------------------------------------------
# Step 1 — source
# ---------------------------------------------------------------------------

_SUPPORTED_SOURCE_EXTS = SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS


def _validate_source(raw: str) -> tuple[str | None, str | None]:
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
    if ext not in _SUPPORTED_SOURCE_EXTS:
        supported = ", ".join(_SUPPORTED_SOURCE_EXTS)
        return None, f"Unsupported file type '{ext or 'none'}'. Supported: {supported}"
    return source, None


def _step_source(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()
    positional = getattr(args, "source", None)

    for attempt in range(_MAX_URL_ATTEMPTS):
        if positional is not None:
            raw: Any = positional
            positional = None  # only use the positional once; re-prompt on failure
        else:
            raw = _check(
                prompts.text("Paste a model URL (Printables page, direct STL/3MF/ZIP link) or a local file path")
            )
        source, error = _validate_source(str(raw))
        if source is not None:
            state.source = source
            return
        prompts.print(error or "Invalid source.")
        if attempt < _MAX_URL_ATTEMPTS - 1:
            prompts.print(f"Try again ({_MAX_URL_ATTEMPTS - attempt - 1} attempts left).")

    abort(
        f"Could not get a usable model source after {_MAX_URL_ATTEMPTS} attempts.",
        exit_code=EXIT_FILE_ERROR,
        failed_step="validate",
    )


# ---------------------------------------------------------------------------
# Step 2 — printer
# ---------------------------------------------------------------------------


def _step_printer(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    from bambu_cli.config import MODEL_MAPPING
    from bambu_cli.context import current_settings

    prompts = deps.get_prompts()
    settings = current_settings()
    model_info = MODEL_MAPPING.get(settings.printer_model, {})
    full_name = model_info.get("full_name", settings.printer_model or "printer")
    prompts.print(f"Printer: {full_name} at {settings.printer_ip} ({settings.nozzle_size}mm nozzle)")
    answer = _check(prompts.confirm("Print on this printer?", default=True))
    if not answer:
        prompts.print("No problem — run 'plate setup' to configure a different printer.")
        raise _GoDone(0)


# ---------------------------------------------------------------------------
# Step 3 — material
# ---------------------------------------------------------------------------


def _read_loaded_ams_material(args: argparse.Namespace, on_active_slot=None) -> str | None:
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
        return _match_material_preset(loaded_type)
    except Exception:
        # AMS detection is a nicety — never let it block or break the wizard.
        return None


def _match_material_preset(loaded_type: str | None) -> str | None:
    """Map a reported AMS filament type onto a MATERIAL_PRESETS key, or None."""
    if not loaded_type:
        return None
    normalized = str(loaded_type).strip().upper()
    for key in _MATERIAL_CHOICES:
        if normalized == key:
            return key
    return None


def _step_material(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()
    detector = deps.get_steps().get_ams_material()

    slot_holder: dict[str, int] = {}

    def _record_slot(slot):
        slot_holder["slot"] = slot

    # The real detector accepts an on_active_slot callback so we can capture the
    # active AMS slot for the ams_mapping; injected test seams take only args, so
    # fall back to the single-arg call for them.
    try:
        detected = detector(args, _record_slot)
    except TypeError:
        detected = detector(args)
    state.detected_ams_material = detected if detected in _MATERIAL_CHOICES else None
    state.detected_ams_slot = slot_holder.get("slot") if state.detected_ams_material is not None else None
    default = detected if detected in _MATERIAL_CHOICES else "PLA"
    for name in _MATERIAL_CHOICES:
        note = "  (detected in AMS)" if name == detected else ""
        prompts.print(f"  {name} — {_MATERIAL_GUIDANCE[name]}{note}")
    answer = _check(prompts.choice("Material", _MATERIAL_CHOICES, default=default))
    state.material = str(answer)


# ---------------------------------------------------------------------------
# Step 4 — quality
# ---------------------------------------------------------------------------


def _step_quality(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()
    for name in _QUALITY_CHOICES:
        prompts.print(f"  {name} — {_QUALITY_GUIDANCE[name]}")
    answer = _check(prompts.choice("Quality", _QUALITY_CHOICES, default="standard"))
    state.quality = str(answer)


# ---------------------------------------------------------------------------
# Step 5 — supports
# ---------------------------------------------------------------------------


def _step_supports(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()
    answer = _check(prompts.confirm("Does the model have big overhangs that need supports?", default=False))
    state.supports = bool(answer)


# ---------------------------------------------------------------------------
# Step 6 — prepare (download + slice) + preview
# ---------------------------------------------------------------------------


def _step_prepare(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    from bambu_cli.download import _extract_zip_model, _is_http_url
    from bambu_cli.download.naming import _file_extension
    from bambu_cli.job.predict import _slice_args_for_job

    prompts = deps.get_prompts()
    steps = deps.get_steps()

    assert state.source is not None  # set by _step_source  # noqa: S101
    source = state.source

    if state.workdir is None:
        state.workdir = tempfile.mkdtemp(prefix="bambu-go-")
    workdir = state.workdir

    prompts.print("Downloading and slicing — this can take a minute or two...")

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

    _render_preview(prompts, state, source, model_path)


def _render_preview(prompts: Any, state: WizardState, source: str, model_path: str) -> None:
    from bambu_cli.config import MODEL_MAPPING
    from bambu_cli.context import current_settings
    from bambu_cli.download.naming import _file_extension, _portable_basename

    settings = current_settings()
    model_info = MODEL_MAPPING.get(settings.printer_model, {})
    full_name = model_info.get("full_name", settings.printer_model or "printer")
    quality_layer = {"draft": "0.28mm", "standard": "0.20mm", "fine": "0.12mm"}.get(state.quality, "")

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

    prompts.print("")
    prompts.print(f"Model      {name}")
    prompts.print(f"Printer    {full_name}, {settings.nozzle_size}mm nozzle")
    supports_label = "yes" if state.supports else "no"
    if state.sliced:
        prompts.print(
            f"Material   {state.material}  ·  Quality: {state.quality} ({quality_layer})  ·  Supports: {supports_label}"
        )
    else:
        # Pre-sliced source: the wizard's material/quality choices were not applied.
        prompts.print("Material   (pre-sliced — material settings not applied)")
    prompts.print(f"Estimate   {estimate_line}")
    prompts.print("")


# ---------------------------------------------------------------------------
# Step 7 — confirm and print
# ---------------------------------------------------------------------------


def _run_print(args: argparse.Namespace, deps: GoDeps, state: WizardState, *, confirm: bool) -> None:
    """Drive cmd_job on the sliced/printer-ready file (upload, optionally print)."""
    from bambu_cli.cli import build_parser

    assert state.printable_path is not None  # noqa: S101
    parser = build_parser()
    # parse_args raises SystemExit(2) on failure; parse_args_or_abort converts
    # that to a BambuError so the wizard never bypasses the abort/BambuError
    # contract (domain code must not sys.exit). The path here is a
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
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None
    deps.get_steps().get_job()(job_ns)


def _step_confirm_print(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()

    start = _check(prompts.confirm("Start this print now?", default=False))
    if start:
        _run_print(args, deps, state, confirm=True)
        prompts.print("Printing. Watch it live: plate status --monitor")
        raise _GoDone(0)

    upload = _check(
        prompts.confirm(
            "Upload it to the printer anyway (start later from the screen or with 'plate print')?",
            default=False,
        )
    )
    if upload:
        _run_print(args, deps, state, confirm=False)
        raise _GoDone(0)

    # Nothing sent. Preserve the printable file outside the temp workdir so the
    # path we print survives cleanup (a user's own pre-sliced file stays put).
    kept = _preserve_printable(state)
    if kept:
        label = "File kept at" if not state.sliced else "Sliced file kept at"
        prompts.print(f"Nothing sent. {label} {kept}")
    else:
        prompts.print("Nothing sent.")
    raise _GoDone(0)


def _under_workdir(path: str, workdir: str | None) -> bool:
    """Return True iff ``path`` lives inside ``workdir`` (the temp dir we own)."""
    if not workdir:
        return False
    abs_path = os.path.abspath(path)
    abs_workdir = os.path.abspath(workdir)
    return abs_path == abs_workdir or abs_path.startswith(abs_workdir + os.sep)


def _preserve_printable(state: WizardState) -> str | None:
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
    if not _under_workdir(state.printable_path, state.workdir):
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


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _cleanup_workdir(state: WizardState) -> None:
    if os.environ.get("BAMBU_KEEP_WORKDIR") == "1":
        return
    workdir = state.workdir
    if not workdir or not os.path.isdir(workdir):
        return
    # If we deliberately preserved a file, do not delete it along with the workdir.
    if state.kept_path and os.path.abspath(state.kept_path).startswith(os.path.abspath(workdir) + os.sep):
        return
    shutil.rmtree(workdir, ignore_errors=True)


def _run_go(args: argparse.Namespace, deps: GoDeps) -> None:
    state = WizardState()
    try:
        _step_preflight(args, deps)
        _step_source(args, deps, state)
        _step_printer(args, deps, state)
        _step_material(args, deps, state)
        _step_quality(args, deps, state)
        _step_supports(args, deps, state)
        _step_prepare(args, deps, state)
        _step_confirm_print(args, deps, state)
    except _GoDone:
        # Clean, intentional early exit (decline / done). Exit 0.
        return
    finally:
        _cleanup_workdir(state)


def cmd_go(args: argparse.Namespace, deps: GoDeps | None = None) -> None:
    """Interactive guided print: URL in, plastic out — no slicer knowledge needed.

    ``deps`` is injectable for tests; production callers pass nothing and get the
    real prompt layer + pipeline collaborators.
    """
    if deps is None:
        deps = GoDeps()

    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        # Interactive mode has no machine contract; agents already have `job`.
        utils.emit_json_error(
            args,
            "go",
            EXIT_COMMAND_ERROR,
            _NON_TTY_MESSAGE,
            failed_step="parse",
        )
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse")

    if not sys.stdin.isatty():
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse")

    try:
        _run_go(args, deps)
    except _WizardCancelled:
        # Matches cli.main's Ctrl-C behavior: message + EXIT_COMMAND_ERROR.
        print("\nOperation cancelled by user.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR, failed_step="cancelled")
