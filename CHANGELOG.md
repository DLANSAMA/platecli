# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
- `--allow-private-ips` now actually enables private/LAN downloads for that invocation (it was parsed but never applied to runtime settings).
- `load_access_code` and domain handlers raise structured `BambuError` / `abort` instead of calling `sys.exit` (process exit is CLI entry only).

### Changed
- `VERSION` is resolved from package metadata / `pyproject.toml` only (no duplicate string in `constants.py`).
- MQTT status-monitor teardown no longer uses a bare `except:`.
- Removed `@mockable` / test-awareness indirection from production code.
- CI coverage floor raised to **92%** (`--cov-fail-under=92`); single pytest path; blocking purity greps for `sys.exit` / `@mockable` / Mock branches.

### Added
- JSON schemas under `docs/schemas/` and contract tests in `tests/contracts/`.
- `docs/quality-roadmap.md` scoreboard and residual coverage policy.
- TLS pin suite (`tests/test_tls_pinning.py`); SSRF / redirect residual coverage; security + contract pytest markers.

### Tests
- CLI e2e coverage for `--allow-private-ips` wiring into `RuntimeContext` / netsafety.
- MQTT + FTPS certificate fingerprint pin match/mismatch (and deferred-handshake) suite.
- Expanded unit coverage for netsafety handlers, setup helpers, slicer pure paths, wizard guided flows.

## [0.1.0] - unreleased

Initial development version: LAN-mode printer control (MQTT/FTPS), one-shot `job`/`send` orchestration, OrcaSlicer integration, guided setup with mDNS discovery, camera snapshots, SSRF-safe downloads with Printables support, simulation mode, and agent-facing `--json` output.

### Added
- PyPI trusted publishing on tagged releases (`v*`).
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, Changelog project URL.

### Changed
- Package renamed to `bambu-local-cli` for PyPI publication (the `bambu-cli` name on PyPI belongs to an unrelated project). The installed command remains `bambu-cli`.
- Wheels no longer bundle non-runtime files (`README.md`, `AGENTS.md` inside the package).

### Removed
- `requirements.txt`, which duplicated the `dependencies` already declared in `pyproject.toml`. Install with `uv pip install -e .` (or `pip install bambu-local-cli`) instead.
