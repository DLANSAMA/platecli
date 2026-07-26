# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- `camera_direct_only` config key (default `false`): when set, disables the Docker/RTSP streamer fallback so any direct port-6000 grab failure aborts instead of silently falling through to the unauthenticated streamer. Closes the remaining camera fallback residuals noted in SECURITY.md. X1-series printers still need the streamer; unset `camera_direct_only` for them.
- Published JSON schemas for the last four `--json` commands that lacked them:
  `upload.json`, `files.json`, `stop.json`, `setup.json`. README has claimed
  "every command speaks `--json` with published schemas" for a while; that is now
  true, and enforced rather than asserted.

### Changed

- Schema coverage is derived instead of hand-maintained. The contract suite reads
  the subcommand list from `build_parser()` and fails if a command has no schema,
  if a schema's `$id` disagrees with its filename, if a schema file is
  unreferenced, or if the `docs/api.md` table omits one. The previous
  hand-written list had already drifted — it omitted `status.json`.
- `status_event.json` and `version.json` gained the `$id` and `title` fields the
  other schemas already carried, so every published schema is self-identifying.

## [0.3.0] - 2026-07-26

### Added
- `tests/fixtures/cube.stl`, a 20 mm ASCII-STL cube — the only model committed
  to the repo (`*.stl` / `*.3mf` remain gitignored, with a narrow negation for
  `tests/fixtures/`). Gives `job` / `print` something to exercise by hand
  without dropping a stray model into the tree.
- AGENTS.md now states the stdout/stderr contract for `--json` (envelope on
  stdout, Rich logs and `-v` diagnostics on stderr; parse stdout only) and warns
  agents to confirm whether they are running an installed `plate` or the
  checkout before reporting behaviour.
- `docs/troubleshooting.md`: a symptom-keyed troubleshooting guide organised
  around the errors the CLI emits (MQTT/FTPS connectivity, cert-pin mismatches,
  OrcaSlicer and profile paths, camera/Docker, SSRF-blocked downloads, config
  location). Linked from the README, the user guide, and the bug-report
  template; ships in the sdist.
- `tests/test_docs_links.py`: checks that cross-doc GitHub links resolve to real
  repo paths and that every in-page anchor in the troubleshooting guide matches a
  real heading.
- README: a "How it compares" section describing adjacent cloud-free Bambu
  projects and when to use them instead.
- Printer error codes are now shown in hex as well as decimal (e.g. `Print failed
  with error code 83935248 (hex 0x0500C010)`), and JSON error envelopes gain an
  additive `printer_error_code_hex` field. Bambu documents these codes in hex.
- `tests/test_docs_consistency.py`: guards against version drift in README /
  `docs/api.md` / the bug-report template, and against `docs/manual.md` sections
  missing from its Contents list.
- `docs/manual.md` documents that Printables downloads are performed on the
  user's behalf, subject to Printables' terms and each model's own licence.

### Changed
- The in-development version now carries a `.devN` suffix (`0.3.0.dev0`).
  Previously `main` advertised the same version as the last PyPI release while
  behaving differently — `plate --version` could not distinguish a source build
  from the release, which made bug reports ambiguous. Drop the suffix when
  tagging.
- `docs/manual.md`: the OrcaSlicer section now covers per-OS installation, the
  exact auto-detected default paths, and how to override them.
- platecli now identifies itself honestly when talking to Printables:
  requests carry `User-Agent: platecli/<version> (+https://github.com/DLANSAMA/platecli)`
  instead of impersonating Chrome, and the forged `Origin`/`Referer` headers
  are gone. Verified against the live Printables GraphQL API. Generic
  user-supplied download URLs keep a browser-compatible User-Agent for CDN
  compatibility, now with the honest `platecli/<version>` token appended.
- Outbound HTTP is throttled to at most one request per second per host, and
  `429`/`503` responses are retried while respecting `Retry-After` (clamped to
  30 s, at most two retries).
- README: stated the OrcaSlicer and Python 3.9+ prerequisites, qualified non-P1
  printer support as best-effort, clarified that `plate snapshot` grabs P1/A1
  cameras directly while X1-series needs the Docker streamer, and added
  unattended-printing safety and maintenance-expectations sections.
- Removed hardcoded version strings from README, `docs/api.md`, and the bug-report
  template; the version is single-sourced from `pyproject.toml`.
