# Documentation

**For users** (also shipped in the PyPI sdist):

- [manual.md](manual.md) — full user guide: setup, config reference, slicing and AMS, monitoring, every flag.
- [troubleshooting.md](troubleshooting.md) — fixes keyed to the exact messages `plate` prints.
- [api.md](api.md) — JSON contracts, support matrix, and stability policy for scripts and agents.
- [schemas/](schemas/) — JSON Schema files for every `--json` payload (generated; do not edit by hand).

**For contributors** (GitHub only):

- [quality-roadmap.md](quality-roadmap.md) — current test and coverage numbers, quality scoreboard, what is left before 1.0.
- [mutation-baseline.md](mutation-baseline.md) — mutation testing scope and floor.
- [live-printer-smoke.md](live-printer-smoke.md) — the opt-in real-printer harness (`BAMBU_LIVE=1`).
- [releasing.md](releasing.md) — release procedure and the PyPI yank runbook.

The `.gif` and `.png` files here are the README demos; each `.tape` file next to
a GIF is its [VHS](https://github.com/charmbracelet/vhs) source.
