# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-08-26

### Added

- **`plate tui` — a full-screen terminal UI** (optional extra:
  `pip install 'platecli[tui]'`). A live dashboard (printer state, temperatures,
  progress, AMS trays), a guided prepare screen (source validation, material and
  quality presets with the AMS-loaded filament pre-selected, supports, slice +
  time/filament preview), an explicit print-confirmation dialog (start / upload
  only / cancel), and a live job monitor that follows a print to its terminal
  state. It is a front-end over the existing pipeline — source validation, AMS
  detection, download/slice, and the `job` request are the very same shared code
  `plate go` runs, so the two cannot drift. Safety is unchanged: a print starts
  only from the confirm dialog, upload-only leaves the file unstarted,
  cancelling preserves the sliced file and says where it is, leaving the monitor
  never stops a print, and quitting is refused while an upload is in flight.
  `plate go` is unaffected and remains the no-extra-dependency path for SSH,
  dumb terminals, and screen readers. Like `go`, `tui` is interactive-only:
  `--json` and a non-TTY stdin exit `5` with the standard error envelope.
  The prepare screen also offers **advanced slice settings** (`s`): a grouped
  form over the named `slice` flags, plus an *Add an override* editor (a key, a
  process/filament bucket, and a value) for everything the named flags do not
  cover, recorded as `--set` / `--set-filament`. Blank fields keep profile
  defaults, unsafe values are refused by the same validation the CLI applies,
  and touching nothing leaves the slice byte identical to before. Use
  `plate slice --list-settings` to see which keys your profiles accept.
### Removed

- Python 3.9 support. The floor is 3.10 so every install resolves patched
  `zeroconf` (no 3.9-only advisory residual).
### Changed

- 3MF estimate parsing walks the zip namelist in one pass and stops reading a
  gcode header once both time and filament weight are known.

- `upload` / `job` `--json` include `size_verified` (`false` when the printer
  omitted FTPS `SIZE` after the transfer). Mismatch is still a failure.

- Command handlers raise `BambuError` instead of emitting a JSON error and
  then aborting. `cli.main` is the sole error-envelope writer
  (`write_error_envelope`). Soft statuses (`confirmation_required`, etc.)
  still emit their own contracts.

- Tests import the real `paho-mqtt` package instead of stubbing it in
  `sys.modules`. Audit-named test files are renamed to topic names.

- The `[tui]` extra now requires Textual 8.x (`textual>=8.0,<9.0`). The
  previous `<2.0` cap was hiding an 8.x `Select` API change (`Select.NULL`
  replaced `Select.BLANK`/`False` for no selection). Pilot tests read
  `Static.content` instead of the removed `renderable`.

- The TUI dashboard and monitor keep one MQTT TLS session for the process
  instead of opening a new connection on every refresh. One-shot CLI commands
  still connect and tear down.

- `--json` emitters construct `bambu_cli.contracts` objects. `status --json`
  no longer flattens firmware fields onto the envelope; they stay under
  `printer`. Errors go through `ErrorEnvelope`; `job` success/failure through
  `JobOk` / `JobError`.

- MQTT client construction, command/status, print-ack, and the monitor loop
  live in separate `protocols/mqtt_*.py` modules. TLS pinning uses a real
  `SSLContext` subclass (`PinningSSLContext`) instead of patching
  `wrap_socket` on a stock context.

- **`plate snapshot` no longer falls back to the Docker streamer by default.**
  The streamer does not honour `cert_fingerprint`, so a failed direct grab now
  aborts unless you set `camera_allow_streamer` in config or pass
  `--allow-camera-streamer`. X1-series printers need that opt-in.
  `camera_direct_only` still forbids the streamer even when the opt-in is set.
  The snapshot command moved out of `protocols/camera.py` into
  `bambu_cli.commands.snapshot`.

