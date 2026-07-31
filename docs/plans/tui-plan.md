# Implementation plan: full-screen TUI (`plate tui`)

**Status:** Draft for implementation — hand-off document for autonomous overnight
coding sessions.
**Branch:** all implementation work goes on **`feat/tui`** (branched from `main`).
Each phase below is sized for one ~1–2 h autonomous session and MUST leave the
repo green (every gate in §8 passing) so the session can commit safely.
**Prerequisite reading:** `AGENTS.md`, `docs/plans/interactive-mode-plan.md`
(house conventions, printer-safety rules), `docs/quality-roadmap.md`.

## 1. Goal

`plate go` is a linear wizard: one prompt after another. Dylan wants a real
full-screen terminal app — panels, keyboard navigation, live state — in the
lazygit style: a printer-status dashboard that is always visible, a model/file
selection pane, material/quality preset configuration, a slice→preview→print
flow, and live job monitoring, all in one persistent screen instead of a
question queue.

Design stance is unchanged from the wizard: **a new front-end over existing
machinery, not new machinery.** The TUI collects the same answers the wizard
collects, builds the same `argparse.Namespace` objects, and drives the same
`cmd_download` / `cmd_slice` / `cmd_job` pipeline through the same injectable
collaborator seams (`GoSteps`-style). Anything the TUI needs that currently
lives inside `interactive/session.py` gets *extracted and shared*, never
duplicated.

`plate go` **stays**. It is the low-dependency, low-bandwidth path (SSH, dumb
terminals, screen readers) and its tests anchor the shared logic. `plate tui`
is a sibling front-end. No deprecation in this work.

## 2. Codebase map (read these before writing code)

- **Wizard:** `bambu_cli/interactive/session.py` — `WizardState`, `GoSteps` /
  `GoDeps` (injectable collaborators), `_validate_source` (pure, returns
  `(source, error)`), `_read_loaded_ams_material` (best-effort AMS detection,
  never raises), `_step_prepare` (download → optional zip-extract → slice →
  estimate preview), `_run_print` (builds the `job` namespace via
  `parse_args_or_abort(parser, ["job", path])`, sets `use_ams`/`ams_mapping`
  only when detected material == chosen material and an active slot is known),
  `_preserve_printable` / `_cleanup_workdir` (temp-workdir hygiene),
  `_MATERIAL_CHOICES` / `_QUALITY_CHOICES` + guidance tables.
- **Prompt layer:** `bambu_cli/interactive/prompts.py` — deliberately tiny; its
  docstring already says it is "swappable for a future Textual front-end".
  The TUI does not use it, but copies its stream discipline and cancellation
  semantics (Ctrl-C → exit 5 path).
- **Presets:** `bambu_cli/interactive/presets.py` — `preset_to_job_args`,
  `parse_args_or_abort`.
- **Command wrapper pattern:** `bambu_cli/commands/go.py` — 17-line lazy-import
  wrapper; `cmd_go` exported from `bambu_cli/commands/__init__.py`; dispatch via
  `cli._resolve_command` (`getattr(commands_mod, f"cmd_{name}")`).
- **Status:** `bambu_cli/printer.py::BambuPrinter.status()` →
  `protocols/mqtt.py::get_status()` — synchronous but internally threaded
  (subscribes to `device/{serial}/report`, sends `pushall`, merges deltas until
  `_REQUIRED_STATUS_KEYS` complete, `threading.Event.wait(timeout)`). Payload
  fields: `gcode_state`, `mc_percent`, `bed_temper`/`bed_target_temper`,
  `nozzle_temper`/`nozzle_target_temper`, `layer_num`/`total_layer_num`,
  `mc_remaining_time`, `gcode_file`, `ams` (parse with
  `bambu_cli/ams.py::parse_ams`). `mqtt.monitor_status` is the existing
  subscribe-until-terminal loop the monitor screen mirrors.
- **Sim harness:** global `--sim` flag → `cli.main()` sets
  `ctx.simulation`; `context.current_simulation()`; `BambuPrinter(...,
  simulation_mode=True)` swaps in `_SimMqttClient` / `_SimFtp` (hardcoded IDLE
  status + AMS data, logged fake sends). The TUI must run end-to-end under
  `--sim` — that is also how it gets tested.
- **Estimates:** `bambu_cli/slicer/estimate.py` — `read_3mf_estimate(path) ->
  Estimate(seconds, grams)` (never raises), `format_estimate`.
