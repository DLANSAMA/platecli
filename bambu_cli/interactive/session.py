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
* Domain code never terminates the process — terminal conditions raise
  ``BambuError`` via ``abort`` (or the cancellation path, which ``cli.main``
  turns into exit 5).

Everything a second front-end also needs (source validation, AMS detection, the
prepare pipeline, the job-namespace builder, workdir hygiene) lives in
``bambu_cli.interactive.core`` and is re-exported here under the module-private
names this module has always used, so both front-ends share one implementation
(plan §4.2). This module keeps only the wizard's own prompt choreography.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from bambu_cli import utils
from bambu_cli.constants import (
    EXIT_COMMAND_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_FILE_ERROR,
)
from bambu_cli.errors import abort
from bambu_cli.interactive.core import MATERIAL_CHOICES as _MATERIAL_CHOICES
from bambu_cli.interactive.core import MATERIAL_GUIDANCE as _MATERIAL_GUIDANCE
from bambu_cli.interactive.core import QUALITY_CHOICES as _QUALITY_CHOICES
from bambu_cli.interactive.core import QUALITY_GUIDANCE as _QUALITY_GUIDANCE
from bambu_cli.interactive.core import SUPPORTED_SOURCE_EXTS as _SUPPORTED_SOURCE_EXTS  # noqa: F401
from bambu_cli.interactive.core import GoSteps as GoSteps
from bambu_cli.interactive.core import WizardState as WizardState
from bambu_cli.interactive.core import build_job_namespace as _build_job_namespace
from bambu_cli.interactive.core import cleanup_workdir as _cleanup_workdir
from bambu_cli.interactive.core import decline_message as _decline_message
from bambu_cli.interactive.core import detect_ams_material as _detect_ams_material
from bambu_cli.interactive.core import make_workdir as _make_workdir
from bambu_cli.interactive.core import match_material_preset as _match_material_preset  # noqa: F401
from bambu_cli.interactive.core import preserve_printable as _preserve_printable  # noqa: F401
from bambu_cli.interactive.core import preview_rows as _preview_rows
from bambu_cli.interactive.core import read_loaded_ams_material as _read_loaded_ams_material  # noqa: F401
from bambu_cli.interactive.core import run_prepare_pipeline as _run_prepare_pipeline
from bambu_cli.interactive.core import slicer_preflight_problem as _slicer_preflight_problem
from bambu_cli.interactive.core import under_workdir as _under_workdir  # noqa: F401
from bambu_cli.interactive.core import validate_source as _validate_source
from bambu_cli.interactive.prompts import Prompts, is_cancelled

__all__ = [
    "GoDeps",
    "GoSteps",
    "WizardState",
    "cmd_go",
]

_NON_TTY_MESSAGE = (
    "plate go is interactive and needs a terminal. Scripts and agents: use 'plate job <url> --json'"
    " (it downloads, slices, and uploads; add --confirm only to start the print),"
    " or 'plate --sim status' to try things with a fake printer."
)
_MAX_URL_ATTEMPTS = 3


class _WizardCancelled(Exception):
    """Raised internally when the user aborts a prompt; converted to exit 5."""


class _GoDone(Exception):
    """Raised to end the wizard cleanly (exit 0) — e.g. the user declined."""

    def __init__(self, code: int = 0) -> None:
        super().__init__()
        self.code = code


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
    problem = _slicer_preflight_problem(settings)
    if problem:
        abort(problem, exit_code=EXIT_CONFIG_ERROR, failed_step="config")


# ---------------------------------------------------------------------------
# Step 1 — source
# ---------------------------------------------------------------------------


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


def _step_material(args: argparse.Namespace, deps: GoDeps, state: WizardState) -> None:
    prompts = deps.get_prompts()
    detector = deps.get_steps().get_ams_material()

    detected, slot = _detect_ams_material(detector, args)
    state.detected_ams_material = detected
    state.detected_ams_slot = slot
    default = detected if detected is not None else "PLA"
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
    prompts = deps.get_prompts()
    steps = deps.get_steps()

    if state.workdir is None:
        state.workdir = _make_workdir()

    prompts.print("Downloading and slicing — this can take a minute or two...")

    model_path = _run_prepare_pipeline(steps, state, state.workdir)

    _render_preview(prompts, state, model_path)


def _render_preview(prompts: Any, state: WizardState, model_path: str) -> None:
    prompts.print("")
    for label, value in _preview_rows(state, model_path):
        prompts.print(f"{label:<11}{value}")
    prompts.print("")


# ---------------------------------------------------------------------------
# Step 7 — confirm and print
# ---------------------------------------------------------------------------


def _run_print(args: argparse.Namespace, deps: GoDeps, state: WizardState, *, confirm: bool) -> None:
    """Drive cmd_job on the sliced/printer-ready file (upload, optionally print)."""
    job_ns = _build_job_namespace(state, args, confirm=confirm)
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
    prompts.print(_decline_message(state))
    raise _GoDone(0)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


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
    """Guided print: URL or file in, plastic out — OrcaSlicer runs underneath, no flags to learn.

    ``deps`` is injectable for tests; production callers pass nothing and get the
    real prompt layer + pipeline collaborators.
    """
    if deps is None:
        deps = GoDeps()

    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        # Interactive mode has no machine contract; agents already have `job`.
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse", command="go")

    if not sys.stdin.isatty():
        abort(_NON_TTY_MESSAGE, exit_code=EXIT_COMMAND_ERROR, failed_step="parse")

    try:
        _run_go(args, deps)
    except _WizardCancelled:
        # Matches cli.main's Ctrl-C behavior: message + EXIT_COMMAND_ERROR.
        print("\nOperation cancelled by user.", file=sys.stderr)
        abort("", exit_code=EXIT_COMMAND_ERROR, failed_step="cancelled")
