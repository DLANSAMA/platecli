# Live-printer smoke (pre-release harness)

Opt-in checks that exercise safety-critical paths against a **real** Bambu printer
and local slicer install. They never run in CI or in the default local suite
(`-m "not live"`).

**Agents:** never enable `BAMBU_LIVE=1`, `BAMBU_LIVE_PRINT_CONFIRM`, or any
`--confirm` live run without explicit user approval ([AGENTS.md](../AGENTS.md)).

## Hard gates (must all be true)

| Gate | Requirement |
|------|-------------|
| Env | `BAMBU_LIVE=1` (or `true` / `yes` / `on`) |
| Config | Working `config.json` (IP, access code, cert pin as you use for real work) |
| Source | `BAMBU_LIVE_SOURCE` — local print-ready `.3mf`/`.gcode`, sliceable mesh, ZIP, or supported URL |
| Pytest mark | Tests are marked `@pytest.mark.live`; CI uses `-m "not live"` |

Without `BAMBU_LIVE=1` the module **exits/skips** immediately and does nothing.

## What it covers (safety-critical round-trips)

Read-mostly by default (no motion / no print start):

1. **preflight --strict --json** and **doctor --json** (connectivity + redaction).
2. **gcode without `--confirm`** → must refuse / require confirmation (never sends).
3. **job --upload-only** → upload appears in `files --json` as a new remote name.
4. **Upload → download integrity** — after upload, downloads the remote file via
   the library FTPS path (`printer.download_file`) and checks local size equals
   remote SIZE (the Phase 0 size-mismatch guard would fail a truncated transfer).
5. **slice** (when source is sliceable or `BAMBU_LIVE_SLICE_SOURCE` is set) →
   output path is a structurally valid sliced `.3mf` (`_is_valid_sliced_3mf`).

Destructive / motion opt-ins (explicit extra flags; clear console warning):

| Env | Effect |
|-----|--------|
| `BAMBU_LIVE_PRINT_CONFIRM=1` | Starts a real print of the uploaded file (`print … --confirm`) |
| `BAMBU_LIVE_CLEANUP=1` | Deletes the uploaded file after the run (skipped if print started) |
| `BAMBU_LIVE_GCODE_CONFIRM=1` | Sends a **harmless** `M105` with `--confirm` (temperature query only) |

Anything that moves axes or starts a print is **off** unless you set the print
confirm env. Prefer a free printer that is not mid-job.

## How to run

```bash
# From repo root, with real config already set up:
export BAMBU_LIVE=1
export BAMBU_LIVE_SOURCE=/path/to/unique_live_smoke.3mf   # unique name preferred

# Optional: expect a specific remote name (URLs/ZIPs)
# export BAMBU_LIVE_EXPECT_REMOTE_NAME=unique_live_smoke.3mf

# Optional: local slice check (mesh path; uses configured OrcaSlicer)
# export BAMBU_LIVE_SLICE_SOURCE=/path/to/model.stl

# Script entry (same checks as pytest):
uv run python tests/live_printer_smoke.py

# Or pytest (still requires BAMBU_LIVE=1):
uv run python -m pytest tests/live_printer_smoke.py -m live -q --no-cov
```

**Default suite / CI (must never hit a printer):**

```bash
uv run python -m pytest tests/ -q -m "not live"
```

Optional cleanup after a successful upload-only run:

```bash
export BAMBU_LIVE_CLEANUP=1
```

## Safety notes

- Never run against a printer that is actively printing someone else's job.
- Use a **uniquely named** source file so upload-new / cleanup assertions are meaningful.
- `BAMBU_CLI` may point at an installed `bambu-cli`; default is this checkout’s
  `scripts/bambu.py`. Commands that include `--sim` are refused.
- Do not set `BAMBU_LIVE_PRINT_CONFIRM` unless you intend to start a print.
- Credentials and serials: doctor JSON must keep serial redacted; the harness
  fails if an unredacted serial leaks.

## Release checklist

Before tagging a release that touches FTPS, gcode confirm, slice validation, or
job upload paths:

1. Unit suite green (`-m "not live"`) plus the usual CI lint/type/security gates
   ([CONTRIBUTING.md](../CONTRIBUTING.md)).
2. Run this live harness with `BAMBU_LIVE=1` (upload-only path at minimum).
3. Optionally enable `BAMBU_LIVE_CLEANUP=1` after success.
4. Record the run date/host in the release notes if useful (no secrets).
5. For 1.0 readiness criteria beyond this harness, see
   [quality-roadmap.md](quality-roadmap.md) §5.