- **Context/config:** `bambu_cli/context.py` — `RuntimeContext`,
  `RuntimeContext.for_request(args)`, `current_settings()`,
  `current_simulation()`; `Settings.printer_ip == "0.0.0.0"` is the
  unconfigured sentinel.
- **Errors:** domain raises `BambuError` / `abort(...)`; `sys.exit` lives only
  in `cli.py` (CI greps for this). Exit codes in `bambu_cli/constants.py`.
- **Contracts:** every subcommand derived from `build_parser()` must either
  publish `docs/schemas/<name>.json` or follow the `go` precedent: no machine
  contract, `--json` emits the standard error envelope
  (`utils.emit_json_error(args, "tui", EXIT_COMMAND_ERROR, ...,
  failed_step="parse")`) then aborts; contract tests assert that error case.
  See `tests/test_json_contracts.py` and `tests/contracts/`.
- **Tests:** `tests/bambu_test_base.py` (`install_baseline_context`,
  `settings_ctx`, `config_ctx`, `_test_printer`; paho-mqtt mocked at import),
  `tests/test_interactive_session.py`, `tests/test_wizard_guided.py`,
  `tests/test_json_contracts.py::run_main`.

## 3. Framework decision: Textual

**Choice: [Textual](https://textual.textualize.io/), as an optional extra.**

Why Textual over the alternatives:

- **Rich is already a runtime dependency** (`rich>=13.0.0`). Textual is built
  on rich by the same team — same renderables, same markup, one rendering
  stack. Any other framework adds a second, unrelated TUI stack.
- **Testability is first-class**, and this repo lives or dies by its gates.
  `App.run_test()` returns a `Pilot` that drives the app headlessly — press
  keys, click widgets, await idle — inside ordinary pytest. That is the only
  realistic way TUI code clears an 83% coverage floor. urwid and
  prompt_toolkit have no comparable harness; curses is untestable without a
  PTY circus and unusable on Windows (the CI matrix runs Windows and macOS).
- **Typed** (`py.typed` shipped), so `uvx mypy -p bambu_cli` keeps working
  without excludes — moving/splitting modules has broken the mypy gate before.
- **Local-only and clean:** pure Python, MIT-licensed, no telemetry, no
  network access of its own — matches the project's values (the printer link
  stays the only network surface, guarded by the existing `netsafety` code).
- **Professional look with little code:** CSS-like styling, focus management,
  built-in widgets (DataTable, ProgressBar, Tree, Footer key hints), async
  workers for the blocking MQTT calls.

Rejected: **urwid** (mature but dated API, no test pilot, weak typing),
**prompt_toolkit full-screen** (lower level — we would hand-build layout, focus
and redraw), **curses/blessed** (no Windows, no testing story),
**pyTermTk/py_cui** (small communities, no typing/test maturity).

**Dependency policy:** Textual must not become a hard runtime dependency
(interactive-mode-plan set that precedent for the wizard). Add an extra:

```toml
[project.optional-dependencies]
tui = ["textual>=0.86,<2.0"]          # pin to newest release whose python floor fits requires-python
test = [..., "pytest-asyncio", "textual>=0.86,<2.0"]
```

`cmd_tui` import-guards textual and aborts with
`"plate tui requires the TUI extra: pip install 'platecli[tui]'"`
(`EXIT_CONFIG_ERROR`) when missing. The implementing session must verify the
chosen pin's `requires-python` against this project's `>=3.9`; if the current
Textual floor is higher, pin the newest compatible major and record it in the
commit message. `pytest-asyncio` is required because `run_test()` is async.

## 4. App design

### 4.1 Module layout

```
bambu_cli/tui/
    __init__.py          # exports cmd_tui (guarded import), nothing heavy at import time
    app.py               # PlateApp(App): screens, global bindings, BambuError→notification
    deps.py              # TuiDeps dataclass: prompts-free analog of GoDeps —
                         #   steps: GoSteps, status_provider, monitor_factory; injectable for tests
    services.py          # StatusService / MonitorService / PipelineService:
                         #   thin sync adapters over printer.status(), parse_ams,
                         #   GoSteps.download/slice/job — called from Textual thread workers
    screens/
        __init__.py
        dashboard.py     # DashboardScreen: status + AMS panels, nav footer
        prepare.py       # PrepareScreen: source input/file picker + presets + slice preview
        monitor.py       # MonitorScreen: live job progress until terminal state
        confirm.py       # ConfirmModal: explicit print confirmation (safety gate)
    widgets/
        __init__.py
        status_panel.py  # temps, state, wifi, gcode_file
        ams_panel.py     # units/trays from parse_ams, active-tray highlight
        job_progress.py  # percent, layers, remaining time
    styles.tcss          # single Textual stylesheet (package-data; see Phase 1)
bambu_cli/commands/tui_cmd.py   # thin wrapper, lazy import (mirrors commands/go.py)
```

