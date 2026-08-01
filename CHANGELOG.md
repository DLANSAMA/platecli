# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

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

### Changed

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

### Added

- Foundations for interactive guided-print mode (`plate go`).
  This is internal groundwork only — no new subcommand, no prompts, and no change
  to any existing command's behavior. The wizard itself follows in a later release.
  - `bambu_cli/slicer/estimate.py` reads print time and filament weight from an
    OrcaSlicer-produced `.3mf`, so a future preview can show an estimate before
    anything is sent to the printer. It prefers `Metadata/slice_info.config` and
    falls back to the header of `Metadata/plate_N.gcode`. It never raises —
    callers get `Estimate(None, None)` on any failure — and time and weight
    degrade independently, so a half-readable file still yields the half it has.
    Implausible values (non-positive, over 30 days, over 10 kg) are treated as
    unparsed on the principle that a wrong estimate is worse than a missing one.
  - `bambu_cli/interactive/presets.py` maps friendly material and quality choices
    onto the flags `job` already accepts, so the wizard can expose zero slicer
    knobs. Material presets carry their own nozzle and bed temperatures, sourced
    from the Bambu `@base` filament profiles.
- Interactive guided-print wizard `plate go`. It walks you from a model URL (or
  local file) to a running print without touching a slicer: paste a source, confirm
  the printer, pick a material and quality preset, answer one supports question, see
  a time/filament preview, then a default-No confirm before anything is sent. It is a
  front-end over the existing pipeline — it drives the same `download` → `slice` →
  `job` machinery `plate job` uses, so slicing is bit-identical. Notes:
  - Interactive by design: `go` requires a TTY on stdin. Piped/non-interactive stdin
    is refused with a pointer to `plate job <url> --confirm` for scripts, and `--json`
    always emits the error envelope (schema `docs/schemas/go.json`) and exits 5 —
    interactive mode has no machine contract. `go` is a local command, so an
    unconfigured printer reaches the wizard, which offers to run `plate setup` itself
    rather than hard-failing at the network gate.
  - The confirm step defaults to No; answering Yes is the deliberate-action gate,
    equivalent to `job --confirm`. Declining offers an upload-only path, and declining
    that keeps the sliced file rather than deleting it.
  - New package `bambu_cli/interactive/` (`prompts.py`, the only module that touches
    interactive input, and `session.py`, the linear state machine). Both the prompt
    layer and the pipeline collaborators are injectable, so the whole flow is tested
    without a TTY, a printer, a network, or a real slicer.
  - Bare `plate` (no subcommand) now launches the wizard when stdin **and** stdout
    are both TTYs and `--json` was not passed. Every non-interactive context — CI,
    pipes, `subprocess`, `plate | less`, `--json` — keeps the previous behavior
    (help to stderr, exit `5`), so no script or agent can observe a change. The
    top-level `--help` epilog now advertises `plate go`.
  - The material step is AMS-aware: it reads the loaded filament from printer status
    and, when it matches a preset (PLA/PETG/ABS/TPU), offers it as the prompt default
    marked "(detected in AMS)". The read is best-effort through the existing status
    machinery — any MQTT error, timeout, missing AMS, or unknown material falls back
    silently to the plain PLA default and never blocks the wizard.
- `camera_direct_only` config key (default `false`): when set, disables the Docker/RTSP streamer fallback so any direct port-6000 grab failure aborts instead of silently falling through to the unauthenticated streamer. Closes the remaining camera fallback residuals noted in SECURITY.md. X1-series printers still need the streamer; unset `camera_direct_only` for them.
- Published JSON schemas for the last four `--json` commands that lacked them:
  `upload.json`, `files.json`, `stop.json`, `setup.json`. README has claimed
  "every command speaks `--json` with published schemas" for a while; that is now
  true, and enforced rather than asserted.

### Changed

- `main` now carries a `.devN` version between releases (`0.4.0.dev0`). It kept the
  released version after 0.3.0, so `plate --version` from a source checkout printed
  `0.3.0` while running seven commits of unreleased code — and the bug-report
  template asks users to paste exactly that string. `docs/releasing.md` gains a
  post-release step so this cannot recur, and step 1 now says explicitly to drop the
  suffix when releasing, since `release.yml` compares the tag to `pyproject.toml`
  exactly.
- Schema coverage is derived instead of hand-maintained. The contract suite reads
  the subcommand list from `build_parser()` and fails if a command has no schema,
  if a schema's `$id` disagrees with its filename, if a schema file is
  unreferenced, or if the `docs/api.md` table omits one. The previous
  hand-written list had already drifted — it omitted `status.json`.