- Refreshed measured test/coverage numbers in `docs/quality-roadmap.md` and
  `docs/test-backlog.md` (758 tests, 84.03%).
- `AGENTS.md` retitled to the public product name.
- `docs/api.md`: the `pause.json` / `resume.json` bullets now describe both the
  success and the `confirmation_required` shape, matching the dual schemas.
- CI runs weekly on a schedule (external drift: firmware, Printables API,
  OrcaSlicer, new CVEs) and pins ruff/mypy/bandit/pip-audit versions.
- Dependabot config added for GitHub Actions and uv dependencies.
- New `docs/releasing.md` with the yank/rollback runbook.

### Changed (BREAKING)
- `pause` and `resume` now require `--confirm`, matching `stop`/`print`/`gcode`/`delete`.
  Pausing mid-print parks a hot nozzle over the part; resuming an abandoned print
  restarts motion unsupervised. Without `--confirm` they now refuse with exit code `5`
  and (with `--json`) `"status": "confirmation_required"`. Scripts calling
  `plate pause` / `plate resume` must add `--confirm`.
- `print` without `--confirm` now exits `5` (`EXIT_COMMAND_ERROR`) instead of `0`.
  The JSON payload is unchanged (`"status": "confirmation_required"`). This aligns it
  with `stop`/`gcode`/`delete` and with the exit-code table in `docs/api.md`; a script
  that treated exit `0` as "print started" was already silently wrong.

### Fixed
- **`scripts/bambu.py` — the documented "run from a source checkout without
  installing" entrypoint — never worked.** Python puts the script's own
  directory (`scripts/`) on `sys.path`, not the repo root, so it died with
  `ModuleNotFoundError: No module named 'bambu_cli'` on any clean checkout. It
  now prepends the repo root before importing. This mattered more than it looks:
  with the shim broken, the only way to run the CLI was an installed `plate`,
  which is usually an older release than the checkout — so contributors and
  agents were reporting the *released* behaviour as if it were the source's.
- **`scripts/clean_artifacts.py` no longer deletes the developer's `.venv`.**
  The script appended `.venv` to its removal list whenever `GITHUB_ACTIONS` was
  unset — i.e. on every local run — and removed it with `ignore_errors=True`. On
  Windows any locked file (a running `python.exe`) left a *partially* deleted
  virtualenv, after which every `uv run` failed with `No pyvenv.cfg file` and
  exit code 106, recoverable only by hand. Nothing warned about this, and the
  name "clean_artifacts" does not suggest destroying the dev environment. The
  deletion also served no purpose: `tests/release_readiness_smoke.py` skips any
  path containing `.venv`, so a present `.venv` was never flagged. `.venv` is now
  opt-in via `--venv` / `--all`, confirmed interactively (`-y` to skip), and the
  walk no longer descends into `.git`, `.venv`, `venv`, `.claude` or
  `node_modules`. Removal is verified instead of best-effort: a surviving path is
  reported with recovery steps and a non-zero exit. Adds `--dry-run`, a docstring,
  `argparse --help`, and `tests/test_clean_artifacts.py`. CI's invocation is
  unchanged and never passed `--venv`.
- **`job` now validates print options on every path, not just under `--confirm`.**
  `_run_job` only checked `--use-ams` / `--ams-mapping` when `--confirm` was passed
  and `--upload-only` was not, so the two paths agents use as a pre-check both
  reported success for flags that would later be rejected. `job --dry-run
  --ams-mapping 0,1` returned `dry_run_local_skipped` even though the same flags
  fail without `--dry-run` — inconsistent with `--infill`, which was already caught
  in dry-run. Worse, `job --upload-only --ams-mapping 0,1` uploaded and then handed
  back a `next_command` of `["print", …, "--ams-mapping", "0,1"]` that `print`
  rejects with exit `5`, so an agent following the documented handoff hit a
  guaranteed failure *after* a real upload. Validation now runs for any supplied
  print option and still passes when none are given.
- **`status` no longer returns a partial MQTT delta as if it were a full state
  snapshot.** The printer publishes incremental updates on the `report` topic and
  only sends the complete state in reply to `pushall`, but `status` returned
  whichever message arrived first. Mid-print that was intermittently a single
  `nozzle_temper` reading, so `plate --json status` emitted a `printer` object
  with no `gcode_state`, `mc_percent`, or `layer_num` — a random `KeyError` for
  any agent reading those fields. Report messages are now merged into one
  accumulated state and `status` keeps waiting (re-issuing `pushall` on each
  retry, honouring the existing timeout and retry count) until the state is
  complete. If only deltas ever arrive, it now fails with a clear error naming
  the missing keys and exit code `6` instead of emitting an incomplete object.
  `doctor` and `print --dry-run`, which use the reply only as a liveness probe,
  still accept the first message.