- **`plate tui` prepare screen is now two columns.** The form (source, material,
  quality, supports, the Prepare/Settings buttons) sits on the left and what the
  run produced — status, the time/filament estimate, and *Start print…* — on the
  right, so the estimate you waited for is visible next to the form instead of a
  scroll below it. The source box is one row with its label beside it, matching
  the settings screen. Below 100 columns the two halves would be too narrow for
  a material label, so the screen keeps the single-column layout there; on a
  narrow terminal a finished prepare — or a failed one — now scrolls itself into
  view instead of landing off-screen. The results side is a titled *Result* box
  that says what pressing *Prepare* will put in it, rather than an empty half
  screen with a disabled button floating in it, and the form's material,
  quality, supports and button groups are one width, so the column has a
  straight right edge instead of four different ones. Nothing about what the
  screen does changed.
- **`plate tui` no longer breaks up a long value in the print summary.** Model,
  printer, material and estimate are laid out as a real two-column grid on both
  the prepare screen and the confirmation dialog, so a value that wraps —
  "Bambu Lab P1S, 0.4mm nozzle" on a narrow column — continues under the value
  instead of restarting in the label column, where "nozzle" read like a field
  name of its own. A long file name is now wrapped in full rather than cut short
  with an ellipsis, and square brackets in a file name still render verbatim.