**View/logic split rule:** widgets and screens hold *no* domain logic. All
decisions (source validation, preset mapping, AMS feed decision, estimate
formatting, state reducers) live in plain functions/dataclasses in
`bambu_cli/interactive/` or `bambu_cli/tui/services.py` so they are unit-tested
without a pilot. Pilot tests then only need to cover wiring.

### 4.2 Reuse via extraction (no duplication)

Phase 2 moves these out of `session.py` into a new
`bambu_cli/interactive/core.py` (imported by both `session.py` and the TUI;
`session.py` re-exports so its tests and behavior are untouched):

- `_validate_source` → `validate_source`
- `_MATERIAL_CHOICES/_GUIDANCE`, `_QUALITY_CHOICES/_GUIDANCE` → module constants
- `_read_loaded_ams_material` + `_match_material_preset` → `read_loaded_ams_material`
- the job-namespace builder inside `_run_print` (everything from
  `parse_args_or_abort(parser, ["job", path])` through the
  `use_ams`/`ams_mapping`/`sim`/`verbose` decoration) → `build_job_namespace(state, args, *, confirm)`
- `_preserve_printable`, `_cleanup_workdir`, `_under_workdir` → shared helpers

Prefer dependency injection over patching module globals (the `download/`
Stage B rule): the TUI receives a `TuiDeps` with a `GoSteps` instance, exactly
like the wizard receives `GoDeps`.

### 4.3 Screens, navigation, live state

- **DashboardScreen** (default): left panel printer status, right panel AMS.
  A `StatusService.fetch()` call (wrapping `RuntimeContext.for_request(args)
  .printer().status()`) runs in a **thread worker** (`run_worker(...,
  thread=True)`) because `get_status()` blocks on a `threading.Event`; result
  posted back as a message; refresh on `r` and on a timer (default 10 s, only
  while the screen is active). Failures render an inline "printer unreachable"
  state — never crash the app.
- **PrepareScreen** (`n` = new print): source `Input` (URL or path) validated
  with `validate_source`; `RadioSet`s for material/quality (guidance text
  shown, AMS-detected material pre-selected via `read_loaded_ams_material`, a
  "(detected in AMS)" tag); supports `Checkbox`. "Prepare" runs
  download→extract→slice through `PipelineService` in a thread worker with a
  progress spinner, then shows the `_render_preview` equivalent (model, printer,
  material line honoring the pre-sliced caveat, `format_estimate`). Preflight
  (`_step_preflight`'s checks: configured IP unless sim, slicer executable,
  profiles dir) runs before the form; failures show an error screen telling the
  user to run `plate setup` — the TUI does **not** embed the setup wizard.
- **ConfirmModal:** "Start print / Upload only / Cancel". Printing calls
  `build_job_namespace(..., confirm=True)` then `GoSteps.get_job()(ns)` in a
  worker. This is the only path that sets `confirm=True` — the safety gate the
  wizard has, kept intact.
- **MonitorScreen:** after a started print (or `m` from dashboard), polls
  `status()` every few seconds in a cancellable worker until a terminal
  `gcode_state` (`FINISH`/`FAILED`/`STOP`/`IDLE` — same set as
  `mqtt.monitor_status`), rendering `job_progress`. `Esc` returns to dashboard
  without stopping the print.
- **Global bindings:** `q` quit (with confirm if a pipeline worker is running),
  `d` dashboard, `n` new print, `m` monitor, `?` help overlay, `r` refresh.
  `Footer` shows them. Quit path performs `_cleanup_workdir`.
- **Errors:** workers catch `BambuError` and post it as a message; the app
  shows it in a notification/modal. `BambuError` never escapes to kill the
  app; `sys.exit` never appears outside `cli.py`.

### 4.4 Command surface & wiring

- `cli.build_parser()`: `p_tui = sub.add_parser("tui",
  parents=[get_global_parser()], help="Full-screen terminal UI (dashboard, "
  "guided print, job monitor)")`. No extra flags in v1.
- Routing: add `"tui"` to `LOCAL_COMMANDS` (same reasoning as `go` — it must
  get past the unconfigured-IP hard-fail to render its own guidance;
  interactive-mode-plan §3.2).