- `status --monitor` merges deltas the same way, so a temperature-only update no
  longer streams as `"gcode_state": "UNKNOWN"` at 0% mid-print.
- **`setup` and `--migrate-access-code` no longer fail outright when the config
  directory is behind filesystem virtualization.** Windows MSIX/AppContainer
  redirection of `%APPDATA%` makes `os.replace` report `ERROR_NOT_SAME_DEVICE`
  (`WinError 17`) even for two paths in the *same* directory, so every config and
  access-code write raised `Could not migrate access code: [WinError 17] ...` and
  platecli could not persist configuration at all on such a machine. The atomic
  temp-file-plus-rename path is unchanged and still the default; only on that
  specific errno (`EXDEV` / winerror 17) does it fall back to writing the file in
  place, and it warns that the write is not crash-safe. The fallback writes the
  target directly rather than copying the temp file over it, so a secret is never
  left in a second location. Reproduced and verified against a real redirected
  `%APPDATA%\bambu`.
- **OrcaSlicer auto-detection now finds stock Windows installs.** The current
  Windows installer lays the binary down as `orca-slicer.exe`, but detection only
  probed `OrcaSlicer.exe`, so `plate setup` missed every default install and users
  had to set `orca_slicer` by hand. Both names are now probed under each install
  root (hyphenated first), plus a `PATH` lookup for custom locations. Found on a
  real Windows box with OrcaSlicer 2.4.2.
- When no OrcaSlicer exists anywhere on the machine, the `orca-slicer` /
  `profiles-dir` errors from `preflight`, `config validate`, and `slice` now append
  a ready-to-run install command for the host platform (`winget` / `brew` /
  `flatpak`) instead of only suggesting a config edit — there is nothing to point
  the config at until something is installed. When an install *is* present, the
  existing "detected at <path>" hint still wins.
- `tests/live_printer_smoke.py` now decodes CLI subprocess output as UTF-8 instead of
  the platform default. On a Windows box with a cp1252 codepage the harness's reader
  threads died with `UnicodeDecodeError` on the CLI's emoji output, losing stdout/stderr
  while the run still reported success. `tests/agent_cli_smoke.py` already carried this
  fix; the live harness had been missed.
- README, AGENTS.md, SECURITY.md, docs/api.md, and docs/manual.md no longer claim a
  universal `--confirm` gate that `pause`/`resume` did not implement; `--confirm` is
  now documented as a deliberate-action gate rather than an authorization boundary.
- `docs/schemas/pause.json` / `resume.json` accept the `confirmation_required` status.
- Filenames containing square brackets (e.g. `part[v2].stl`, common in Printables model titles) no longer crash the CLI or get silently truncated in log output — the Rich log handler no longer parses log text as markup.
- `--json` now always writes a parseable error envelope to stdout: the JSON envelope is emitted before the human-readable log line, so a logging failure can no longer leave stdout empty.
- The envelope-before-log ordering now covers the whole package (`commands/`,
  `camera`, `download/`, `slicer/`, `setup_cmd/`), not just `cli.py`. Every log call on
  an error path now also routes through the shared `logging_utils.safe_log_error`, which
  degrades to a bare stderr write instead of propagating, so a handler that raises while
  rendering a user-controlled string can no longer swallow the `--json` envelope or
  replace the real `BambuError` with a logging traceback.
- `plate doctor`'s reported `camera_snapshot_note` (and `camera.py`'s snapshot
  docstring) claimed P1P/P1S snapshots go through the BambuP1Streamer container.
  It is the reverse: P1/A1-class cameras are captured directly with no Docker,
  and the container is the X1-series fallback.
- `plate preflight` now says what to do when `orca_slicer` / `profiles_dir` are not
  configured, instead of printing `OrcaSlicer not found at` with a blank path.
- Contributor setup uses `uv sync --extra test`, so the documented test command works
  on a fresh clone (`uv sync` alone does not install pytest).
