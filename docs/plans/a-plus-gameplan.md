# Gameplan: A+ across the board

**Date:** 2026-08-13  
**Current measured (2026-08-14, post-#119/#120):** **1499** passed / 1 live deselected, **90.99%** branch on Linux (**90.68%** Windows — the binding leg, macOS passing) over **8368** statements, CI floor **86**. Scoreboard: overall **A**, none below A−. Product **A−**. **A+ is not earned.**

*Historical baseline (2026-08-13, dirty tree, Linux py3.12): 1459 passed, 89.4% over 8413 statements — superseded by the line above; kept because the W1 sizing below was computed from it.*

**Progress 2026-08-13:** W1 moved coverage **89.4 → 90.99%** (1499 passed, 8368 stmts) via transport/session/camera/ftps tests + dead `abort` tails after `emit_json_error`. W2/W3 landed (residual acceptance, facade freeze, scoreboard refresh). **Did not raise the CI floor** (90.99 < the 92.5 margin W4 requires). **Not A+ across the board.** Remaining: 90.99→92.5, floor 86→92, `mypy --strict`, `v1.0.0` tag.

This is an execution plan, not another audit. Truth sources stay [quality-roadmap.md](../quality-roadmap.md) §2 / §3.1 / §5 and [test-backlog.md](../test-backlog.md).

## What A+ actually is

A+ across the board means **all three** of:

1. Every scoreboard row is **A+** (roadmap §1).
2. The §3.1 A+ *totals* are met (suite ≥550, line/branch **≥92% / ≥85%**).
3. The §5 v1.0.0 checklist is ticked, including tag `v1.0.0`.

§5 also requires the **mqtt / ftps / netsafety / download** module floors (A+ column: 95 / 95 / 98 / 95). Wizard / preflight / camera / slicer floors in §3.1 are stretch; do not block the 92% total or a 1.0 tag on them.

## Honest split: this session vs blocked

| Row | Now | This session target | Blocked on |
|-----|-----|---------------------|------------|
| Security | A | **A+** | Formal residual *acceptance* in SECURITY.md (not new crypto). Hypothesis + `-m security` already run in default CI. |
| Architecture | A | **A+** | Facade freeze test on `protocols/mqtt.py` `__all__`. Complexity budget = no new C901 ratchet this pass (enabling C901 would be its own PR). |
| Agent JSON UX | A | **A+** | Already has generated schemas + contract loader. Refresh schema count 26→27. Field-level api.md sync stays a follow-on. |
| Correctness | A | **A+** | Property tests already exist. No new dead-flag hunt unless a real one appears. |
| Typing | A | **A** (stay) | `mypy --strict` is **1521** errors. §5 is already met (`check_untyped_defs`, no excludes). Do **not** claim Typing A+. |
| Error model | A | **A+** | Already entry-only `sys.exit`. |
| Tests | A | **A+** | **92%** total on Linux. Per-module A+ floors only for §5's four. |
| CI / release | A | **A+** | Raise `--cov-fail-under` to **92** only if Linux ≥ **92.5%** (Windows last trailed ~0.3 pt). |
| Docs | A | **A+** | Number refresh **done** (#120: 1499/90.99%/8368) and camera/`--confirm`/doctor accuracy **done** (#121/#122). Remaining: field-level `api.md` sync. |
| Product | A− | **A−** until tag | Classifier + changelog can be prepared. **Do not tag `v1.0.0` without an explicit user “tag it”.** |

**Consequence:** this session can make every *unblocked* row A+ and leave Typing A + Product A−. That is **not** “A+ across the board.” Do not write that phrase into the scoreboard until the tag exists and Typing is either strict or the A+ definition is deliberately changed in a separate, reviewed docs PR.

## Wave order (do in this order)

### W1 — Coverage to ≥92.5% Linux (Tests A+)

Sized when coverage was 89.4%; **as of 2026-08-14 Linux is 90.99%**, so the remaining gap to 92.5% is roughly **~125** statement/branch hits, not the ~280 originally scoped. The per-module misses below are pre-W1 and were not re-measured — re-run `--cov-report=term-missing` before picking targets. Hit the fattest *decision* holes first; do not pragma I/O loops just to move the number.

| Priority | Module | Last miss | Approach |
|----------|--------|-----------|----------|
| 1 | `protocols/mqtt_monitor.py` | 71.3% | Drive `monitor_status` with Fake/Magic client: sim human path, connect rc≠0, decode error, KeyboardInterrupt, teardown exceptions |
| 2 | `protocols/mqtt_session.py` | 83.2% | `send_command` / `get_version` connect-fail, timeout, OSError retry; `_reset_client` teardown exceptions |
| 3 | `protocols/camera.py` | 87.2% | empty ip/code, EOF mid-frame, size≤0 skip, close() exception |
| 4 | `protocols/ftps.py` | 88.3% | connect cleanup, data-channel pin, `_SimFtp` 550 |
| 5 | `commands/snapshot.py` | 74.5% | helpers (`_utc_stamp`, port/bind), write OSError, docker unreachable, start-container fail |
| 6 | `setup_cmd/wizard.py` | 75.9% | `_parse_mdns_*` / `_cmd_setup_noninteractive` error matrix (no TTY) |
| 7 | leftover | downloader / preflight / slicer/cmd | Only if still <92.5% after 1–6 |

After W1: remeasure with `pytest -m "not live" --cov=bambu_cli`. Do not raise the CI floor yet.

### W2 — Docs truth + residual acceptance (Docs A+, Security A+)

- Scoreboard / backlog: **done** in #120 — **1499** passing, **90.99%** Linux / **90.68%** Windows, **8368** stmts, floor still 86 until W4. (Schema count **27** was never re-verified; confirm before quoting.)
- Delete the stale “TCP failure on 6000 still falls back to the streamer” sentence. **Done** in #121/#122.
- SECURITY.md: mark camera streamer / `insecure_tls` / leftover container / HTTP integrity / TOFU / Windows ACLs as **accepted 1.0 residuals** (not open P0s). That is what “close or explicitly accept” means.

### W3 — Architecture cheap A+

- Freeze `bambu_cli.protocols.mqtt.__all__` in a test (fail if a name is added without updating the freeze).
- Do **not** enable ruff C901 in CI this wave.

### W4 — CI floor (CI A+)

- If Linux ≥92.5%: set `--cov-fail-under=92` in `ci.yml`, CONTRIBUTING, AGENTS, roadmap, backlog, `ci_workflow_smoke.py` together.
- If Linux is 92.0–92.4%: leave floor at 86 (or ratchet to 90) and say Windows may flake a 92 gate.
- Never raise the floor on an unmeasured Windows hope.

### W5 — 1.0 prep only (Product stays A−)

- Changelog “Unreleased → 1.0.0” draft is fine.
- Do **not** change `Development Status :: 4 - Beta`, do **not** bump `pyproject.toml` to 1.0.0, do **not** `git tag v1.0.0` unless the user says to ship.

## Out of scope (do not start)

- `mypy --strict` (1521 errors).
- Weekly fuzz / SBOM / Dependabot (Phase E).
- Scheduled live-printer lab.
- Field-level generated `api.md`.
- Per-module floors for wizard / preflight / camera / slicer (optional after 1.0).

## Gates before claiming any row moved

```bash
uvx ruff check bambu_cli && uvx ruff format --check bambu_cli
uvx mypy -p bambu_cli
uvx bandit -c pyproject.toml -r bambu_cli -ll
python scripts/check_layers.py
python scripts/gen_schemas.py --check
uv run python -W error::ResourceWarning -m pytest tests/ -m "not live" \
  --cov=bambu_cli --cov-report=term-missing --cov-fail-under=86
uv run python tests/ci_workflow_smoke.py
```

A green pytest is not evidence of ruff/mypy/bandit. Do not advertise A+ until the measured % and the scoreboard agree.