- **`plate tui` advanced settings are clearer to fill in.** The screen previously
  asked you to hand-edit a `KEY=VALUE` string, with a `filament:` prefix to
  remember and no help on what a setting expects. Now the named flags with a
  fixed option set (wall type, support type, seam position, ironing) are
  dropdowns, and anything else is added as a name, a *process*/*filament* bucket
  and a value — the same split `--set` and `--set-filament` make. Pending
  overrides are a list you can click to edit or remove one at a time. The bucket
  resets to `process` for each new key instead of carrying the previous choice
  over, so a process setting can never be sent as a filament override by
  accident. An empty value is still allowed, since clearing a setting is a
  legitimate override.
  The screen was also rebuilt visually: each field is one row (label beside its
  control) instead of a stacked label over a bordered box, so a 80x24 terminal
  shows three whole groups rather than four fields; every field carries an
  example value; and the header names the screen you are on.
- Internal refactor only — no user-visible change. Extracted the path,
  JSON-mode/redaction, and argparse-coercion helpers out of `bambu_cli/cli.py`
  into three focused modules (`bambu_cli/paths.py`, `bambu_cli/jsonio.py`,
  `bambu_cli/argutils.py`), so domain modules no longer import private
  `_underscore` helpers from the CLI entrypoint (roadmap B.4). Behaviour,
  output, JSON envelopes, and exit codes are unchanged; `cli.py` re-imports the
  same helpers from their new homes.
### Fixed

- Windows `--json`: local path fields (`file`, `path`, `output`, `local_path`,
  `workdir`, `config_path`, the `job` step paths, …) are emitted with `/`
  separators instead of `\`. A single envelope previously mixed `\` in its
  typed path fields with the `/` used by human-facing messages, remote printer
  paths, and archive entries, so a consumer could not compare or join two path
  fields without knowing which produced which. `~` home compaction is
  unchanged. No effect on macOS/Linux.

- User-facing docs now match shipped behaviour for `--confirm` on `job` /
  `send` (upload still runs; exit `0` `uploaded_not_printed`), the fail-closed
  camera streamer (opt-in, not auto-fallback), `doctor` fingerprint/`-v`
  output, STEP/STP slice precedence, and sdist-relative links to repo-only
  quality docs.

- MQTT print-path integrity: `plate print` no longer reports a false
  `Print started` (exit 0) when the printer connection is refused. A refused
  CONNACK (e.g. wrong/rotated LAN access code) is delivered asynchronously via
  `on_connect`; it is now tracked and surfaced as a network error with
  `printed: false`, matching the status path.
- MQTT print/command payloads are now published exactly once per attempt. paho's
  auto-reconnect could re-fire `on_connect` inside the acknowledgement window and
  silently re-issue a state-changing command (print-start, pause, stop, gcode) to
  a printer that may already be executing the first. A once-flag prevents any
  reconnect from re-publishing.
- `plate print` now honours the printer's `project_file` acknowledgement: an ack
  carrying `result: fail` (e.g. invalid `ams_mapping`) is reported as a printer
  error instead of success.
- `plate print` no longer misattributes a stale, latched `print_error` from a
  previous job (arriving on the first periodic status report) to the new print.
  Errors are only blamed on this command once its own `project_file` ack has been
  seen.
- `plate pause/stop`/gcode commands now publish at QoS 1, so success reflects a
  broker acknowledgement rather than a bare local socket write.
- `plate config show`, `plate setup --migrate-access-code`, and `plate doctor`'s
  interactive cert-fingerprint pin now read config.json tolerant of a UTF-8 BOM
  (utf-8-sig), matching `load_config`. Previously these commands opened the file
  as plain utf-8 and failed on a BOM'd config (PowerShell `Set-Content -Encoding
  utf8`, Notepad "Save as UTF-8") that every other command loads fine — the
  diagnostic tools contradicted the working commands, and the doctor pin silently
  declined a security control the user had opted into. All config reads now route
  through one shared `config.read_config_json`.
- A failure to tighten config.json to 0600 (EPERM on exFAT/vfat/NTFS mounts,
  network shares, or a root-owned-but-readable file) no longer aborts every
  command. The chmod is now best-effort: it warns and still reads the (readable)
  config, instead of hard-failing and having `preflight` misreport the file as
  invalid JSON.
- Access-code migration is now idempotent/retryable across its two writes. If the
  config write fails after the secret file was written, the just-created orphan
  secret file is cleaned up; a retry that finds an identical pre-existing secret
  file (its own resumed output) now completes instead of wedging behind "target
  already exists".
- The interactive setup wizard now rejects an empty or placeholder access code
  (re-prompting) instead of writing an immediately-broken config and, on a re-run,
  replacing a working credential with the empty one.
- `plate --json <bad global flag>` now always emits the JSON error envelope and
  exits `EXIT_COMMAND_ERROR` (5). An invalid value for a typed global flag (e.g.
  `plate --json --network-timeout abc status`) previously bypassed the envelope
  and exited `2` — which this CLI's contract maps to `EXIT_NETWORK_ERROR` — so
  an agent saw a silent, misclassified failure with no structured error object.
- Ctrl-C / EOF during a `--json` run now emits an `interrupted` JSON error
  envelope on stdout (and sends the human "Operation cancelled by user." line to
  stderr) instead of printing plain text to stdout, keeping the machine channel
  parseable like every other failure path.
- `plate upload … --dry-run` now surfaces the real failure reason (a TLS
  cert-pin mismatch, a bad access code, an unreachable printer) instead of the
  fixed, misleading "Could not reach printer." — a security-relevant pin failure
  is no longer indistinguishable from the printer being off.
- `plate snapshot` on A1/A1M no longer swallows configuration errors: a domain
  abort raised while acquiring the printer (e.g. a malformed `access_code`) now
  propagates with its own exit code instead of being demoted to a debug log and
  falling through to the Docker path. A file-write failure on the direct-grab
  path (disk full, permission denied) now exits `EXIT_FILE_ERROR` with a JSON
  error object rather than escaping as an uncaught traceback.
- `plate doctor` (and the written `printer_capabilities.json`) now reports
  `camera_snapshot: true` for A1/A1M, matching the model-agnostic direct camera
  grab those printers actually support; previously it reported `false` and an
  agent would skip a working snapshot feature.
- Guided wizard (`plate go`): AMS material detection no longer names the wrong
  filament. It now trusts only the tray the printer marks active, scanning all
  AMS units (a non-active spool in an earlier unit no longer shadows the active
  tray in a later one), and treats the `tray_now` external-spool sentinel
  (254/255) as "nothing loaded from the AMS."
- Guided wizard: the confirmed print now feeds from the AMS (`use_ams=true`)
  when the wizard detected an AMS-loaded filament and the user kept that
  detected material; otherwise it keeps the conservative external-spool default.
  Previously every guided print sent `use_ams=false`, stalling AMS-only machines.
- Guided wizard: extracting a local `.zip` no longer crashes with an uncaught
  traceback on password-protected or Deflate64 archives; those now surface a
  clean extract-step error (the fix in `_extract_zip_model` also covers the
  `plate job` pipeline).
- `_display_path` no longer mangles sibling paths in JSON output: a path under a
  directory whose name merely starts with `$HOME` (e.g. `/home/user2/...` when
  `$HOME` is `/home/user`) is left intact instead of being rewritten to a
  non-existent `~2/...`. It now requires a separator boundary after the prefix.
- `_resolve_ip` no longer permanently caches DNS failures: a transient resolve
  failure or a join-timeout is returned without caching, so later calls retry
  (only genuine successes are cached). The resolver thread is a daemon.
- `redact_url_credentials` now strips userinfo from scheme-relative URLs
  (`//user:pass@host/…`), closing a gap where such a URL echoed into a log line
  or JSON error detail reached output with its password intact.
- **Test-suite integrity (deep audit):** tautological assertions in
  `test_mqtt_print_and_setup.py` replaced with discriminating checks
  (`list_files()` must return a `list`, `delete_file()` must return `True`
  (fire-and-forget FTPS semantics), `status()` must return a `dict`, `_validate_slice_options(valid)`
  must return `None`, `_process_profile_compatible` asserts exact `True`/`False`);
  `_SIM_FTP_FILES` dict now snapshot-restored around every test via autouse conftest
  fixture; `test_coverage_platform_paths.py` permission test now asserts
  `status == "warning"` and the `chmod 600` hint for a 0o644 file; contract suite
  slice fixture replaced with real hermetic emitter output via orca stub.
  `tests/agent_cli_smoke.py` no longer sets `BAMBU_KEEP_WORKDIR=1` at import time
  (it now only lives in the subprocess env), so importing it can no longer suppress
  workdir cleanup across the whole pytest process; the defensive `delenv` guards in
  `test_job.py` were removed. `tests/live_printer_smoke.py` now resolves `BAMBU_CLI`
  lazily on first use instead of at import, so a set-but-invalid `BAMBU_CLI` (e.g.
  containing `--sim`) can no longer break collection of the hermetic suite.
- **Docs honesty:** `AGENTS.md` architecture-debt section updated to reflect that
  B.4/B.5 both landed; SECURITY.md and README corrected to clarify that `job`/`send`
  without `--confirm` exits `0` with `uploaded_not_printed` (not exit 5); `docs/api.md`
  `stop` section now references `schemas/stop.json` (published since 0.4.0);
  `docs/quality-roadmap.md` schema-gaps and B.4/B.5 stale claims corrected;
  `docs/test-backlog.md` schema count updated 19 → 25; `docs/manual.md` Windows
  detection table corrected to list both `orca-slicer.exe` (current) and
  `OrcaSlicer.exe` (older) per directory; `AGENTS.md` sdist table now includes
  `docs/manual.md` and `docs/troubleshooting.md`.

- `plate job/send <url> --dry-run` now reports `would_slice` consistently with
  the real run. The dry-run predictor previously returned `would_slice: false`
  for sources whose extension it could not read from the URL path (Printables
  model pages, extension-less direct links), even though the real run downloads
  a model file (falling back to `.stl`) and slices it. The prediction and the
  real run now share one slicing predicate, so a dry-run pre-check no longer
  disagrees with what actually happens.

- `plate download`: fixed a crash when a `Content-Disposition: attachment;
  filename="*.zip"` header upgraded a resolved-name download to an archive. The
  archive temp file was never created on that path, so the transfer body did
  `open(None, "wb")` and reported a spurious network error; archive downloads now
  always get a temp path.
- `plate download`: 0-byte placeholder files with the requested name (reserved by
  the collision-avoidance step) are no longer left on disk when a download fails,
  is re-targeted by a redirect/`Content-Disposition`, upgrades to an archive, or
  re-resolves an HTML page. Previously an agent/user could slice or upload the
  empty placeholder believing the download succeeded.
- `plate download`: a Printables GraphQL error-shaped response (`{"errors": [...],
  "data": null}`) now degrades to a clean resolve error instead of an unhandled
  `AttributeError`/`TypeError` traceback. Null `stls`/`gcodes` fields and file
  entries missing `id`/`name` are also handled gracefully.
- `plate download`: a password-protected/encrypted ZIP member now reports an
  extract failure (`EXIT_FILE_ERROR`) instead of a misleading network error — the
  `RuntimeError` `zipfile` raises for encrypted members is mapped to the extract
  path. The extraction placeholder is also cleaned up on member-write failures.
- `plate slice`: a stale pre-existing `*_sliced.3mf` is no longer accepted as
  fresh output. `slice` now snapshots the output path before running OrcaSlicer
  and rejects a file the run did not rewrite, so a failed re-slice can no longer
  report success (and be uploaded/printed) with an outdated model.
- `plate slice`: on a slice timeout or Ctrl-C the whole OrcaSlicer process group
  is now killed (`start_new_session` + `os.killpg` on POSIX), so `xvfb-run`'s
  Xvfb and OrcaSlicer children are reaped instead of surviving as orphans burning
  CPU. Windows behaviour (plain `kill()`, no wrapper) is unchanged.
- `plate slice`: an unrecognized `--quality` value (e.g. a typo like `High`, or an
  unsupported layer height) now logs a loud warning before falling back to
  `0.20mm Standard`, instead of silently slicing at a different layer height.
- `plate job/send`: `--copies` on a printer-ready source (`.3mf`/`.gcode`) now
  warns that copies only apply when slicing (and flags `copies_ignored` in the
  JSON/dry-run summary) instead of silently printing a single copy.
- `plate job/send`: an unreadable/IO-erroring ZIP archive (e.g. `PermissionError`,
  disk-full during member extraction) now emits the structured job-failure summary
  (`failed_step: "extract"`) instead of escaping as an unstructured error.
- `plate job/send <model> --dry-run`: a 0-byte/unreadable sliceable model now fails
  the dry-run, matching the existing printer-ready empty-file check (dry-run parity).
### Documentation

- Troubleshooting gained the three `plate tui` symptoms, keyed to the exact strings
  the command prints: the missing `[tui]` extra (exit `1`), the interactive-only
  refusal in a script, pipe, or `--json` run (exit `5`), and an override that is
  accepted but changes nothing — the *Applies to* bucket, which is what stops a
  filament key being sent as a silently-ignored process override.
- SECURITY.md now states how deliberate-intent works in the interactive front-ends.
  The threat model described `--confirm` as the gate for physical actions, which is
  true of the non-interactive commands but not of `plate go` / `plate tui`, where the
  user never types the flag and the equivalent gate is an explicit confirmation
  dialog. Both remain unscriptable (`--json` and non-TTY stdin exit `5`).
- AGENTS.md: the module table now lists `interactive/`, `tui/`, `tlspin.py`,
  `netsafety.py`, `printables.py`, `ams.py` and `utils.py`; `go` and `tui` are marked
  explicitly as human-only surfaces with no machine contract; and a stray paragraph
  that had been splitting the quality-gates table in two (breaking its rendering on
  GitHub and PyPI) was moved below it.
- CONTRIBUTING.md lists all eight CI smokes instead of four, and explains why
  `python_compat_smoke.py` is worth running by hand: PEP 604 `X | None` passes ruff,
  mypy, and the local suite while breaking Python 3.9 at import time.
- Measured coverage/test numbers refreshed across the quality roadmap and test
  backlog from the current matrix (Linux 88.4%, Windows 88.09%, macOS 88.33%),
  replacing figures dating from before the TUI work.
### Security

- `read_3mf_estimate` now refuses `Metadata/slice_info.config` members larger
  than 10 MB and falls back to the gcode header, so a zip-bomb / oversized XML
  in a downloaded `.3mf` cannot exhaust memory.

- `insecure_tls` is now coerced strictly and fails **closed**. A hand-edited
  JSON string such as `"insecure_tls": "false"` (or `"no"`/`"0"`) is a truthy
  Python string and previously silently DISABLED TLS certificate validation for
  MQTT/FTPS/camera while the user believed it was off (fail-open). Only a JSON
  boolean `true` (or the explicit string spellings `"true"`/`"1"`/`"yes"`/`"on"`)
  now enables it; any other value keeps validation ON and warns. `preflight` /
  `config validate` also flag a non-boolean `insecure_tls`.
- Migrating an inline `access_code` out of config.json no longer leaves a
  plaintext copy of the credential behind in `config.json.bak`. The backup that
  `_secure_write_json` normally keeps for crash recovery is suppressed (and any
  stale `.bak` scrubbed) whenever the previous config held an inline secret being
  removed — for both `plate setup --migrate-access-code` and the wizard's
  inline→file switch. Without this the migration was defeated: the secret it
  removed from config.json persisted in the sibling `.bak`.
- A config carrying BOTH an inline `access_code` and an `access_code_file` now
  uses the **file** (matching the migration story) and warns loudly about the
  conflict, instead of silently authenticating with the possibly-stale inline
  value. `setup --migrate-access-code` now strips the stale inline key in that
  state instead of no-op'ing, and `preflight` flags the lingering inline key.
- `plate setup` with both `--access-code` and `--access-code-file` no longer
  silently overwrites an existing secret file (secret-file writes are backup=off,
  so the old credential — possibly shared with another printer profile — was
  unrecoverable). It now refuses unless `--force` is passed.
- `plate slice`: the bed-temperature safety bound (`MAX_BED_TEMP_C`) is now applied
  to any `*_plate_temp` / `*_plate_temp_initial_layer` override, not just the four
  legacy plate types. OrcaSlicer 2.2+ `supertack_plate_temp` (Cool Plate SuperTack)
  overrides previously bypassed the range check.
- `plate job/send --dry-run`: the output-directory writability check now uses a real
  create-and-remove probe instead of `os.access(W_OK)`, which ignores NTFS ACLs on
  Windows — a dry-run against an ACL-denied location no longer falsely reports
  success where the real run would fail.
- Consolidated TLS certificate-fingerprint pin verification into a single shared
  `bambu_cli.tlspin.verify_cert_fingerprint`, used by MQTT, FTPS (control and data
  channels), and the direct camera grab. Previously each transport carried its own
  hand-written copy of the security-critical compare. The shared verifier fails
  closed on a missing pin, a mismatched pin, or an unobtainable peer certificate,
  and uses a constant-time comparison (`hmac.compare_digest`) on normalized hex.
  The pin is validated to be exactly 64 hex characters before comparison, so a
  malformed or non-ASCII pin (e.g. a copy-pasted homoglyph or non-breaking space)
  fails closed with the caller's error type rather than raising a `TypeError` that
  could escape into a transport's generic fallback. Behaviour is otherwise
  equivalent at each call site, with one further hardening: the camera path now
  fails closed on a missing peer certificate instead of raising an unhandled
  `AttributeError` that previously fell through to the unpinned Docker streamer.

## [0.4.0] - 2026-07-29

PLACEHOLDER_REST