- Config and access-code writes are now atomic (temp file + fsync + rename), so a crash
  mid-write can no longer truncate `config.json` or the access-code file. `config.json`
  also keeps a `.bak` of the previous file; access-code files are written atomically at
  0600 but without a backup copy (fewer copies of credentials on disk). Mode stays `0600`.
- `plate slice` now says what to do when `orca_slicer` is not configured at all, instead
  of reporting `OrcaSlicer not found at ` with a blank path.
- Device, file, print and G-code commands now emit the `--json` error envelope before
  writing the human-readable log line, so a failure in the logging layer can no longer
  leave stdout without a parseable envelope.
- **A UTF-8 byte-order mark on `config.json` silently voided the entire config.**
  PowerShell's `Set-Content -Encoding utf8`, Notepad's "Save as UTF-8", and most
  Windows editors write a BOM. `json.load` then failed, the error was swallowed,
  and every command reported `Printer IP is not configured. Please run 'plate
  setup' first.` — advice that would have overwritten the config the user was
  trying to repair. Config files are now read as `utf-8-sig`, which handles BOM
  and BOM-less files identically.
- A config that exists but fails to parse is no longer reported as *missing*.
  `load_config` always logs the real parse error, and `plate preflight` now says
  the file exists and could not be read rather than `Config not found at …`.
- `plate preflight`'s Docker check is now model-aware: it no longer warns P1/A1
  owners that "camera snapshots will be unavailable" when those printers capture
  directly over the printer's TLS camera port. (The matching `doctor` note was
  already corrected; this was the remaining copy of the same wrong claim.)
- `bambu_cli/bambu.py` looks up `reconfigure` via `getattr`, fixing the sole
  outstanding mypy error (`TextIO` has no `reconfigure`) without changing the
  Windows encoding hardening it performs.
- `.gitignore` now ignores `result.json`, the slice-result file OrcaSlicer's CLI
  drops in the working directory on every `plate slice`. The existing entry read
  `results.json` (plural) and never matched, so the file showed up as untracked
  in every working tree after a slice.

### Security
- **`plate doctor` no longer prints your printer's LAN IP or full certificate
  SHA-256 in its human output by default.** doctor output is routinely pasted
  into issue reports and recorded into the README/PyPI demo GIFs. The IP now
  reads `<redacted>`, and once a cert is pinned doctor prints a hex-free match
  confirmation instead of the fingerprint. Pass `-v`/`--verbose` to see both.
  The full fingerprint is still printed when the cert is *not* yet pinned (you
  need it to pin), the MQTT-failure message still echoes the configured IP so a
  typo is diagnosable, and the `--json` contract is unchanged.
- The bug-report template now asks for `plate doctor --json` (which redacts the
  LAN IP) instead of the human output.
- All GitHub Actions are pinned to immutable commit SHAs, enforced by
  `tests/ci_workflow_smoke.py`. Notably `pypa/gh-action-pypi-publish` was
  tracking the mutable `release/v1` branch in the job that holds the PyPI
  trusted-publishing `id-token: write` permission.
- Releases now re-run the full CI matrix on the tagged commit before publishing,
  so a tag on a red commit can no longer reach PyPI.
- The four demo GIFs committed to the repo (`docs/demo-{dark,light}.gif`,
  `docs/doctor-{dark,light}.gif`) previously had the maintainer's printer LAN IP
  and full certificate SHA-256 **rasterised into the frames**, where no
  code-level redaction could reach them. The GIFs are embedded in README.md and
  docs/manual.md and served from raw.githubusercontent.com, making them the most
  widely-read copy of that data. Re-recorded against the doctor redaction from
  #61; the fingerprint now shows a hex-free match confirmation and the IP reads
  `<redacted>`.

## [0.2.2] - 2026-07-24

### Added
- `plate snapshot --unique`: timestamped output filenames
  (`printer_snapshot_20260724T195820Z.jpg`) so repeated captures never
  overwrite each other; composes with `--output` by inserting the stamp
  before the extension. Designed for AI-agent workflows.
- Snapshot `--json` output now includes `captured_at` (ISO-8601 UTC) and
  `sha256` (digest of the JPEG bytes) so callers can verify a capture is
  genuinely new. Additive fields; `docs/schemas/snapshot.json` updated.
- Camera-snapshot guidance for agents in `AGENTS.md` and a Camera snapshots
  section in `docs/manual.md`.

## [0.2.1] - 2026-07-24

