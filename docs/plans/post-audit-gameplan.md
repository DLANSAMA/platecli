# Gameplan: post-audit hardening + `feat/tui` merge prep

**Audience:** coding agents executing work in this repo.  
**Source:** 2026-07-31 deep read-only audit (one parent pass + 3× parallel exploration passes).  
**Branch at audit:** `feat/tui` @ `0d63378` (12 commits ahead of `main`) — **since merged** (#97, #104).  
**Measured suite at audit (Linux, that session):** `1314 passed`, `1 deselected` (live), **88.6%** branch coverage.  
**Version at audit:** `0.5.0.dev0`.

> **Every number and file path above is a 2026-07-31 snapshot, not current state.**
> Current: **1499** non-live tests, **90.99%** Linux / **90.68%** Windows over **8368**
> statements, CI floor **86**, version **0.5.0** (Beta), Python **3.10/3.12/3.14**
> (3.9 dropped in #115). `protocols/mqtt.py` is no longer an ~885 LOC hotspot — it
> was split into `mqtt_tls` / `mqtt_cmd` / `mqtt_print` / `mqtt_monitor` /
> `mqtt_session` in #119. Camera code lives under `protocols/`, not
> `bambu_cli/camera.py`. **See the re-verified status block below the residual
> table before acting on any row.**

> **Revision 2026-07-31 (cross-check pass).** A second independent audit re-verified this plan's premises and found the plan **missing a confirmed merge blocker** plus eight other findings; the new items are folded into the residual table and into **WS-B** below. S1/S2/S3 were re-verified in code and hold — **S2 is a genuine catch this plan surfaced that the other audit missed**. **Q2's diagnosis was wrong and is corrected below.** Post-fix suite is `1321 passed`, **88.58%**.

This is an **execution plan**, not a re-audit. Prefer implementation + green gates over more research.

---

## 0. Non-negotiable constraints (read first)

Copy these into every work session:

1. **Architecture / printer safety:** [AGENTS.md](../../AGENTS.md), [SECURITY.md](../../SECURITY.md).
2. **Quality truth sources:** [docs/quality-roadmap.md](../quality-roadmap.md), [docs/test-backlog.md](../test-backlog.md). Prefer these over older prose.
3. **Never** run printer commands with `--confirm`, or `BAMBU_LIVE=1` / `BAMBU_LIVE_PRINT_CONFIRM`, without explicit human approval.
4. **`sys.exit` only in `bambu_cli/cli.py`.** Domain raises `BambuError` / `abort`. CI greps this.
5. **No `@mockable`**, no `isinstance(..., Mock)` / test-awareness branches in production.
6. **Do not** hand-maintain package / py_compile / help-command lists (setuptools + smokes auto-discover).
7. **Do not** add AI/session attribution trailers to commits/PRs.
8. **LOCAL-ONLY:** `CLAUDE.md` is gitignored via `.git/info/exclude` — never commit it.
9. **TUI is human-only:** no machine contract; never try to drive `tui`/`go` via `--json`.
10. **Confirm choke point:** under `bambu_cli/tui/`, `confirm=True` must appear only in `screens/confirm.py` (the Start print path). Preserve this invariant; add a CI grep if you touch confirm.
11. **No raw `str` in Rich sinks.** Any printer- or user-supplied value reaching a Rich `Table` cell, `Select`/`OptionList` prompt, or a `Static` without `markup=False` must be wrapped in `rich.text.Text` — a `str` is markup-parsed, silently eating `[...]` and raising `MarkupError` on `[/...]`. Filenames routinely contain brackets. See WS-B.

### Canonical gates (run before claiming done)

```bash
uvx ruff check bambu_cli
uvx ruff format --check bambu_cli
uvx mypy -p bambu_cli
uvx bandit -c pyproject.toml -r bambu_cli -ll
uv run python -W error::ResourceWarning -m pytest tests/ -m "not live" \
  --cov=bambu_cli --cov-report=term-missing --cov-fail-under=83
# After any ci.yml or CLI subcommand / floor change:
uv run python tests/ci_workflow_smoke.py
python scripts/syntax_smoke.py
python scripts/cli_help_smoke.py
# After packaging / package-data (e.g. tcss) changes:
uv build && uv run python tests/package_contents_smoke.py
```

A green pytest alone is **not** evidence ruff/mypy/bandit passed.

### What is already done (do not re-litigate)

- TLS pin single-sourced in `bambu_cli/tlspin.py` (B.5); mqtt/ftps/camera call it.
- SSRF layer + proxy disable + redirect hop cap; `allow_private_ips` CLI-only.
- ZIP extract: basename, skip symlink mode, size cap, noncolliding paths.
- Error model: domain `abort` / `BambuError`; entry-only `sys.exit`.
- Full-package mypy + `check_untyped_defs`.
- TUI phases 1–5 on `feat/tui` (dashboard → prepare → confirm → monitor → advanced settings).
- Shared wizard/TUI core in `interactive/core.py`.
- Camera: pin mismatch + pin+`ssl.SSLError` fail closed; `camera_direct_only` choke point; loopback default bind.
- Deep-audit fix wave already landed (history: `#93`–`#96` family commits).

### Honest residual (from audit) — this plan targets these

| ID | Residual | Severity |
|----|----------|----------|
| S1 | Default camera path can fall through to **unpinned** Docker streamer even when pin is set (empty/failed direct grab); only `camera_direct_only` closes it | High residual |
| S2 | `insecure_tls` + pin: MQTT/camera ignore pin; FTPS still verifies pin if present | Medium |
| S3 | Streamer JPEG `resp.read()` unbounded | Medium (local DoS) |
| S4 | HTTP downloads accepted (SSRF yes, integrity no) | Documented residual |
| A1 | Domain → `cli.build_parser` for job namespaces (`interactive/core.py`, `presets.py`) | Architecture debt |
| A2 | Process globals for JSON emit state (`utils._JSON_*`) | Architecture debt |
| A3 | `protocols/mqtt.py` ~885 LOC hotspot | Maintainability |
| Q1 | CI floor **83** vs measured **~88.6%** (~5pt silent-drift room); Windows binding leg ~88.09% | Quality |
| Q2 | ~~Docs mention 1307/1308 with 1 fail~~ — **CORRECTED: there is no phantom failing test.** `docs/test-backlog.md:13` and `quality-roadmap.md:19,64` say "1308 collected / 1307 passing"; the 1-test delta **is the deselected live test**, which is correct accounting, not a failure. The real issue is only that the numbers are **stale** (1307 → 1321, 88.53% → 88.58%). Refresh the numbers; do not "fix" a failure that never existed | Docs staleness (low) |
| Q3 | Roadmap Architecture **A** / AGENTS “no architecture debt” slightly overstated → **A−** fair | Docs honesty |
| T1 | Textual pinned `>=0.86,<2.0` (8.x breaks dashboard pilots) | Dep risk |
| T2 | Confirm modal keyboard ergonomics / quit-during-prepare thinner than job-in-flight | UX polish |
| R1 | Untracked `docs/job-hero.mp4` (2.3M); marketing media hygiene. **Note: it is untracked AND unignored** — `git check-ignore` covers every other stray artifact but not this one, so `git add .` commits it | Repo hygiene |
| **B1** | **MERGE BLOCKER — Rich markup injection in TUI table cells.** `status_panel.py:30`, `confirm.py:249`, `ams_panel.py:37`, `settings.py:306` passed raw `str` into Rich sinks, which markup-parse it. Repro: `model [remix].stl` rendered as `model .stl`; `a[/b]c.gcode` raised `MarkupError`. `download/naming.py` strips `<>:"/\|?*` but **not** brackets, so ordinary Printables names reach it. Worst case: the confirm modal — the only screen starting a physical action — displays a different filename than the one that prints | **Blocker — FIXED, in working tree, uncommitted** |
| S5 | `utils._redact_url_credentials` (`utils.py:62`) guards **every** string in emitted JSON via `_compact_all_strings`, but is the weaker of the two redactors and its body (`utils.py:70-82`) has **0% test coverage** — no test executes it. Repro: `bob:secret123@192.168.1.5` passes through unredacted; the stronger `jsonio` version strips it. `utils.py` is also outside the mutation scope | High |
| P1 | **Temperature bound fails open.** `--set-filament nozzle_temperature=400abc` → `_effective_override_temps` returns `([], [])` → validation passes, where plain `400` correctly errors. `_numeric_values` (`options.py:224-228`) `continue`s on unparseable values instead of rejecting. `options.py` *is* in the mutation scope — mutation testing mutates existing code and structurally cannot find a **missing** guard | High (printer safety) |
| Q4 | **The MQTT layer has never run against real paho.** 81 `sys.modules.setdefault("paho…")` calls across 33 test files; zero tests import the real library. Pin is `>=2.0,<3.0` — a binding change inside that range ships green. Same failure class as the textual 8.x break | High |
| Q5 | Two safety tests cannot fail as intended: `test_cmd_print_dry_run_success` (`test_doctor_and_safety.py:194`) never asserts the print publish did **not** happen; `cmd_stop` asserts only `assert_called_once()` (`test_printer_commands.py:277`) with no payload check, unlike its `cmd_pause`/`cmd_light` siblings | High |
| Q6 | ~13 contract tests build payloads **by hand** and validate them against the schema (`tests/contracts/test_schema_validation.py:302` et al) — no `bambu_cli` code runs, so emitter and schema can drift together. The file's own docstring at `:414` names the correct pattern | Medium |
| A4 | `mqtt_port` is a dead setting that `doctor` **lies about**: 3 references (`context.py:94,141`, `doctor.py:142`), never in a connect — `mqtt.py:167` hard-codes 8883. Doctor prints the configured port while connecting elsewhere | Medium |
| R2 | `privacy_smoke` is a crying-wolf gate: exit 1 locally on correctly-gitignored files (it walks the filesystem without consulting git), and on CI runners the account-name patterns resolve to the filtered generic `runner`, so its two best checks never build. Red locally, disarmed remotely | Medium |
| R3 | `CLAUDE.md:16` prints `--cov-fail-under=81`; CI is **83** (`ci.yml:77`, `CONTRIBUTING.md:25`, `AGENTS.md:104`). An agent following CLAUDE.md runs a weaker gate than CI | Medium |

### Status re-verified 2026-08-14 — read this before working any row above

The table is the **2026-07-31 audit snapshot**, kept as the record. Each row below
was re-checked against current `main` (`85c831e`); cited paths and line numbers are
from that check, not from the audit.

**Closed — do not re-open:**

| ID | Evidence |
|----|----------|
| S1 | Camera is fail-closed/opt-in: a failed direct grab does not start the streamer without `camera_allow_streamer` / `--allow-camera-streamer`; pin mismatch or `ssl.SSLError` with a pin hard-aborts; `camera_direct_only` forbids it entirely |
| S5 | `utils.py:63-67` is now a 4-line shim delegating to `jsonio.redact_url_credentials`. There is no longer a weaker second implementation — both spellings return `https://192.168.1.5/a.stl` for the audit's own `bob:secret123@…` repro |
| A3 | `protocols/mqtt.py` split into five siblings (#119) |
| A4 | `mqtt_port` is live, not dead: `context.py:89,137` → `printer.py:339` → `mqtt_tls.py:143` `_mqtt_port()` → `mqtt_tls.py:162` `client.connect(resolved_ip, _mqtt_port(printer), …)`. Doctor no longer lies |
| Q1 | CI floor is **86** (#119) against ~91% measured |
| T1 | Textual pin is `>=8.0,<9.0` (#115) — the `<2.0` cap is gone and the 8.x break is fixed |
| B1 | Rich markup injection fixed before the TUI merged (#97) |
| R1 / R3 | Media hygiene handled; `CLAUDE.md` cites floor 86 |

**Still open — re-verified as real:**

| ID | Evidence |
|----|----------|
| P1 | **Confirmed printer-safety bug.** `_numeric_values("400abc")` returns `[]` while `_numeric_values("400")` returns `[400.0]` (`slicer/options.py:162-175`), so a non-numeric nozzle/bed override contributes no values and temp validation passes vacuously where plain `400` correctly errors |
| Q4 | **Partly closed, and less closed than #116's message implies.** The 81 `sys.modules.setdefault("paho…")` calls are gone (0 remain), but `tests/bambu_test_base.py:26-29` still *unconditionally* assigns `sys.modules["paho"|"paho.mqtt"|"paho.mqtt.client"] = MagicMock()` at import time and never restores them. Real paho is installed and importable. This is also an order-dependence hazard of exactly the kind `AGENTS.md` forbids |
| Q5 | Both halves hold. `test_cmd_print_dry_run_success` (`tests/test_doctor_and_safety.py:198`) asserts `nlst` and `get_status` but never that the print publish did **not** happen; `test_cmd_stop_with_confirm` (`tests/test_cmd_device.py:107`) ends at `mock_send_command.assert_called_once()` with no payload check |
| Q6 | `tests/contracts/test_schema_validation.py:~299` still hand-builds `{"status": "print_started", …}` and validates it against the schema — no `bambu_cli` code runs, so emitter and schema can still drift together |
| A1 | Open, but the path moved: `interactive/core.py:558` and `interactive/presets.py:81` now import `build_parser` from `bambu_cli.cliparse` (not `bambu_cli.cli`). Domain→parser coupling remains |
| A2 | `utils.py:58` `_JSON_EMITTED` (with `_LAST_ERROR_PAYLOAD` / `_LAST_DOWNLOAD_PAYLOAD`) are still process globals mutated via `global` at `utils.py:133-134` |
| S3 | `commands/snapshot.py:399` is still an unbounded `resp.read()`. Severity is lower than the audit's Medium: the streamer path now requires explicit opt-in, so it is reachable only after the user enables it |
| R2 | Still crying wolf — `uv run python tests/privacy_smoke.py` exits **1** on a clean checkout |
| T2 / WS4 | Confirm-modal ergonomics and quit-during-prepare — unchanged |
| W3.4 | Never implemented: `ci.yml` contains no grep asserting `confirm=True` under `bambu_cli/tui` is confined to `screens/confirm.py` |

---

## 1. Outcome goals

After this gameplan:

1. **`feat/tui` is merge-ready** with honest docs, green multi-OS CI, and no phantom “1 failing test”.
2. **Security residuals S1–S3** are either fixed or explicitly deferred with tests + SECURITY.md updates (no silent status).
3. **Coverage floor ratcheted to 85** (data supports it; 88 is too tight for Windows).
4. **Optional stretch:** architecture A1 (parser decoupling) if time; not a merge blocker.

**Not goals of this plan:** `v1.0.0`, 92% coverage, Textual 8.x support, live printer lab, dropping Python 3.9.

---

## 2. Workstreams (ordered)

Execute **WS-B → WS0 → WS1 → WS2** in order. WS3 is optional post-merge or parallel only if WS0–WS2 green. WS4 is polish.

---

### WS-B — Merge blocker (do before anything else) — **DONE, awaiting review**

**Goal:** `feat/tui` must not merge while the TUI can mis-render or crash on a printer-supplied filename.

| Task | Detail | Status |
|------|--------|--------|
| WB.1 | Wrap Rich cell/prompt values in `rich.text.Text` at `tui/widgets/status_panel.py:30`, `tui/screens/confirm.py:249`, `tui/widgets/ams_panel.py:37`, `tui/screens/settings.py:306` | ✅ done (uncommitted) |
| WB.2 | Regression tests asserting bracketed values render verbatim and markup-shaped values do not raise, in `test_tui_dashboard.py` / `test_tui_confirm.py` / `test_tui_settings.py` | ✅ done — red-before-green verified by reverting each fix individually |
| WB.3 | Audit **every** markup sink under `bambu_cli/tui/` (`add_row`, `Static` without `markup=False`, `.update`, OptionList/Select prompts) and record safe/unsafe per site | ✅ done — this is what turned up the 4th site (`settings.py:306`) |

> **Why this workstream exists.** Commit `36a3d20` fixed this exact bug class in the OptionList prompts and claimed a full visual pass — but only patched the site where it was discovered. Four more sinks survived. **Rule: when an escaping bug surfaces, enumerate every sink of that kind before declaring the fix complete.** `grep -rn "add_row" bambu_cli/tui/` finds them all in one command. Text assertions cannot see markup — this class is invisible to the existing test style, which is why WB.2's tests assert against **rendered** output via a real `Console`.

**Gates:** all five canonical gates green post-fix — `1321 passed`, 88.58%.

---

### WS0 — Truth, hygiene, merge baseline

**Goal:** Docs and branch state match reality; no mystery failures; media not accidental.

| Task | Detail | Acceptance |
|------|--------|------------|
| W0.1 | Re-measure suite on current tip; record exact pass/fail | `pytest -m "not live"` green; paste final line into PR/notes |
| W0.2 | Refresh [quality-roadmap.md](../quality-roadmap.md) scoreboard: tests **1314+** all green (or current N), coverage measured, drop “1307/1 fail” wording; Architecture grade **A−** unless A1 lands | Docs consistency tests still pass |
| W0.3 | Refresh [test-backlog.md](../test-backlog.md) snapshot to match | Same |
| W0.4 | Soften [AGENTS.md](../../AGENTS.md) “no remaining architecture debt” to name residual seams (domain→`build_parser`, utils JSON globals, mqtt size) **or** leave and open follow-up issue — prefer one honest sentence | No false “zero debt” claim |
| W0.5 | `docs/job-hero.mp4`: either gitignore large raw mp4s, commit a intentional small asset, or delete — do **not** leave multi‑MB untracked with possible LAN leakage from tapes | `git status` clean of surprise binaries; tape warnings stay |
| W0.6 | Open/update PR `feat/tui` → `main` description from CHANGELOG Unreleased + this plan’s merge checklist | Human can review |

**Do not change behavior in WS0 except media/docs.**

**Gates:** ruff/mypy/bandit + pytest (floor still 83 until W1.1) + `test_docs_consistency` if it greps floors/numbers.

---

### WS1 — Coverage floor ratchet (83 → 85)

**Goal:** Deny ~5 points of silent coverage rot. Roadmap already says 85 is supported; Windows ~88.09% is the binding leg.

| Task | Detail | Acceptance |
|------|--------|------------|
| W1.1 | Bump `--cov-fail-under` **83 → 85** in `.github/workflows/ci.yml` | CI config changed |
| W1.2 | Update every enforced citation together: `docs/quality-roadmap.md`, `docs/test-backlog.md`, and any test that greps the floor (`tests/test_docs_consistency.py`, `tests/ci_workflow_smoke.py`) | Local `ci_workflow_smoke` + docs consistency green |
| W1.3 | Do **not** jump to 88 in this PR — Windows margin is ~0.09pt and will flake | Floor is 85 |

**Gates:** full non-live pytest with `--cov-fail-under=85` + `ci_workflow_smoke.py`.

**If something fails the new floor:** fix coverage with real tests on residual paths (prefer mqtt/ftps/netsafety/camera decision branches), not `# pragma: no cover` on pure helpers.

---

### WS2 — Security product fixes (S1–S3)

**Goal:** Close the highest-value residuals without breaking X1 users who need the streamer.

#### W2.1 — Pin implies no unpinned streamer (S1) — **preferred default**

**Current:** pin mismatch / pin+SSLError abort; empty direct grab still falls through unless `camera_direct_only`.

> ⚠️ **Product decision, not a bug fix — do not let an agent land this unasked.** S1 is a *documented, accepted* residual whose planned mitigation (`camera_direct_only`) already shipped. Changing the default means **X1-series users lose snapshots on upgrade**, since those printers require the streamer and have no port 6000. That is a breaking change in a minor release. The lower-risk alternative, which this plan should consider before flipping any default: **keep the fallback but emit a loud human + JSON warning** when a pin is set and the unpinned streamer is used — closing the silent part of the residual without breaking anyone. Get a human product call before implementing either.

**Target behavior (recommended):**

- When `cert_fingerprint` is set **and** `insecure_tls` is false: **do not** fall back to Docker streamer (same as `camera_direct_only` for that case), **unless** an explicit opt-in is set.
- Opt-in name (pick one; document in SECURITY.md + config/setup preserve-unknown-keys path):
  - `camera_allow_streamer: true` (new, default false), **or**
  - keep `camera_direct_only` but **default it true when pin is present** (more surprising for X1).
- Prefer **new key `camera_allow_streamer` default false** when pin is set: clearest semantics  
  “pin = verified direct only; streamer requires explicit allow”.
- X1 users without port 6000: set `camera_allow_streamer: true` (and understand streamer is unpinned), or leave pin unset + accept residual (document).

**Tests (required):**

- Pin set, direct returns no frame → abort, **no** streamer call (mock streamer/docker).
- Pin set + `camera_allow_streamer=true` → streamer allowed (existing path).
- No pin, not `camera_direct_only` → streamer still allowed (X1 / legacy).
- Pin mismatch still hard-aborts (regression).
- Pin + SSLError still hard-aborts (regression).

**Docs:** SECURITY.md known-limitations table; AGENTS camera paragraph; config help if any.

#### W2.2 — Unify pin + `insecure_tls` policy (S2)

**Current mismatch — re-verified in code 2026-07-31, this is real and is the strongest finding in this plan:**

- MQTT `protocols/mqtt.py:110-113` — `if printer.insecure_tls: tls_set(CERT_NONE)` / `elif printer.cert_fingerprint:` → **insecure_tls wins, pin silently skipped.**
- Camera `camera.py:206` — `if not printer.insecure_tls and printer.cert_fingerprint:` → **insecure_tls wins, pin silently skipped.**
- FTPS `ftps.py:103-116` — `if pin or insecure_tls: CERT_NONE` then `if pin: verify_cert_fingerprint(...)` → **pin wins, still verified.**

Three transports, two opposite policies. A user who pins and later sets `insecure_tls` to debug something keeps pin verification on FTPS while silently losing it on MQTT and camera. FTPS has the safe behavior; **Policy A generalizes FTPS's rule to the other two**, which is the right direction.

**Target (pick and implement one policy — recommend A):**

| Policy | Rule |
|--------|------|
| **A (recommended)** | If pin present → always verify pin; `insecure_tls` only affects CA/hostname when pin absent. Warn if both set. |
| B | If `insecure_tls` → refuse to also set pin (config error / doctor). |

**Tests:** mqtt + ftps + camera fixtures for pin+`insecure_tls` combinations; doctor/preflight message if policy B.

#### W2.3 — Cap streamer body size (S3)

- Bound `resp.read()` (or chunked read) for streamer JPEG to a sane max (align with direct-grab frame sanity / ~12MB-class limit already used elsewhere if present).
- On oversize: structured error, no partial write as success.
- Test with oversized fake streamer body.

#### W2.4 — Optional low-effort (same PR or follow-up)

- Human + JSON **warn** on `http://` downloads (S4) without breaking HTTP (residual stays but visible).
- Do **not** flip default to HTTPS-only without product decision.

**Gates:** existing camera suites + new cases; bandit still green; SECURITY honesty table updated (move fixed rows to Fixed).

---

### WS3 — Architecture polish (optional, not merge-blocking)

Do **after** WS0–WS2 green, or as a separate PR on main.

| Task | Detail | Acceptance |
|------|--------|------------|
| W3.1 | Extract job-namespace construction so `interactive` does not import `bambu_cli.cli.build_parser` | `rg "from bambu_cli.cli import build_parser" bambu_cli` empty (or only cli tests); wizard/TUI job args unchanged (contract tests) |
| W3.2 | Move `_JSON_EMITTED` / last-error payload state toward `RuntimeContext` (finish dual-write migration in `job/support.py`) | No behavior change; JSON envelope order tests still pass |
| W3.3 | Split `protocols/mqtt.py` (status wait / print execute / monitor / pin helpers) **behavior-preserving** | Same public functions; mqtt tests green |
| W3.4 | CI grep: `confirm=True` under `bambu_cli/tui` only in `screens/confirm.py` | Lint job fails if second site appears |

---

### WS4 — TUI polish (optional)

| Task | Detail |
|------|--------|
| W4.1 | Confirm modal: keyboard mnemonics or documented Tab order; pilot if bindings added |
| W4.2 | Soft quit guard or status text while **prepare** worker running (not only job) |
| W4.3 | Spike Textual ≥2 / 8.x pilot fixes on a branch — **do not** raise pin until green |

---

## 3. Suggested PR slice order

Keep PRs reviewable and gate-safe:

| PR | Title (suggested) | Contains | Blocks merge of |
|----|-------------------|----------|-----------------|
| **PR-A** | `docs: post-audit truth + coverage floor 85` | WS0 docs/hygiene + WS1 floor | nothing hard |
| **PR-B** | `fix: camera pin no streamer fallback by default` | W2.1 + tests + SECURITY | — |
| **PR-C** | `fix: unify pin/insecure_tls + streamer size cap` | W2.2 + W2.3 | — |
| **PR-D** | `feat: plate tui` (or final polish on `feat/tui`) | Existing TUI branch + any leftover WS0; merge to main when CI green | — |
| **PR-E** (later) | architecture: job namespace + mqtt split | WS3 | not required for TUI |

**Merge order recommendation:**  
Finish **PR-D (`feat/tui`)** with WS0 honesty + green CI first if product wants TUI out.  
Security WS2 can land on `main` before or after TUI; **prefer before or with** if you want security A closer to A+.  
Floor ratchet (PR-A) is safe anytime measured legs stay ≥88 — i.e. the ratchet target is **85**, chosen to leave ~3pt of headroom above the binding Windows leg (88.09%). Do not read "≥88" as the floor.

Alternatively: one combined PR on `feat/tui` with WS0+WS1+WS2 if the human wants a single land — only if the diff stays reviewable.

---

## 4. `feat/tui` merge checklist (for the human + agent)

Before merging to `main`:

- [ ] **WS-B markup fix committed** — no raw `str` in any Rich sink under `bambu_cli/tui/`; `grep -rn "add_row" bambu_cli/tui/` shows `Text(...)` at every site
- [ ] **Human smoke with a bracketed filename** — prepare a file literally named `model [remix].stl` and confirm the dashboard + confirm modal show the name in full
- [ ] `git status` clean of accidental multi‑MB media (incl. the untracked-AND-unignored `docs/job-hero.mp4`)
- [ ] CHANGELOG Unreleased accurately describes TUI
- [ ] `uvx ruff check/format`, `mypy`, `bandit` green
- [ ] `pytest -m "not live"` green with ResourceWarning as error
- [ ] Coverage floor (83 or 85 after W1) holds on **Windows** CI leg
- [ ] `uv build` + `package_contents_smoke` — `*.tcss` in wheel
- [ ] `ci_workflow_smoke` if parser/CI touched
- [ ] Multi-OS GitHub Actions green on tip (not only an older SHA like `cc6f78c`)
- [ ] Human smoke: `plate tui --sim` on a real TTY (not agent-driven)
- [ ] No AI/session attribution trailers; no force-push of shared history without ask
- [ ] Version remains `.dev0` until release tag process ([docs/releasing.md](../releasing.md))

**Do not auto-merge.** Human review required (plan precedent).

---

## 5. Implementation notes for agents

### Camera change (W2.1) — where to look

- Fallthrough choke: `bambu_cli/camera.py` after direct grab fails/empty, before streamer.
- Settings: `bambu_cli/context.py` (`camera_direct_only`, add allow-streamer if chosen).
- Config load/preserve unknown keys: setup already preserves unmanaged keys — keep that.
- Tests: `tests/test_camera_cmd.py` (large; follow existing pin/fallback patterns).

### Policy unification (W2.2)

- `bambu_cli/protocols/mqtt.py` `create_mqtt_client`
- `bambu_cli/protocols/ftps.py` TLS setup
- `bambu_cli/camera.py` direct grab pin branch
- Prefer **one helper** or one documented order: pin first, then insecure_tls CA skip.

### Floor ratchet (W1)

- `tests/test_docs_consistency.py` and `tests/ci_workflow_smoke.py` **enforce** floor citations stay in sync — update all in one commit.

### TUI

- Shared logic lives in `interactive/core.py` — **do not** reimplement AMS/job rules in screens.
- `confirm=True` only in `tui/screens/confirm.py`.
- Optional extra: `platecli[tui]`; import-guard in `tui/entry.py`.

### Commit style

- Conventional, complete sentences in body when non-obvious.
- No AI trailers.
- Prefer small commits matching PR slices.

---

## 6. Out of scope / refuse

- Live printer work without human OK.
- Raising floor to 88 or 92 in this plan.
- Making TUI an agent surface (`--json`).
- Enabling `insecure_tls` by default or weakening pin.
- Worktrees from `$HOME` (user rule: never; use project cwd).
- “Fixing” accepted residuals (Windows ACLs, TOFU on hostile LAN, access code = full control) without product design.

---

## 7. Success criteria (plan complete)

| Criterion | Evidence |
|-----------|----------|
| **TUI markup blocker closed** | Every Rich sink under `bambu_cli/tui/` uses `Text`; regression tests assert rendered output, verified red-before-green |
| Docs honest | Test counts current (staleness only — there was never a failing test); Architecture A− or debt fixed |
| Floor 85 | CI + docs + smokes agree |
| Camera pin story | Default path with pin set does not use unpinned streamer without opt-in; tests prove it |
| Pin/insecure_tls | One policy across transports; tests prove it |
| Streamer size cap | Oversized body fails closed |
| TUI | Merged or PR approved with multi-OS green + human `--sim` smoke |
| Gates | ruff, format, mypy, bandit, pytest+ResourceWarning all green |

---

## 8. Pasteable brief for a new agent session

```text
You are working in your local platecli checkout on branch feat/tui (or main if TUI already merged).

Execute docs/plans/post-audit-gameplan.md.

Priority order: WS-B (TUI markup blocker — already fixed in the working tree, verify + commit) → WS0 (docs/truth) → WS1 (cov floor 83→85) → WS2 (unify pin+insecure_tls; streamer size cap; camera streamer default is a HUMAN product call, do not flip it unasked). WS3/WS4 optional.

Also open and unassigned: S5 (utils redactor weaker + 0% covered), P1 (temperature override fails open on non-numeric values), Q4 (paho never exercised against the real library), Q5 (dry-run and cmd_stop tests cannot fail as intended). See the residual table.

Constraints: AGENTS.md + SECURITY.md; no --confirm / BAMBU_LIVE without asking; sys.exit only in cli.py; no @mockable; no AI/session attribution trailers; do not commit the local-only agent notes file.

Verify with ruff + ruff format + mypy + bandit + pytest -m "not live" (ResourceWarning error) at the stated cov floor. After ci.yml changes, run tests/ci_workflow_smoke.py.

Prefer small PRs as in §3 of the gameplan. Do not invent live printer tests. Keep tui confirm=True single-site invariant.
```

---

## 9. Audit evidence pointers (for implementers)

- Security agent findings: camera fallthrough `camera.py:431+`; MQTT insecure_tls `mqtt.py:110+`; FTPS pin despite insecure `ftps.py:104+`; streamer `read()` ~`camera.py:580`.
- Architecture: `interactive/core.py` / `presets.py` → `build_parser`; `utils.py` JSON globals; `mqtt.py` size.
- TUI: single confirm `tui/screens/confirm.py:113`; entry guards `tui/entry.py:41–57`.
- Prior full suite measurement: 1314 passed, 88.6% branch (Linux, 2026-07-31 session). Re-measure before claiming numbers.

---

*End of gameplan. Update this file’s “done” checkboxes in a follow-up commit only if you want living status; otherwise tick progress in the PR body.*
