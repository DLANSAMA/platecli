# Inbox

Noticed but not chosen, one line each, oldest first: `- date · where · what · (source)`. Triage promotes, merges or closes lines (see the `master-plan` skill). Nobody needs to read this to start work.

- 2026-07-25 · bambu_cli/config.py · flatpak OrcaSlicer profile-dir paths are inferred from the app-tree layout, never checked against a real Flathub install · (launch notes)
- 2026-07-26 · tests/test_docs_consistency.py · measured coverage % has no drift guard; only the test count and the CI floor are pinned · (launch notes)
- 2026-07-26 · download · `plate download <printables-url>` end to end was last exercised before the UA/throttle rewrite; may be stale, re-check · (launch notes)
- 2026-11-05 · bambu_cli/slicer · no bounds on speed/acceleration overrides (OrcaSlicer rejects bad values itself) · (audit round 4)
- 2026-11-05 · bambu_cli/job · no upper cap on `--copies` · (audit round 4)
- 2026-11-05 · bambu_cli/protocols/camera.py · each socket read has a 12 s timeout but the frame loop has no overall deadline · (audit round 4)
- 2026-11-05 · bambu_cli/download/extract.py · a ZIP's whole central directory is held in memory (zipfile reads it before any size check can run) · (audit round 4)
- 2026-11-05 · bambu_cli/jsonio.py · userinfo inside prose (not a URL on its own) is not redacted · (audit round 4)
- 2026-11-05 · bambu_cli/commands/snapshot.py · the Docker streamer's urlopen honours HTTP(S)_PROXY from the environment · (audit round 4)
- 2026-11-05 · bambu_cli/setup_cmd/config_cmd.py · human-mode `config show` can print camera_stream_url with credentials · (audit round 4)
- 2026-11-05 · bambu_cli/commands/files.py · `cmd_files` builds its printer with `get_printer()` instead of `ctx.printer()` like the other commands · (audit round 4)
- 2026-11-05 · bambu_cli/tlspin.py · a blank `cert_fingerprint` means "no pin" at connect time; `config validate`/`preflight` flag it, but commands that skip those checks run unpinned without a warning · (audit round 4)