### Fixed
- **Slice temperature safety now covers process-section overrides**: nozzle/bed
  temperature keys passed via `--set` or the `"process"` object of
  `--settings-json` are validated against the same printer-safety bounds as
  filament overrides (previously only the filament path was checked).
- `plate doctor` honors `--network-timeout` for its MQTT, FTPS, and version
  probes (previously hardcoded 5 s with silent retries taking ~23 s), and no
  longer retries the MQTT check.
- Deprecation and setup messages referenced the pre-rename `bambu setup`
  command; they now correctly say `plate setup`.
- The headless (non-TTY) setup error now includes a copy-pasteable
  non-interactive example command.

### Added
- `docs/schemas/status.json`: published JSON Schema for static
  `plate status --json` output (the monitor stream already had
  `status_event.json`), validated in contract tests.
- Community files: code of conduct, issue forms (bug report asks for
  `plate doctor` output), and a pull-request template.

### Changed
- README restructured into a concise landing page; full technical
  documentation moved to `docs/manual.md` (ships in the sdist, linked from
  the PyPI sidebar via the new `Documentation` project URL).
- Quickstart examples use a real Printables model (3DBenchy) instead of a
  placeholder URL.
- Repo hygiene: `pytest.ini` and `.coveragerc` folded into `pyproject.toml` (`[tool.pytest.ini_options]`, `[tool.coverage.*]`); removed the `.jules/` bot-notes directory. Test/coverage behavior unchanged; sdists still carry the full test config via `pyproject.toml`.

## [0.2.0] - 2026-07-23

### Changed
- **Project renamed to `platecli`; the installed command is now `plate`** (was
  `bambu-cli`, published as `bambu-local-cli`). The rename removes the vendor
  name from the project branding; platecli remains an unofficial tool for
  Bambu Lab printers and is not affiliated with Bambu Lab. The internal Python
  package (`bambu_cli`), environment variables (`BAMBU_*`), and config path
  (`~/.config/bambu/`) are unchanged, so existing configs keep working —
  only the command you type changes. The old PyPI release `bambu-local-cli`
  0.1.0 is yanked to point users at `platecli`.

## [0.1.0] - 2026-07-18

Initial development version: LAN-mode printer control (MQTT/FTPS), one-shot `job`/`send` orchestration, OrcaSlicer integration, guided setup with mDNS discovery, camera snapshots, SSRF-safe downloads with Printables support, simulation mode, and agent-facing `--json` output.

### Documentation
- Full doc truth pass (2026-07-17 audit): aligned **AGENTS.md**, **CONTRIBUTING.md**,
  **SECURITY.md**, **README.md**, **docs/api.md**, **docs/quality-roadmap.md**,
  **docs/test-backlog.md**, live/mutation docs. Corrected stale claims (mypy residual
  blocklist, ≥98% coverage). Expanded config reference in README; security known
  limitations (camera Docker bind, pin soft-fallback, HTTP downloads, pause/resume).
  Architecture grade noted as **A−** until domain→`cli` helper extraction lands.
  Honest metrics: ~618 non-live tests, ~82% coverage, CI floor 81, target 92 for A+/1.0.
- **Packaging doc policy:** PyPI sdist ships only user/agent docs (`README`, `AGENTS`,
  `SECURITY`, `CHANGELOG`, `docs/api.md`, `docs/schemas/*`). Contributor planning docs
  (`CONTRIBUTING`, quality-roadmap, test-backlog, mutation/live-smoke) stay GitHub-only;
  enforced by `MANIFEST.in` + package_contents forbidden list. Wheel remains runtime-only.

### Security
- **Camera streamer now binds loopback by default.** `camera_port` defaults to `127.0.0.1:1985:1984` (was `1985:1984`), so the BambuP1Streamer container no longer publishes the unauthenticated printer camera feed on all host interfaces (`0.0.0.0`). Set `camera_port` to `0.0.0.0:1985:1984` to deliberately restore LAN access. An **already-running** container keeps its old binding until recreated — run `docker rm -f bambu_camera` (the CLI now warns when it detects a running container still bound to a non-loopback interface). `camera_port` is also validated, and the localhost stream URL is now derived correctly from host-qualified specs (`[HOST:]HOSTPORT:CONTAINERPORT`).

