# Implementation plan: interactive mode (`plate go`)

**Status:** **Implemented — `plate go` shipped in 0.4.0.** Kept as the design record
and rationale; it is no longer a to-do list, and the "current version" statements
below are frozen at the time of writing. For current behaviour read
[the manual](../manual.md#guided-mode-plate-go), not this file.

*(Historical, as written:)* **Prerequisite:** Ship **0.4.0** first (`main` was
`0.4.0.dev0` with an active Unreleased changelog). Interactive mode targeted
**0.5.0** on a stable base; do not start Phase 1 until the 0.4.0 tag exists
(see `docs/releasing.md`).

## 1. Goal

A guided wizard for people who want to print from a URL without ever touching a
slicer. Flow: `plate go` (and possibly bare `plate` on a TTY — open question Q1) →
paste model URL → pick printer → pick material + quality preset → preview
time/filament estimate → explicit confirm → print.

Design stance: **a new front-end over existing machinery, not new machinery.** The
wizard collects answers, builds the same `argparse.Namespace` the `job` command
uses, and drives the existing pipeline. No slicer knobs are exposed; a small preset
table maps friendly choices onto the flags `job` already accepts.

Prompts use **rich** (already a runtime dependency; `rich>=13.0.0` in
`pyproject.toml`) — `rich.prompt.Prompt` / `Confirm` / `IntPrompt` and
`rich.table.Table` for the summary. **Textual is a possible future upgrade for a
full-screen TUI but must NOT become a dependency in this work.** Structure the
wizard so the prompt layer is swappable (see §4, `prompts.py`).

## 2. Codebase map (what exists and gets reused)

Read these before writing code:

- **Entry point:** console script `plate = bambu_cli.bambu:main` (`pyproject.toml`),
  which imports `main` from `bambu_cli/cli.py`. `cli.build_parser()` declares all
  subcommands; `cli.main()` parses, calls `load_config(exit_on_fail=False)`,
  installs a `RuntimeContext`, then dispatches via `cli._resolve_command(name)`,
  which looks up `cmd_<name>` on `bambu_cli.commands` (`job`/`send` both map to
  `cmd_job`). No-subcommand today prints help to stderr and exits
  `EXIT_COMMAND_ERROR` (5).
- **Pipeline:** `bambu_cli/job/orchestrate.py` — `_cmd_job(args)` builds
  `RuntimeContext.for_request(args)` + `JobSteps()` and calls `_run_job`. `_run_job`
  handles URL validation/SSRF (`_validate_http_url_or_exit`), download, ZIP
  extraction, slicing (`_slice_args_for_job` in `job/predict.py`), remote-name
  safety (`_safe_remote_name`), upload, and the `--confirm` print gate. `JobSteps`
  (`job/steps.py`) is the injection seam tests use for fakes.
- **Slicing:** `bambu_cli/slicer/cmd.py::cmd_slice` — contains the `quality_map`
  (`draft`→`0.28mm Extra Draft @BBL {model_code}`, `standard`→`0.20mm Standard`,
  `high`→`0.12mm Fine`, plus literal layer heights `0.12`–`0.28`), filament-profile
  substring matching (prefers `@base` files in `<profiles_dir>/filament/`, falls
  back to `Bambu PLA Basic @base.json`), machine-profile fallback chain, and
  `_finalize_slice` (`slicer/output.py`) which validates the produced `.3mf`
  (`_is_valid_sliced_3mf`).
- **Profiles:** `bambu_cli/slicer/profiles.py` — `_discover_process_profile`,
  `_create_temp_profiles`, `_slicer_executable_problem`, `_profiles_dir_diagnostic`.
- **Config/context:** `bambu_cli/config.py` (`load_config`, `MODEL_MAPPING`,
  `detect_orca_slicer`, `detect_profiles_dir`) and `bambu_cli/context.py`
  (`Settings` dataclass: `printer_ip` default `"0.0.0.0"` = unconfigured sentinel,
  `printer_model`, `nozzle_size`, `profiles_dir`; `current_settings()`;
  `RuntimeContext.for_request(args)`).
- **Setup wizard:** `bambu_cli/setup_cmd/wizard.py` (`cmd_setup`, mDNS discovery)
  and `setup_cmd/common.py` (`_prompt_text` / `_prompt_secret`: print prompt to
  **stderr**, read `input()`, `EOFError` → abort; marked
  `# pragma: no cover -- interactive prompt`). Interactive mode follows the same
  stream discipline: prompts and chrome on stderr, machine data (if any) on stdout.
- **Errors/exit codes:** `bambu_cli/errors.py` (`BambuError`, `abort`);
  `bambu_cli/constants.py` exit codes (`EXIT_SUCCESS`=0, `EXIT_CONFIG_ERROR`=1,
  `EXIT_NETWORK_ERROR`=2, `EXIT_FILE_ERROR`=3, `EXIT_PRINTER_ERROR`=4,
  `EXIT_COMMAND_ERROR`=5, `EXIT_TIMEOUT`=6) and the command-routing sets
  `PRINTER_CONFIG_COMMANDS` / `LOCAL_COMMANDS` / `PRINTER_NETWORK_COMMANDS`.
- **Status/AMS:** `bambu_cli/commands/status.py` + `protocols/mqtt.py` expose
  printer state incl. AMS tray materials (see the `--sim` output in README) —
  Phase 3 uses this to offer detected filament as the material default.
- **Sim mode:** global `--sim` sets `ctx.simulation`; the whole pipeline runs
  against a fake printer. The wizard must work end-to-end under `--sim` — that is
  also how it gets tested.

**What does not exist yet:** any time/filament estimate. Nothing in `bambu_cli`
parses slice metadata (verified by grep). §6 adds it.

## 3. Command surface & wiring

### 3.1 New subcommand `go`

In `cli.build_parser()`:

```python
p_go = sub.add_parser(
    "go",
    parents=[get_global_parser()],
    help="Interactive guided print: URL in, plastic out — no slicer knowledge needed",
)
p_go.add_argument("source", nargs="?", help="Model URL or local file (skips the first prompt)")
```

No `--yes` flag — resolved in §11 Q5 (hard TTY requirement; tests inject the
prompt layer instead).

Dispatch: `cli._resolve_command` resolves `go` → `cmd_go` via the existing
`getattr(commands_mod, f"cmd_{name}")`; export `cmd_go` from
`bambu_cli/commands/__init__.py` (thin wrapper importing from
`bambu_cli.interactive.session`, matching the `setup_wrappers.py` pattern of lazy
imports).

### 3.2 Routing-set decision (important, easy to get wrong)

Do **NOT** add `go` to `PRINTER_CONFIG_COMMANDS`/`PRINTER_NETWORK_COMMANDS`.
`cli.main()` hard-fails commands in `PRINTER_NETWORK_COMMANDS` when
`printer_ip == "0.0.0.0"` ("run `plate setup` first"). The wizard's job on an
unconfigured machine is to *offer to run setup itself* (§5 step 0), so it must get
past that check and do its own validation. Add `go` to `LOCAL_COMMANDS`.

### 3.3 TTY and `--json` behavior

- `go` requires a TTY on stdin: if `not sys.stdin.isatty()`, abort with
  `EXIT_COMMAND_ERROR` and the message
  `plate go is interactive; use 'plate job <url> --confirm' for scripts.`
  (Mirrors `_json_setup_should_be_noninteractive` logic in spirit.)
- `--json` + `go`: emit the standard error envelope
  (`emit_json_error(args, "go", EXIT_COMMAND_ERROR, ...)` with
  `failed_step="parse"`) and exit 5. Interactive mode has no machine contract;
  agents already have `job`.
- **Contract-suite consequence:** the contract tests derive the subcommand list
  from `build_parser()` and fail any command without a published schema (see
  CHANGELOG Unreleased and `tests/contracts/test_schema_validation.py` /
  `tests/test_json_contracts.py`). Adding `go` WILL trip this. Resolution: publish
  `docs/schemas/go.json` describing the *error* envelope above (`$id`, `title`,
  `status: "error"` only), and add `go` to the `docs/api.md` command table with a
  note that `--json` always errors. Do not add a silent test exemption — the
  suite's design intent is "derived, not hand-maintained."
- Bare `plate` with no args: **resolved (§11 Q1)** — launch the wizard when
  `sys.stdin.isatty() and sys.stdout.isatty()` and no `--json`; otherwise keep
  today's help-to-stderr + `EXIT_COMMAND_ERROR`. Also append the epilog hint
  `Tip: 'plate go' walks you through printing from a URL.` so the help path still
  advertises it. Phase 2/3 work.

## 4. New files

All new interactive code lives in a new package `bambu_cli/interactive/`:

| File | Contents |
|---|---|
| `bambu_cli/interactive/__init__.py` | re-export `cmd_go` |
| `bambu_cli/interactive/session.py` | `cmd_go(args)` + the state machine: step functions `_step_source`, `_step_printer`, `_step_material`, `_step_quality`, `_step_supports`, `_step_preview`, `_step_confirm_print`; each takes/returns a plain `WizardState` dataclass so steps are unit-testable without a TTY |
| `bambu_cli/interactive/prompts.py` | thin prompt layer over `rich.prompt` writing to `Console(stderr=True)`; ONLY module that touches input/rich-prompt (swap point for future Textual); handles `KeyboardInterrupt`/`EOFError` → returns a `Cancelled` sentinel that `session` converts to "Operation cancelled" + `EXIT_COMMAND_ERROR` (matching `cli.main`'s existing Ctrl-C behavior) |
| `bambu_cli/interactive/presets.py` | pure data + pure functions: `MATERIAL_PRESETS`, `QUALITY_PRESETS`, `preset_to_job_args(state) -> argparse.Namespace` (§5.4, §7) |
| `bambu_cli/slicer/estimate.py` | `read_3mf_estimate(path) -> Estimate` (§6); pure, no I/O beyond reading the zip |
| `docs/schemas/go.json` | error-envelope schema (§3.3) |
| `tests/test_interactive_presets.py`, `tests/test_interactive_session.py`, `tests/test_slicer_estimate.py` | §8 |

Touched existing files: `cli.py` (subparser + `LOCAL_COMMANDS` entry in
`constants.py`), `commands/__init__.py`, `docs/api.md`, `docs/manual.md`,
`README.md`, `CHANGELOG.md`.

## 5. UX flow — every prompt and error path

The wizard is a linear state machine. Every prompt writes to stderr. Ctrl-C or
EOF at any prompt → `\nOperation cancelled by user.` → exit 5. Every terminal
failure ends with a copy-pasteable next command, matching the codebase's
`next_command` habit.

### Step 0 — preflight (no prompt unless something is wrong)

1. `load_config` already ran in `main()`. If `current_settings().printer_ip == "0.0.0.0"`
   and not `ctx.simulation`: say `No printer configured yet — let's set one up.`,
   `Confirm("Run setup now?")` → yes: call `commands.cmd_setup(args)` (the existing
   guided wizard, incl. mDNS discovery), then `load_config` again and continue; no:
   exit `EXIT_CONFIG_ERROR` with `Run 'plate setup' when ready, then 'plate go'.`
2. OrcaSlicer check: `_slicer_executable_problem(settings.orca_slicer)`
   (`slicer/profiles.py`). If a problem string comes back, print it (it already
   embeds `orca_install_hint()` / `plate setup` advice) and exit
   `EXIT_CONFIG_ERROR`. Do this *before* asking for a URL — never collect answers
   we can't act on.
3. Profiles check: if `not os.path.isdir(os.path.join(settings.profiles_dir, "process"))`,
   run `_profiles_dir_diagnostic` and show its hint; exit `EXIT_CONFIG_ERROR`.

### Step 1 — source

Prompt: `Paste a model URL (Printables page, direct STL/3MF/ZIP link) or a local file path:`
(pre-filled/skipped when the `source` positional was given).

Validation, in order, reusing `bambu_cli.download` helpers exactly as
`_run_job` does: `_normalize_url_input` → if `_looks_like_url` but not
`_is_http_url`, or `_validate_http_url_or_exit` raises `BambuError` → show the
error's message and **re-prompt** (max 3 attempts, then exit `EXIT_FILE_ERROR`).
Local paths: `_expand_path`, must exist and not be a directory
(`_is_directory_input`); extension must be in
`SLICEABLE_EXTENSIONS + PRINT_READY_EXTENSIONS + ARCHIVE_DOWNLOAD_EXTENSIONS`
else re-prompt with the supported list.

### Step 2 — printer

Single-printer reality today (one config = one printer): display, don't interrogate —
`Printer: Bambu Lab P1S at 192.168.1.23 (0.4mm nozzle)` from `current_settings()` +
`MODEL_MAPPING[settings.printer_model]["full_name"]`, then
`Confirm("Print on this printer?", default=True)`. Decline → point at
`plate setup` and exit 0 (a decline is not an error). Multi-printer profiles are
out of scope (Q4).

### Step 3 — material

`Prompt.ask` with choices from `MATERIAL_PRESETS` keys: `PLA` (default), `PETG`,
`ABS`, `TPU`. One line of guidance per material in the choice text (e.g.
`PLA — easy, rigid, most models`, `PETG — tougher, slightly stringy`,
`TPU — flexible; print slow`). Phase 3 upgrades the default by reading loaded AMS
material from `status` (§9 Phase 3).

### Step 4 — quality

`Prompt.ask` choices from `QUALITY_PRESETS`: `draft — fastest, visible layers`,
`standard — the right default` (default), `fine — slowest, smoothest`. These map
1:1 onto the existing `--quality` values `draft`/`standard`/`high` (§7); the word
"fine" is the user-facing label for `high` to match Bambu's own profile naming
(`0.12mm Fine`).

### Step 5 — supports

`Confirm("Does the model have big overhangs that need supports?", default=False)`.
Sets `supports=True, support_type="tree"` when yes. This is the only geometry
question we ask; everything else is preset.

### Step 6 — prepare + preview (the work happens here, before any confirm)

Say `Downloading and slicing — this can take a minute or two...` then run the
**download and slice halves** of the pipeline via existing commands (not a
reimplementation):

1. Build `workdir = tempfile.mkdtemp(prefix="bambu-go-")` (clean up in `finally`,
   honoring the existing `BAMBU_KEEP_WORKDIR=1` escape).
2. URL source: call `commands.cmd_download` with
   `argparse.Namespace(url=..., output=workdir, name=None, max_download_mb=DEFAULT_MAX_DOWNLOAD_MB, json=False, progress=True)`
   (exactly the shape `_run_job` builds for `steps.get_download()`). ZIP results:
   the download layer already extracts a model member.
3. If the resulting file's extension is in `SLICEABLE_EXTENSIONS`: call
   `commands.cmd_slice` with `_slice_args_for_job(path, preset_namespace, workdir)`
   (`job/predict.py`) so slicing is bit-identical to what `plate job` would do.
   A `.3mf`/`.gcode` skips this (and the preview shows `estimate unavailable
   (pre-sliced file)` for `.gcode`; for a downloaded `.3mf` still try §6 parsing —
   works if it is a sliced 3MF, degrade gracefully if not).
4. Parse `read_3mf_estimate(sliced_path)` (§6) and render a rich table:

   ```
   Model      benchy.stl (from printables.com)
   Printer    Bambu Lab P1S, 0.4mm nozzle
   Material   PLA  ·  Quality: standard (0.20mm)  ·  Supports: no
   Est. time  1h 42m          Filament: ~13 g
   ```

   Estimate parse failure is a warning line (`Couldn't read a time estimate from
   the sliced file`), never a flow-stopper.

Error paths in this step surface as `BambuError` from the underlying commands;
catch, show `exc` message (these are already user-actionable — profile hints,
SSRF messages, size caps), and exit with `exc.exit_code`. On slice failure
specifically, add `Try: plate slice <downloaded-file> -v` as the next command.

### Step 7 — confirm and print

`Confirm("Start this print now?", default=False)`. **Default is No — this is the
deliberate-action gate.** The wizard's Yes is the moral equivalent of `--confirm`
(a human deliberately answered a direct question; that is exactly what the flag
exists to prove — consistent with the SECURITY.md framing that `--confirm` guards
accidents, not authorization).

- Yes → call `_run_job` (or `commands.cmd_job`) on the **sliced local `.3mf`**
  with `argparse.Namespace` from `_add_job_arguments` defaults +
  `source=sliced_path, confirm=True` — it takes the `PRINT_READY_EXTENSIONS`
  branch: name-safety check, upload, print, done. Then print the send-off:
  `Printing. Watch it live: plate status --monitor` and exit 0.
- No → offer `Confirm("Upload it to the printer anyway (start later from the
  screen or with 'plate print')?", default=False)`; yes → same call with
  `confirm=False` (prints the existing `uploaded_not_printed` guidance); no →
  `Nothing sent. Sliced file kept at <path>` (in that case move it out of the
  temp workdir into cwd, or don't clean up — implementer's choice, but never print
  a path that's about to be deleted) and exit 0.

## 6. Estimate extraction (`bambu_cli/slicer/estimate.py`)

Orca-sliced Bambu `.3mf` files are OPC zips (see `_is_valid_sliced_3mf`) carrying:

- `Metadata/slice_info.config` — XML; per-plate `<metadata key="prediction"
  value="6120"/>` (seconds) and `<metadata key="weight" value="13.05"/>` (grams).
  **Primary source.**
- `Metadata/plate_N.gcode` — header comments (`; model printing time: 1h 42m ...`,
  `; total filament weight [g] : ...`). **Fallback** when `slice_info.config` is
  absent/unparseable; read only the first ~64 KB of the member.

API:

```python
@dataclass(frozen=True)
class Estimate:
    seconds: int | None
    grams: float | None

def read_3mf_estimate(path: str) -> Estimate  # never raises; returns Estimate(None, None) on any failure
```

Use `zipfile` + `xml.etree.ElementTree` (both stdlib; note bandit config — no new
suppressions should be needed for parsing a local file we just produced, but if
bandit flags `ET.parse`, prefer `defusedxml`-style hardening comments consistent
with existing style rather than a blanket skip). Format helper
`format_estimate(est) -> str` (`"1h 42m"`, `"~13 g"`, `"unknown"`).

**Verify the exact key names against a real Orca-produced file before relying on
them** — slice one under `--sim`-less local run or take a fixture from a real
slice; do not trust this plan's memory of Orca internals. Ship 2–3 small fixture
`.3mf` files (hand-built zips with minimal members are fine) under
`tests/fixtures/`.

## 7. Preset design ("no slicer knobs")

`interactive/presets.py`, pure data:

```python
QUALITY_PRESETS = {
    "draft":    {"quality": "draft"},     # -> 0.28mm Extra Draft @BBL <model>
    "standard": {"quality": "standard"},  # -> 0.20mm Standard
    "fine":     {"quality": "high"},      # -> 0.12mm Fine
}

MATERIAL_PRESETS = {
    #          filament profile substring   nozzle_temp  bed_temp
    "PLA":  {"filament": "PLA Basic",  "nozzle_temp": 220, "bed_temp": 60},
    "PETG": {"filament": "PETG",       "nozzle_temp": 255, "bed_temp": 70},
    "ABS":  {"filament": "ABS",        "nozzle_temp": 270, "bed_temp": 90},
    "TPU":  {"filament": "TPU",        "nozzle_temp": 230, "bed_temp": 40},
}
```

Rationale for carrying temps: `_create_temp_profiles` **always** overwrites
`nozzle_temperature`/bed-plate temps from `args` (defaults 220/60) — so a naive
"just set `--filament PETG`" would print PETG at PLA temperatures. The preset
table is where material-correct temps live. Values must stay inside
`MIN/MAX_NOZZLE_TEMP_C` / `MIN/MAX_BED_TEMP_C` (`constants.py`) and should be
sanity-checked against the matching Bambu filament profiles during implementation
(read the `@base` JSONs from a real profiles dir and copy their midpoints rather
than trusting the numbers above). *Alternative considered:* skip the temp
override entirely and let the Orca filament profile's own values through — that
requires changing `_create_temp_profiles` to distinguish "user set a temp" from
"argparse default", which is a behavior change to `job`/`slice` proper. Flagged
as Q3; the preset table is the no-risk v1.

`preset_to_job_args(state)` merges: `_add_job_arguments` defaults (source of
truth — build via `build_parser()` parsing `["job", "X"]` or mirror the defaults
in one place with a test pinning them against the parser) + quality + material +
`supports`/`support_type` + `infill=15` (existing default), `pattern="3dhoneycomb"`.
Everything else stays default. **The wizard never exposes:** infill, walls,
speeds, accelerations, seams, ironing, brim, `--set` overrides.

## 8. Testing strategy

Constraints: pytest with `addopts = --cov=bambu_cli --cov-report=term-missing`,
CI coverage floor **83** (Windows is the binding leg — keep new code
platform-neutral), markers `security`/`contract`/`live`/`slow`, contract suite
derives command list from `build_parser()`.

- **Pure units (bulk of coverage):** `presets.py` (mapping tables, namespace
  construction — pin against `_add_job_arguments` defaults so drift fails a
  test), `estimate.py` (fixture 3mfs: full metadata, gcode-header-only, corrupt
  zip, missing members → `Estimate(None, None)`), `format_estimate`.
- **Session state machine:** inject a fake prompt layer (list of scripted
  answers) into `session.py` — this is why prompts live behind `prompts.py`.
  Test: happy path assembles the right namespaces and calls download/slice/job
  fakes in order (inject via the same style as `JobSteps`); decline-at-confirm
  → upload-only offer; decline-both → nothing called, exit 0; Ctrl-C at each
  step → exit 5; unconfigured printer → setup offer; slicer missing → exit 1
  before any prompt; bad URL re-prompt ×3 → exit 3.
- **Interactive prompt functions themselves:** follow the existing convention —
  `# pragma: no cover -- interactive prompt` on the thin `rich.prompt` calls
  only. Keep that layer tiny so the pragma covers almost nothing.
- **End-to-end under `--sim`:** scripted-stdin run of `plate go <local-stl> --sim`
  with a fake slicer step (or a real slice marked `slow` if CI images carry
  OrcaSlicer — they don't; use the fake). Asserts the full flow reaches
  "printed" against the simulated printer.
- **Contract:** `go.json` schema exists, `$id` matches filename, `docs/api.md`
  row present — the existing derived suite enforces this once the subcommand
  lands; also add an explicit test that `plate go --json` emits the error
  envelope and exits 5, and that non-TTY stdin aborts.
- **Docs consistency:** `tests/test_docs_consistency.py` exists — check what it
  pins (README/manual command lists) and update accordingly.
- Run `ruff` (`select` includes `I`, `UP`, `SIM`; line length 120) and
  `mypy -p bambu_cli` — new package is auto-included by the mypy config; write
  it fully typed from the start.

## 9. Phased breakdown

### Phase 0 — release 0.4.0 (gate, not part of this feature)
Follow `docs/releasing.md`; drop the `.dev0` suffix; tag; bump `main` to
`0.5.0.dev0`. **Acceptance:** PyPI shows 0.4.0; `main` carries `0.5.0.dev0`.

### Phase 1 — foundations (pure code, no UX)
`slicer/estimate.py` + fixtures + tests; `interactive/presets.py` + tests
(including the parser-defaults pin and temp-bounds checks).
**Acceptance:** new tests green cross-platform; coverage ≥ 83; `mypy`/`ruff`
clean; no behavior change to any existing command (full suite untouched).

### Phase 2 — the wizard
`interactive/prompts.py`, `interactive/session.py`, `cmd_go` export, `go`
subparser, `LOCAL_COMMANDS` entry, `go.json` schema, `docs/api.md` row, session
tests, `--sim` end-to-end test.
**Acceptance:** `plate go --sim` completes URL→"printed" against the fake
printer with scripted answers; every error path in §5 exits with the specified
code; `plate go` with piped stdin refuses; contract + docs-consistency suites
green; coverage floor holds.

### Phase 3 — polish
AMS-aware material default (read `status` data; on MQTT failure fall back
silently to plain prompt — never block the wizard on status); help-epilog hint
in `build_parser()`; `README.md` + `docs/manual.md` sections; CHANGELOG entry;
optionally a `docs/*.tape` VHS demo matching the existing demo GIFs.
**Acceptance:** with `--sim`, material prompt defaults to the loaded AMS slot's
material; docs suites green.

Phases 1 and 2 are separate PRs; Phase 3 can ride with 2 or follow.

## 10. Risks / notes for the implementer

- `cmd_download`/`cmd_slice` are invoked here outside `_run_job` for the first
  time in-process back-to-back; they communicate some state via
  `utils._LAST_DOWNLOAD_PAYLOAD` / `_LAST_ERROR_PAYLOAD` — reset them the way
  `_run_job` does before each call.
- Windows: prompts must survive cp1252 consoles — `bambu.py` already
  reconfigures stdout/stderr to UTF-8; avoid emoji in *prompt* strings anyway
  (rich handles it, but keep parity with `setup`'s plain prompts).
- The slice in step 6 runs before any confirm — that's by design (preview needs
  it) but means minutes of CPU before the user can say no. Message it honestly
  (`this can take a minute or two`).
- Don't leak the temp workdir path into the final success message unless the
  file survives (§5 step 7).

## 11. Resolved decisions (settled 2026-07-27 — these are binding for implementation)

Decision principle applied throughout: **clean, professional, secure,
easy-to-use** — in that order when they conflict, with "secure" never traded away
for "easy".

### Q1 — bare `plate` → **launch the wizard when stdin is a TTY; print help and exit 5 when not.** ✅ decided

Rationale: the target user is someone who just installed `plate` and typed it to
see what happens; landing in a guided flow is the single highest-leverage
ease-of-use win in this feature. The script-compat objection is answered by the
TTY gate — CI, pipes, `subprocess`, and `plate | less` all keep today's exact
behavior (help on stderr, `EXIT_COMMAND_ERROR`). No non-interactive caller can
observe a change, so this is a zero-breakage default flip.

Implementation notes: the TTY check lives in `cli.main()`'s no-subcommand branch,
gated on `sys.stdin.isatty() and sys.stdout.isatty()` (require *both* — a TTY
stdin with redirected stdout is a script pattern too). Any explicit global flag
that implies machine use (`--json`) forces the help path. **Phase 2/3 work — do
not touch `cli.main()` in Phase 1.**

### Q2 — command name → **`go`.** ✅ decided

Short, unclaimed, no collision with `job`/`print`/`send`, and reads well in the
one-line pitch (`plate go`). `wizard` is longer and sounds like configuration
(which `setup` already owns). Locked; renaming after 0.5.0 is a deprecation cycle.

### Q3 — temperature ownership → **material presets own their temperatures.** ✅ decided

This is not merely a preference: `_create_temp_profiles` unconditionally
overwrites `nozzle_temperature` / bed-plate temps from `args`, whose argparse
defaults are PLA's 220/60. A user who picks PETG and gets a 220 °C nozzle with a
60 °C bed gets a failed print at best. That is a **correctness and printer-safety
bug**, and the preset table is the fix for the `go` path.

Binding constraints for the implementer:
- Temps in `MATERIAL_PRESETS` are the source of truth for the wizard and must be
  copied from the real Bambu `@base` filament profiles in a live `profiles_dir`,
  not from this document's from-memory numbers. Record where each number came
  from in a comment.
- A test must assert every preset temp is inside `MIN/MAX_NOZZLE_TEMP_C` and
  `MIN/MAX_BED_TEMP_C` (`constants.py`), and that no preset is silently missing a
  temp (a `None` would fall back to the PLA default — the exact bug being fixed).

Deferred, not dropped: teaching `_create_temp_profiles` to distinguish "user set a
temp" from "argparse default" (sentinel default → defer to the Orca profile) is
the cleaner long-term fix and would benefit `slice`/`job` users too. It is a
behavior change to shipped commands, so it does **not** ride in this feature —
file it as a separate issue once Phase 2 lands.

### Q4 — multi-printer → **one config, confirm the printer, for v1.** ✅ decided

Matches today's data model exactly (one `Settings`, one `printer_ip`); inventing
named profiles inside a wizard PR would be a config-schema change wearing a UX
costume. The confirm step (§5 step 2) means the user always sees which machine is
about to receive a print — that is the safety property that matters. Named
printer profiles are a separate future feature touching `config.py` + `setup`.

### Q5 — automation escape hatch → **hard-require a TTY; no `--yes`, no scripted stdin.** ✅ decided

Delete the `--yes` line from the §3.1 subparser sketch. Reasons, in order:
1. **Security.** `--yes` on a command whose last step starts a physical print is a
   footgun and an attractive nuisance in copy-pasted one-liners. `job --confirm`
   already exists for deliberate automation and is the documented path.
2. **Clean surface.** A hidden (`SUPPRESS`-ed) flag that only tests use is a
   test-shaped hole in the product; the injectable `prompts.py` layer (§4) gives
   tests everything they need without a user-visible flag.
3. The non-TTY abort message already points at the right tool:
   `plate go is interactive; use 'plate job <url> --confirm' for scripts.`

Consequence for §8: the `--sim` end-to-end test drives the wizard through the
injected prompt layer, **not** by piping stdin.

### Q6 — copies → **no copies prompt in v1.** ✅ decided

Every prompt is a tax on the "URL in, plastic out" promise, and the honest answer
for multiple copies is plate arrangement, which this feature explicitly does not
do (a naive copies flag would just print the same object N times sequentially,
which the printer screen already does better). Six questions max. Revisit only if
users ask.

### Q7 — estimate accuracy → **yes, partial estimates are fine; degrade per-field.** ✅ decided

`Estimate` already models `seconds` and `grams` independently as `int | None` /
`float | None`. Render whichever fields parsed and omit the others; never let a
parse failure block the flow (§5 step 6 already says this). Show
`Est. time 1h 42m` alone rather than suppressing the whole row, and print
`estimate unavailable` only when *both* fields are `None`. A wrong-looking number
is worse than a missing one — if a parsed value is implausible (non-positive, or
time > 30 days / weight > 10 kg), treat it as unparsed.