- `status_event.json` and `version.json` gained the `$id` and `title` fields the
  other schemas already carried, so every published schema is self-identifying.
- CI coverage floor raised **81 → 83**. Measured coverage had reached 84.10% on
  Linux while the gate still accepted 81, leaving three points of silent
  regression room; the existing drift guards pin the test count and the floor but
  never the measured percentage. 83 rather than 84 because **Windows is the
  binding leg at 83.85%** (Linux 84.10%, macOS 84.08%, Linux 3.9/3.12 ~84.3%), so
  a floor of 84 would fail CI on Windows.

### Fixed

- Material presets for the upcoming `plate go` wizard name their filament profile
  in full (`Bambu ABS @base`) rather than by bare material (`ABS`). `slice` matches
  `--filament` as a case-insensitive substring against every `@base` profile and
  takes the first `os.listdir` hit, so a bare `ABS` also matches
  `Bambu Support for ABS @base.json` — support-interface filament — and `TPU`
  matches `Generic TPU for AMS`. Because `os.listdir` order is filesystem-dependent,
  the profile chosen could differ between Linux and Windows. Caught before the
  wizard shipped; a regression test pins each preset to exactly one profile. The
  underlying substring matching in `slice`/`job` is unchanged and still matches
  loosely by design — only the preset values are now unambiguous.

- `tests/privacy_smoke.py` no longer treats the tokens of a GitHub noreply email
  as local account names. It split `user.email` on every non-alphanumeric
  character, so a `<id>+<login>@users.noreply.github.com` address produced
  `users`/`noreply`/`github` patterns that flagged every tracked file containing
  the word "users" — destroying the "any tracked-file hit is a real leak" triage
  rule. Email candidates are now mined only from the local part (before `@`),
  numeric id prefixes are dropped, and `users`/`noreply`/`github` are in
  `GENERIC_LOCAL_NAMES` as a backstop. Covered by a new hermetic regression test
  (`tests/test_privacy_smoke_patterns.py`).
- Auto-repair of URL-derived filenames is now correct in four ways it was not, and
  the repairer is guaranteed to produce names the printer-side check accepts.
  Previously `_sanitize_download_filename` could emit a name `_safe_remote_name`
  refused, meaning a model could download and then fail to upload.
  - **Windows device names are detected before the first dot.** The check used
    `os.path.splitext`, which strips only the last extension, so `aux.gcode.3mf`,
    `con.gcode.3mf` and friends passed both functions — and `.gcode.3mf` is the
    print-ready format this tool produces. On Windows those names are unusable.
  - **The length cap is enforced in UTF-8 bytes, not characters.** 160 CJK
    characters is 480 bytes and exceeded ext4's 255-byte limit, so the repairer
    produced names the local filesystem rejected with `ENAMETOOLONG`. Truncation
    stops on a codepoint boundary, so no mojibake.
  - **A pathological extension no longer defeats truncation.** `"x." + "a" * 200`
    left the stem limit at 1 and returned all 202 bytes untruncated.
  - **Trailing whitespace/dots are stripped after truncating, not before**, since
    truncation can land on a space — which Windows drops and the safety check
    rejects.
  Repaired names differ from before for these inputs (e.g. `aux.gcode.3mf` now
  becomes `_aux.gcode.3mf`), so a re-download may land under a new name. Ordinary
  names such as `USB-C Cover.stl` are untouched, and `#`/`%` remain legal.
- `slice --list-settings` writes its listing to **stdout** instead of stderr, one
  key per line and unwrapped. It went through `logger.info`, so the obvious usage
  — `plate slice --list-settings | grep layer_height` — silently matched nothing,
  and Rich wrapped long values such as `description` across lines, defeating
  `grep` even when stderr was captured. The header and closing hint stay on stderr
  as human chrome, so redirecting stdout to a file yields only data. `--json`
  output is unchanged.
- `plate setup` no longer silently deletes config keys it does not manage.
  It rebuilt `config.json` from scratch, so re-running it for an unrelated reason
  dropped hand-added settings — `camera_port`, the `*_timeout` tunables, and
  security opt-ins such as `camera_direct_only`, which looked enabled afterwards
  but was not. Setup now preserves unmanaged keys and lists the ones it kept.
  Keys it *does* manage stay authoritative, so switching an inline `access_code`
  to an `access_code_file` still removes the plaintext secret, and declining
  `insecure_tls` still turns it off.

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

[Unreleased]: https://github.com/DLANSAMA/platecli/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/DLANSAMA/platecli/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/DLANSAMA/platecli/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/DLANSAMA/platecli/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DLANSAMA/platecli/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DLANSAMA/platecli/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DLANSAMA/platecli/releases/tag/v0.1.0