- Dispatch: export `cmd_tui` from `bambu_cli/commands/__init__.py`, wrapper in
  `commands/tui_cmd.py` lazy-importing `bambu_cli.tui`.
- TTY / `--json`: identical to `go` — `--json` emits the error envelope and
  aborts `EXIT_COMMAND_ERROR` (`failed_step="parse"`, message
  `"plate tui is interactive; use 'plate job <url> --confirm' for scripts."`);
  non-TTY stdin aborts with the same message. Document the exemption in
  `docs/api.md` next to `go`'s.
- `cli_help_smoke.py` and `ci_workflow_smoke.py` derive the command set from
  `build_parser()` — do not hand-maintain lists; run both after touching
  `cli.py`.

### 4.5 Testing strategy (how this clears the gates)

- **Unit tests (no pilot):** everything extracted into `interactive/core.py`
  and `tui/services.py` is plain sync Python — test like
  `test_interactive_session.py` does today, with `settings_ctx` /
  `_test_printer(simulation_mode=True)` and injected `GoSteps` fakes.
- **Pilot tests:** `async def` tests using `PlateApp(...).run_test()` with a
  `TuiDeps` carrying scripted fakes (statuses, pipeline results) — no real
  MQTT, no sleeps; drive with `pilot.press(...)` and assert on queried widgets.
  Mark with `pytest-asyncio`; they run headless in CI on all three OSes.
- **Sim-mode integration:** at least one pilot test builds real services with
  `simulation_mode=True` transports (the `_SimMqttClient` status payload) to
  prove the `--sim` path end to end, mirroring `test_wizard_guided.py`.
- **Contract tests:** extend `tests/test_json_contracts.py` with the `tui`
  error-envelope cases (`--json`, non-TTY), same shape as `go`'s.
- **Coverage:** the floor is repo-wide 83%. The view/logic split keeps
  screens thin; pilot tests execute the screen code. Do **not** add blanket
  `# pragma: no cover` to TUI modules; the only acceptable pragmas are the
  same narrow interactive-input style used in `prompts.py`. Measure before
  claiming (CLAUDE.md rule).

## 5. Phase 1 — dependency, entry point, status dashboard (read-only)

**Files:**

- `pyproject.toml`: add `[project.optional-dependencies] tui`, extend `test`
  extra with `textual` + `pytest-asyncio`; add `asyncio_mode = "auto"` (or
  explicit markers) to `[tool.pytest.ini_options]`; ensure `styles.tcss` ships
  (package-data / `MANIFEST.in` as the build backend requires).
- `bambu_cli/tui/__init__.py`, `app.py`, `deps.py`, `services.py`
  (StatusService only), `screens/__init__.py`, `screens/dashboard.py`,
  `widgets/__init__.py`, `widgets/status_panel.py`, `widgets/ams_panel.py`,
  `styles.tcss`.
- `bambu_cli/commands/tui_cmd.py`; export in `bambu_cli/commands/__init__.py`.
- `bambu_cli/cli.py`: subparser + `LOCAL_COMMANDS` + no other lists (derived).
- `docs/api.md`: `tui` no-JSON-contract note beside `go`'s.
- Tests: `tests/test_tui_entry.py` (missing-extra abort, `--json` envelope,
  non-TTY abort, `LOCAL_COMMANDS` routing), `tests/test_tui_dashboard.py`
  (pilot: renders sim status + AMS, `r` refreshes, `q` quits, unreachable
  printer renders error state), contract additions in
  `tests/test_json_contracts.py`.

**Acceptance criteria:** `uv run plate tui --sim` on a TTY shows the dashboard
with the simulated IDLE status and AMS trays; `q` exits 0; `plate tui --json`
exits 5 with the standard error envelope; all §8 gates green, **including**
`uv build` + `uv run python tests/package_contents_smoke.py` (packaging
touched) and `uv run python tests/ci_workflow_smoke.py` (cli.py touched).
Commit to `feat/tui`.

## 6. Phase 2 — shared-core extraction + prepare flow (source → presets → slice → preview)

**Files:**

- `bambu_cli/interactive/core.py` (new): extractions listed in §4.2;
  `bambu_cli/interactive/session.py` shrinks to re-export/import from it —
  behavior and public seams (`GoDeps`, `GoSteps`, `cmd_go`) unchanged.
- `bambu_cli/tui/services.py`: add `PipelineService` (download → zip-extract →
  slice via injected `GoSteps`, temp workdir owned per-prepare, estimate via
  `read_3mf_estimate`).