### Fixed
- **Camera pin fail-open:** a `cert_fingerprint` **mismatch** during the direct P1/A1 camera grab now hard-aborts `snapshot` (exit 2) instead of being swallowed by the broad fallback handler and silently retried through the Docker streamer, which connects without honoring the pin. A *missing* pin still legitimately falls through to the streamer (X1 path).
- **Camera socket fd leak:** the direct-grab TLS socket is now closed via the wrapped `SSLSocket` (which owns the fd after `wrap_socket` detaches it) rather than the detached raw socket, so a successful snapshot no longer leaks an fd / emits a `ResourceWarning`.
- Global `--json` placed *before* the subcommand is now honored by `status`, `light`, `pause`, and `resume` (they re-declared `--json` with an implicit `False` default that clobbered the global flag, so `bambu-cli --json status` silently emitted nothing).
- `--allow-private-ips` now actually enables private/LAN downloads for that invocation (it was parsed but never applied to runtime settings).
- `load_access_code` and domain handlers raise structured `BambuError` / `abort` instead of calling `sys.exit` (process exit is CLI entry only).

### Changed
- Release workflow now creates the GitHub Release only after the PyPI publish succeeds (build → publish → release job chain).
- Sdists include `pytest.ini` so the shipped test suite runs with the project's marker/coverage config.
- Simulation mode (`--sim status`) reports representative bed/nozzle targets, fan speed, and WiFi signal instead of `?` placeholders.
- The missing-config error now names the exact command: ``Please run `bambu-cli setup` first.``
- `VERSION` is resolved from package metadata / `pyproject.toml` only (no duplicate string in `constants.py`).
- MQTT status-monitor teardown no longer uses a bare `except:`.
- Removed `@mockable` / test-awareness indirection from production code.
- CI coverage floor enforced at **81%** (`--cov-fail-under=81`, raised from 79; multi-OS minimum — Linux ~82.3% / Windows ~81.9% branch total); single pytest path; blocking purity greps for `sys.exit` / `@mockable` / Mock branches. (Further ratchet toward the 92% A+ target remains Phase C work in `docs/quality-roadmap.md`.)
- Package renamed to `bambu-local-cli` for PyPI publication (the `bambu-cli` name on PyPI belongs to an unrelated project). The installed command remains `bambu-cli`.
- Wheels no longer bundle non-runtime files (`README.md`, `AGENTS.md` inside the package).

### Added
- **Full-surface slicer overrides.** `slice` (and `job`/`send`) accept `--set KEY=VALUE` / `--set-filament KEY=VALUE` (repeatable) and `--settings-json '{"process":{…},"filament":{…}}'` to override **any** of the 176 OrcaSlicer process/filament settings, not just the ~17 with dedicated flags. Unknown keys warn (with a "did you mean" suggestion) but still pass through. Temperature overrides are re-validated against the printer-safety bounds so `--set-filament nozzle_temperature=999` is refused.
- **Slicer setting discovery:** `slice --list-settings [--json]` dumps every settable process/filament key with an example value — the agent-facing way to learn the override vocabulary.
- Named slicer convenience flags for the common tuning knobs (sugar over the generic override machinery), each verified against a real slice: `--layer-height`, `--first-layer-height`, `--brim`, `--speed`, `--seam-position {nearest,aligned,back,random}`, `--ironing {none,top,topmost,solid}`, `--support-threshold`, `--fan-speed`, and `--flow-ratio`.
- JSON schemas under `docs/schemas/` and contract tests in `tests/contracts/`.
- `docs/quality-roadmap.md` scoreboard and residual coverage policy.
- TLS pin suite (`tests/test_tls_pinning.py`); SSRF / redirect residual coverage; security + contract pytest markers.
- PyPI trusted publishing on tagged releases (`v*`).
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, Changelog project URL.

### Tests
- CLI e2e coverage for `--allow-private-ips` wiring into `RuntimeContext` / netsafety.
- MQTT + FTPS certificate fingerprint pin match/mismatch (and deferred-handshake) suite.
- Expanded unit coverage for netsafety handlers, setup helpers, slicer pure paths, wizard guided flows.

### Removed
- `requirements.txt`, which duplicated the `dependencies` already declared in `pyproject.toml`. Install with `uv pip install -e .` (or `pip install platecli`) instead.

[Unreleased]: https://github.com/DLANSAMA/platecli/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/DLANSAMA/platecli/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/DLANSAMA/platecli/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DLANSAMA/platecli/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DLANSAMA/platecli/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DLANSAMA/platecli/releases/tag/v0.1.0
