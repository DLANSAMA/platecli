# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

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
- `requirements.txt`, which duplicated the `dependencies` already declared in `pyproject.toml`. Install with `uv pip install -e .` (or `pip install bambu-local-cli`) instead.