- `bambu_cli/tui/screens/prepare.py`, plus any small widgets it needs.
- `bambu_cli/tui/app.py`: `n` binding, preflight-failure screen.
- Tests: `tests/test_interactive_core.py` (moved/extended unit tests),
  `tests/test_tui_prepare.py` (pilot: invalid source shows inline error; valid
  local `.stl` with fake steps reaches preview; pre-sliced `.3mf` shows the
  "material settings not applied" caveat; AMS-detected default pre-selected).
  Existing `test_interactive_session.py` / `test_wizard_guided.py` must pass
  **unmodified except for import paths** — if their assertions need changing,
  the extraction changed behavior; stop and fix.

**Acceptance criteria:** wizard (`plate go --sim`) behavior byte-identical
(existing tests green); TUI prepare flow completes in a pilot test against
fake steps and against sim transports; mypy clean after the module split (run
it — module moves have broken this gate before). All §8 gates green. Commit to
`feat/tui`.

## 7. Phase 3 — confirm/print + live job monitor

**Files:**

- `bambu_cli/tui/screens/confirm.py` (ConfirmModal: start / upload-only /
  cancel → `build_job_namespace` + `GoSteps.get_job()` in worker; on decline,
  `preserve_printable` message like the wizard's "Nothing sent. Sliced file
  kept at ...").
- `bambu_cli/tui/screens/monitor.py`, `widgets/job_progress.py`,
  `services.py::MonitorService` (cancellable poll loop, terminal-state set
  matching `mqtt.monitor_status`).
- `bambu_cli/tui/app.py`: `m` binding, prepare→confirm→monitor flow, quit
  guard while a job upload is in flight, `_cleanup_workdir` on exit.
- Tests: `tests/test_tui_confirm.py` (pilot: print path passes
  `confirm=True` exactly once and only via the modal; upload-only passes
  `confirm=False`; AMS mapping set only when detected==chosen with a firm
  slot — reuse the wizard's test cases against `build_job_namespace`),
  `tests/test_tui_monitor.py` (pilot: progresses through scripted RUNNING→
  FINISH statuses, stops polling on terminal state, Esc detaches without
  cancelling).

**Acceptance criteria:** full sim end-to-end in one pilot test: dashboard → `n`
→ source/presets → prepare → confirm → monitor → FINISH. No code path starts a
print without the modal. All §8 gates green. Commit to `feat/tui`.

**Safety note (CLAUDE.md):** all of this phase is `--sim`/fake-transport only.
Never run against a live printer or set `BAMBU_LIVE=1` without asking Dylan.

## 8. Phase 4 — polish, docs, hardening

**Files:**

- Help overlay (`?`), consistent Footer bindings, focus order, `styles.tcss`
  polish, empty/error states reviewed on a narrow (80×24) terminal.
- `docs/manual.md`: "plate tui" section (install extra, keys, screens);
  `README.md` mention; `CHANGELOG.md` Unreleased entry;
  `docs/quality-roadmap.md` row for the TUI modules.
- Coverage hardening: fill misses reported by `--cov-report=term-missing` in
  `bambu_cli/tui/*` until the repo floor holds with margin (target the TUI
  package itself ≥ the repo floor; measure, don't assert).
- Sweep: `rg -n "sys.exit" bambu_cli/tui bambu_cli/interactive` returns
  nothing; no hand-maintained command lists were introduced; ResourceWarning
  run clean (workers/timers all cancelled on exit — a leaked worker will fail
  the `-W error::ResourceWarning` CI mode).

**Acceptance criteria:** all §8 gates green from a clean checkout of
`feat/tui`; `uv build` + package smoke green (styles.tcss present in wheel);
docs updated. Final commit to `feat/tui`; branch ready for PR review — do not
merge to `main`, do not add Claude-Session trailers.

## 9. Quality gates (run at the end of EVERY phase)

```bash
uvx ruff check bambu_cli
uvx ruff format --check bambu_cli
uvx mypy -p bambu_cli
uvx bandit -c pyproject.toml -r bambu_cli -ll
uv run python -W error::ResourceWarning -m pytest tests/ -m "not live" \
  --cov=bambu_cli --cov-report=term-missing --cov-fail-under=83
uv run python tests/ci_workflow_smoke.py   # any phase that touches cli.py or ci.yml
python scripts/syntax_smoke.py             # auto-discovers new bambu_cli modules
python scripts/cli_help_smoke.py           # derives subcommands from build_parser()
# when pyproject/packaging touched (Phases 1 and 4):
uv build && uv run python tests/package_contents_smoke.py
```

The lint job (ruff, format, mypy, bandit, pip-audit) is separate from pytest in
CI — green pytest says nothing about them. pip-audit and the Windows/macOS
matrix only run in CI: after pushing `feat/tui`, `gh run watch` and do not
assume green.

## 10. Open questions (do not block; defaults chosen)

- **Q1 — bare `plate` on a TTY opening the TUI:** out of scope; revisit after
  the TUI ships (interactive-mode-plan Q1 pending too).
- **Q2 — embedded setup wizard:** v1 points unconfigured users at
  `plate setup` rather than embedding mDNS discovery in a screen. Candidate
  Phase 5.
- **Q3 — MQTT push subscription instead of polling in MonitorScreen:** polling
  via `get_status()` is simpler and sim-testable; a persistent subscription
  (reusing `monitor_status`'s internals) is a later optimization.
- **Q4 — camera panel (`bambu_cli/camera.py`):** out of scope for v1.

## 11. Phase 5 — advanced slice settings ("a slicer without the slicer")

**Requested after the live test (2026-07-31):** the prepare flow should
optionally expose real slicer control, not just material/quality/supports.
The CLI already has the full surface — ~25 named `slice` flags plus
`--set KEY=VALUE` / `--set-filament KEY=VALUE` reaching every one of the
~176 OrcaSlicer process/filament settings, discovered via
`slice --list-settings` and safety-validated in `_validate_slice_options`
(`bambu_cli/slicer/options.py`). Phase 5 is a front-end over that existing
machinery; no new slicing vocabulary.

### 11.1 Design

- **Opt-in:** PrepareScreen gains a "Settings…" button + `s` binding beside
  the presets. Untouched, the flow behaves exactly as today (presets only).
  For pre-sliced `.3mf` sources the button is disabled with the existing
  "material settings not applied" caveat — overrides cannot apply.
- **SettingsScreen** (new, full screen): grouped form whose fields map 1:1
  onto existing `slice` parser dests — Quality (layer height, first layer
  height), Strength (infill %, pattern, walls, top/bottom layers), Supports
  (enable, type, threshold, interface density), Adhesion (brim), Filament
  (nozzle temp, bed temp, fan speed, flow ratio), Speed (the five accel
  flags), Plate (copies, seam position, ironing). Blank field = no override
  (profile default wins), matching CLI semantics.
- **"All settings" browser** inside SettingsScreen: searchable list of every
  settable key + example value, read from the installed profiles via the
  same loader `slice --list-settings` uses; filter-as-you-type; selecting a
  key opens a value editor and records a `--set` / `--set-filament`-style
  override (kind chosen by which profile the key came from). Where profiles
  are unavailable (e.g. `--sim` with no slicer configured) the browser
  degrades to free-form KEY=VALUE entry — the CLI's unknown-key
  warn-but-pass semantics already tolerate that.
- **State/plumbing:** a `SliceOverrides` dataclass in
  `bambu_cli/interactive/core.py`, carried on `WizardState`; a pure
  `apply_overrides(ns, overrides)` decorates the slice namespace inside
  `run_prepare_pipeline`. Empty overrides ⇒ byte-identical behavior, so the
  wizard (`plate go`) is untouched.
- **Preview + re-slice:** the preview gains an `Overrides   n set (...)`
  line when any are active; changing settings after a preview re-enables
  Prepare (the existing re-prepare path already discards the old workdir).
- **Safety:** temperature overrides from any path stay subject to
  `_validate_slice_options` — the screen surfaces that `BambuError` inline
  (test the nozzle=999 refusal). The filament flow-ratio key is
  `filament_flow_ratio` via `--flow-ratio`/set-filament, NOT process-level
  `flow_ratio` (the silent-no-op gotcha) — browser keys from the filament
  profile must be sent as filament overrides.

### 11.2 Tests

Unit: `SliceOverrides` round-trip + `apply_overrides` decoration (incl.
none-set ⇒ namespace identical). Pilot: form values land on the slice
namespace via fake `GoSteps`; browser search filters; sim degradation;
pre-sliced disables the button; temp-refusal renders inline. Integration:
one `tests/fakes/orca_stub` run asserting overrides land in the temp
profiles the stub receives (the read-back lesson — never assert only on the
request).

**Acceptance:** default flow byte-identical when no settings touched; full
§9 gates green; the settings screen usable at 80×24.
