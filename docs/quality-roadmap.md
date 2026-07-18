# Quality roadmap: A+ across the board

Living plan to take `bambu-local-cli` from **solid 0.1.x beta (B− overall)** to
**A+ product + A+ testing**. Derived from the 2026-07 harsh codebase audit.

Update this file when a phase ships: tick acceptance criteria, refresh the
scoreboard, and move “Done” items into `CHANGELOG.md` under the release that
lands them.

---

## 1. Scoreboard (baseline → target)

Baselines from audit + full `pytest --cov=bambu_cli` on 2026-07-08:
**368 tests**, **78%** line coverage (1105 / 4973 stmts missed), **130**
`sys.exit` sites in `bambu_cli/`, **7** `@mockable` sites (def + 6 uses),
**1** `BambuError` raise in production. Recalculate after each phase.

| Area | Baseline | Gate to A | Gate to A+ | Primary evidence |
|------|----------|-----------|------------|------------------|
| Security mindset | A− | A | **A+** | Every documented control works e2e; adversarial suite in CI; pin logic single-sourced |
| Architecture | B | A− | **A+** | No `mockable`; facade frozen/deprecated; domain ↛ CLI imports; complexity budget |
| Agent JSON UX | A− | A | **A+** | Versioned JSON Schema per command; contract tests load schemas |
| Correctness / bugs | B− | A | **A+** | Zero known dead flags; property tests on pure safety code |
| Typing | D | B+ | **A+** | mypy strict on all of `bambu_cli`; public APIs fully annotated |
| Error model | C | A− | **A+** | `sys.exit` only in `cli.main` / entry; handlers raise `BambuError` |
| Tests | B+ | A | **A+** | See §3 testing scorecard |
| CI / release | B | A | **A+** | Coverage floors; blocking audit; single test runner; one version source |
| Docs / governance | B+ | A | **A+** | Roadmap + backlog current; 1.0 stability policy published |
| Product polish | C+ | A− | **A+** | 1.0 release criteria met; support matrix; hermetic slicer tests |

**Overall target:** every row **A+** before tagging `v1.0.0`.

## Scoreboard (current)

Updated **2026-07-17** (full codebase audit + doc truth pass). Foundational phases
(0/A/B) are done. Phase C **typing is done** (full package + `check_untyped_defs`);
coverage floor is **81** (target 92). Phase D schemas largely landed but not
complete for every command. The camera Docker bind default and camera pin
soft-fallback hardenings are now **fixed** (loopback-only default bind,
fail-closed on pin mismatch and on `ssl.SSLError` during the handshake when a
pin is configured); see [SECURITY.md](../SECURITY.md) for the remaining
residuals (the no-pin-configured Docker streamer path is unverified by design,
and even with a pin a TCP-level failure on port 6000 still falls back to the
streamer, since X1-series printers legitimately refuse that port).
Those residuals do not lower the security *mindset* grade but are one reason
security is not yet **A+**.

| Area | Score | Evidence |
|------|-------|----------|
| Security mindset | **A** | allow-private-ips fixed; TLS pin suite (mismatch + handshake SSLError both fail closed); SSRF/redirect tests; bandit blocking; security markers; honest known-limitations table in SECURITY.md. A+ needs single pin helper |
| Architecture | **A−** | `@mockable` = 0; abort error model; thin entrypoint; domain ↛ `sys.exit`. **Still open:** domain→`cli` private helper imports (~40 sites); Phase B.4 extract not done; pin verify not single-sourced (B.5) |
| Agent JSON UX | **A** | ok/error envelopes + many per-command schemas + contract harness. Gaps: dedicated `status` success schema, upload/files/stop/setup |
| Correctness / bugs | **A** | dead flags fixed (global `--json` before subcommand); structured errors; purity greps; version single-sourced |
| Typing | **A** | `uvx mypy -p bambu_cli` full package with `check_untyped_defs = true`; no residual excludes |
| Error model | **A** | `sys.exit` only in `cli.py` (errors.py hits are docstrings); domain uses `abort` / `BambuError` |
| Tests | **A−** | **~618** non-live tests collected; **~82%** coverage; floor **81**; per-module floors not enforced |
| CI / release | **A−** | single pytest path; purity greps; bandit/audit/mypy blocking; **`--cov-fail-under=81`** (A+ target remains 92) |
| Docs / governance | **A−** | roadmap + backlog + SECURITY + AGENTS aligned (2026-07-17); prior AGENTS mypy-blocklist / backlog ≥98% claims corrected |
| Product polish | **B+** | quality gates in place; version remains **0.1.0** Beta; coverage ratchet + camera defaults remain for 1.0 A+ |

**Overall:** **solid A− / A** — error model, typing, and security *controls* are strong;
architecture is A− until domain helpers leave `cli.py`. Remaining gap to A+ / `v1.0.0`
is coverage toward 92, schema completeness, B.4/B.5 layering, and documented camera
hardenings. Tagging `v1.0.0` still requires §5.

**Coverage floor history:** 79 (honest post-Phase-1 gate) → **81** (2026-07-09).
Measured branch total is ~82.3% on Linux and ~81.9% on Windows; the floor is set
at the multi-OS minimum so the matrix does not flake while still denying ~2 points
of silent rot vs the old 79 gate.

### Residual coverage policy

Integration-heavy and platform/TTY/process paths may carry `# pragma: no cover` when unit tests already cover the pure/decision branches and remaining lines are I/O loops (MQTT ack, FTPS resume, interactive wizard, Orca process). Measured coverage is computed under `.coveragerc` with those exclusions. Do not pragma pure helpers that have no tests.

---

## 2. Non-negotiable A+ definition (product)

A+ for *this* project means all of the following are true simultaneously:

1. **Truth = docs.** Every flag, config key, and security claim is exercised by a
   CLI-level or contract test. Dead controls are P0 bugs.
2. **Agents can rely on JSON.** Schemas published; fields only removed under
   deprecation policy; `failed_step` / `exit_code` / `next_command` complete on
   error paths.
3. **Handlers are pure enough to unit-test.** No `sys.exit` in domain modules;
   `cli.main()` is the sole process-exit boundary (plus the console-script entry).
4. **Transport is proven.** MQTT, FTPS, camera TLS pin match/mismatch/missing-pin
   covered; upload resume/size mismatch covered; monitor NDJSON shape locked.
5. **CI is a quality gate, not a suggestion.** Coverage floors, typed core,
   blocking security tools, one canonical test command.
6. **No test-only runtime branches** in production (`mockable`, Mock isinstance,
   unittest-in-sys.modules, MagicMock-specialcased APIs).
7. **1.0 stability promise** for exit codes, JSON fields, and config keys.

---

## 3. Testing scorecard (baseline → A+)

### 3.1 Quantitative targets

| Metric | Baseline (approx.) | A | A+ |
|--------|--------------------|---|-----|
| Suite size (unit + contract, excl. live) | ~368 | ≥450 | ≥550 with no flaky tests |
| Line coverage total | ~78% | ≥85% | ≥92% |
| Branch coverage (enable when ready) | untracked | ≥75% | ≥85% |
| `protocols/mqtt.py` | ~59% | ≥85% | ≥95% |
| `protocols/ftps.py` | ~71% | ≥90% | ≥95% |
| `setup_cmd/wizard.py` | ~55% | ≥80% | ≥90% |
| `setup_cmd/migrate.py` | ~55% | ≥85% | ≥95% |
| `setup_cmd/preflight.py` | ~70% | ≥90% | ≥95% |
| `netsafety.py` | ~78% | ≥95% | ≥98% |
| `download/*` combined | ~75–85% | ≥90% | ≥95% |
| `camera.py` | ~68–75% | ≥90% | ≥95% |
| `slicer/` | ~75% | ≥85% | ≥92% |
| `job/` | ~93% | ≥95% | ≥97% (keep) |
| `commands/` | ~80% | ≥90% | ≥95% |
| JSON contract tests | partial (`test_json_contracts.py`) | every command | every command + schema file |
| Property / adversarial tests | few | netsafety + zip + filenames | + redirect/SSRF fuzz |
| Flakes in CI (30 consecutive green main runs) | unknown | 0 known | 0 |
| Dual runners (unittest list + pytest) | yes | single pytest entry | single + markers |
| Live printer | opt-in smoke | documented matrix | scheduled lab job (optional A+) |

### 3.2 Qualitative A+ test standards

Every new or touched command path must satisfy:

| Standard | Rule |
|----------|------|
| **No network / no printer** | Unit & contract tests never open real sockets except to `127.0.0.1` fakes under test control. |
| **Patch at the call site** | `patch("bambu_cli.download.downloader.build_safe_opener")` style — not production branches for tests. |
| **JSON is shape-asserted** | On success *and* error: `status`, `command`, `failed_step`, `exit_code`, and any documented extras. Prefer schema validation once schemas exist. |
| **CLI e2e for flags** | Global flags that mutate safety (`--allow-private-ips`, timeouts, `--json`, `--sim`) tested through `main()` / `parse_args`, not only hand-built `Settings`. |
| **Exit boundary** | Prefer `pytest.raises(BambuError)` / result objects over `SystemExit` once Phase B lands; until then assert exit codes *and* JSON. |
| **No new test-awareness** | CI grep stays red on Mock/unittest branches; do not extend `mockable`. |
| **Determinism** | Time, RNG, DNS, and temp paths controlled; no order-dependent tests. |
| **Resource hygiene** | `-W error::ResourceWarning` remains; no leaked threads/fds. |
| **Markers** | `@pytest.mark.contract`, `@pytest.mark.security`, `@pytest.mark.slow`, `@pytest.mark.live` for selective CI jobs. |

### 3.3 Test pyramid (target shape)

```
          ┌─────────────────┐
          │ live_printer    │  optional / scheduled lab
          │ (real hardware) │
          └────────┬────────┘
                   │
          ┌────────▼────────┐
          │ agent_cli_smoke │  installed entry + --sim JSON shapes
          │ + packaging     │
          └────────┬────────┘
                   │
     ┌─────────────▼─────────────┐
     │ contract tests            │  schema + full payload per command
     │ (test_json_contracts +    │
     │  schemas/*.json)          │
     └─────────────┬─────────────┘
                   │
     ┌─────────────▼─────────────┐
     │ domain unit tests         │  netsafety, zip, mqtt pin, ftps resume,
     │                           │  slicer argv, job orchestration (mocked steps)
     └───────────────────────────┘
```

**A+ bias:** heavy bottom two layers; smokes stay thin and fast; live is never
required for merge.

### 3.4 Suite restructure (testing A+)

| Work item | Why |
|-----------|-----|
| Collapse CI to **one** runner: `pytest` only | Dual unittest list drifts from full suite |
| Keep ResourceWarning via `pytest` filter or `python -W error -m pytest` | Preserve CI hardness |
| Convert remaining `unittest.TestCase` modules to pytest style over time | One idiom; fixtures via `conftest` / `bambu_test_base` |
| Introduce `tests/security/` | Adversarial SSRF, ZIP, filename, redirect |
| Introduce `tests/contracts/` + `docs/schemas/` | Schema-backed agent contracts |
| Introduce `tests/fakes/` | Shared fake MQTT/FTPS/TLS sockets, fake Orca binary |
| Refresh `docs/test-backlog.md` every phase | Backlog currently stale (job “12%” vs ~93%) |
| Coverage fail-under in CI | `pytest --cov-fail-under=85` then ratchet to 92 |
| Per-module fail-under for critical packages (optional second step) | Protect mqtt/netsafety from average dilution |

### 3.5 Prioritized test-writing queue (execute in this order)

Order is **risk × current hole**, not module size.

#### T0 — Correctness of safety controls (blocks security A)

| ID | Case | Module / entry | Assert |
|----|------|----------------|--------|
| T0.1 | `--allow-private-ips` through CLI enables private connect | `cli.main` + netsafety | Private IP allowed only with flag |
| T0.2 | Without flag, private/link-local/CGNAT rejected | netsafety | URLError + no connect |
| T0.3 | IPv6-mapped IPv4 private blocked | netsafety | reject |
| T0.4 | DNS cache TTL expiry + full clear at 1000 | netsafety | re-resolve / no stale allow |
| T0.5 | Redirect hop >5 fails clearly | netsafety | URLError message |
| T0.6 | Redirect to private IP rejected per hop | download + netsafety | no SSRF via redirect |

#### T1 — Transport (blocks tests A / architecture trust)

| ID | Case | Module |
|----|------|--------|
| T1.1 | MQTT cert pin match / mismatch / missing pin | `protocols/mqtt` |
| T1.2 | MQTT `send_command` retry exhaustion + timeout | mqtt |
| T1.3 | MQTT `get_status` decode errors soft-fail | mqtt |
| T1.4 | Monitor NDJSON: update → terminal; sim parity | mqtt + contracts |
| T1.5 | FTPS pin on control + data channel | ftps |
| T1.6 | Upload resume: short remote, size mismatch, 530 fail-fast | `printer.upload_file` / ftps |
| T1.7 | Download atomic partial cleanup on failure | printer / ftps |
| T1.8 | Camera pin match/mismatch/missing; oversized frame skip | camera |
| T1.9 | Camera Docker fallback: config names, localhost-only URL, code redaction | camera |

#### T2 — Setup / secrets (first-run trust)

| ID | Case | Module |
|----|------|--------|
| T2.1 | Non-interactive setup: every missing/placeholder/conflict error JSON | setup_cmd |
| T2.2 | `--access-code-env` / file create vs existing | setup_cmd |
| T2.3 | Migrate inline access_code → file; permissions 0600 | migrate |
| T2.4 | Preflight matrix: ok/warn/error, `--strict`, perms 0644 | preflight |
| T2.5 | Guided: headless reject, zeroconf missing fallback, selection bounds | wizard |
| T2.6 | mDNS identity parse edge cases | common / wizard |

#### T3 — Download / extract (security depth)

| ID | Case | Module |
|----|------|--------|
| T3.1 | Content-Disposition RFC 2231 `filename*` | downloader / naming |
| T3.2 | HTML link loop: exhaustion, priority, dedup, hints | html_links |
| T3.3 | Content-Length reject vs mid-stream cap; empty; short read; partial cleanup | downloader |
| T3.4 | ZIP: symlink skip, traversal name, oversize member, no supported member | extract |
| T3.5 | Printables resolution failure payloads | printables + job |

#### T4 — Job / commands / slicer (agent primary path)

| ID | Case | Module |
|----|------|--------|
| T4.1 | Job step-failure detail objects (`*_error` via `_LAST_ERROR_PAYLOAD`) | job |
| T4.2 | `next_command` + `recovery_hint` matrix | job / contracts |
| T4.3 | Dry-run matrix all source types | job |
| T4.4 | Slice missing profiles JSON: `profiles_dir` + `detected_profiles_dir` | slicer |
| T4.5 | Slice option validation errors | slicer |
| T4.6 | Print without `--confirm` refused in job | job |
| T4.7 | Doctor fingerprint offer never in `--json` / non-TTY | commands |

#### T5 — Contracts & schemas (agent A+)

| ID | Work |
|----|------|
| T5.1 | Draft JSON Schema for: `version`, `status`, `status` monitor events, `job` ok/error, `doctor`, `download`, `slice`, `preflight`, `config show` |
| T5.2 | `tests/contracts/test_schema_validation.py` loads schemas, runs fixtures |
| T5.3 | `docs/api.md` generated or explicitly synced with schemas (single source) |
| T5.4 | Golden NDJSON fixtures for monitor stream |

#### T6 — Property / adversarial (correctness A+)

| ID | Work |
|----|------|
| T6.1 | Hypothesis (or manual exhaustive tables) on filename sanitizer |
| T6.2 | Random ZIP member names (traversal, null, unicode, reserved Windows names) |
| T6.3 | IP classification table (RFC1918, ULA, link-local, multicast, mapped) |
| T6.4 | Optional: atheris/fuzz job weekly (A+ stretch) |

---

## 4. Work phases (product + tests interleaved)

Each phase has: **goal**, **engineering tasks**, **test tasks**, **CI gates**,
**acceptance (Definition of Done)**, **score impact**, **exit criteria**.

Do not start the next phase until the previous DoD is green on CI.

---

### Phase 0 — Trust & truth (P0)

**Goal:** Nothing documented lies. Metrics and backlog match reality.

**Duration:** ~1–3 days.

#### Engineering

| # | Task | Files (likely) |
|---|------|----------------|
| 0.1 | Wire `--allow-private-ips` onto `RuntimeContext` / `Settings` after parse in `main()` | `cli.py`, `context.py` |
| 0.2 | Replace bare `except:` in MQTT monitor teardown | `protocols/mqtt.py` |
| 0.3 | Single-source version: `constants.VERSION` from package metadata *or* build writes one file; CONTRIBUTING documents one edit | `constants.py`, `pyproject.toml`, release workflow |
| 0.4 | Refresh `docs/test-backlog.md` with current coverage + remaining gaps only | `docs/test-backlog.md` |
| 0.5 | Link this roadmap from `CONTRIBUTING.md` and `docs/test-backlog.md` | docs |

#### Tests (T0.1–T0.2 minimum)

- CLI e2e: flag on/off for private IP fetch (mocked DNS/connect).
- Regression: default remains deny.

#### CI

- No new floors yet; keep existing green.

#### DoD

- [x] `--allow-private-ips` works end-to-end; default deny preserved.
- [x] No bare `except:` in `bambu_cli/`.
- [x] Version cannot drift without CI failing (tag check already exists; extend if needed).
- [x] Backlog numbers match `pytest --cov` within ~2%.

#### Score impact

| Area | Δ |
|------|---|
| Correctness | B− → A− |
| Security | A− → A |
| Docs | B+ → A− |

---

### Phase A — Testing foundation (tests → A−)

**Goal:** One runner, honest coverage gates, security suite skeleton, transport holes closed enough to trust merges.

**Duration:** ~1–2 weeks.

#### Engineering (only as needed for testability)

| # | Task |
|---|------|
| A.1 | Add pytest markers in `pyproject.toml` / `pytest.ini` |
| A.2 | Add `tests/security/`, `tests/fakes/` packages |
| A.3 | Shared fakes: TLS socket with controllable peer cert DER; FTP size/resume; MQTT client |
| A.4 | Prefer injecting fakes via `BambuPrinter` / protocol functions over patching internals |

#### Tests

| # | Task |
|---|------|
| A.5 | Implement **T1.1–T1.9** (transport + camera) |
| A.6 | Implement **T0.3–T0.6** (SSRF depth) |
| A.7 | Implement **T2.3–T2.4** (migrate + preflight) as quick trust wins |
| A.8 | Enable **branch** coverage report (informational) |

#### CI gates (introduce)

```text
pytest -W error::ResourceWarning --cov=bambu_cli --cov-fail-under=85
# plus existing smokes (agent, package, privacy, help)
```

- [ ] Remove or shrink dedicated unittest module list; pytest collects everything.
- [ ] `bandit -ll` **blocking** (no `|| true`) or allowlist with linked issues.
- [ ] `pip-audit` blocking for high/critical (allowlist documented).

#### DoD

- [ ] mqtt ≥85%, ftps ≥90%, camera ≥90%, netsafety ≥95%.
- [ ] Total coverage ≥85% with fail-under.
- [ ] Single primary test command documented in CONTRIBUTING + CLAUDE/AGENTS.
- [ ] Zero flakes on 10 consecutive local full runs (or CI retries ≤0 on main for a week).

#### Score impact

| Area | Δ |
|------|---|
| Tests | B+ → A− |
| CI / release | B → A− |
| Security | A → A |

---

### Phase B — Error model + seam cleanup (architecture → A)

**Goal:** Domain code raises; `main` exits; tests stop needing SystemExit for happy design.

**Duration:** ~1–2 weeks.

#### Engineering

| # | Task | Notes |
|---|------|-------|
| B.1 | Expand `BambuError` usage: config, download, slice, upload, connect, auth | Keep exit codes identical |
| B.2 | Replace `sys.exit` in `commands`, `job`, `download`, `slicer`, `setup_cmd`, `camera`, `config` with raises | Protocol layer returns errors or raises; no exit in mqtt/ftps |
| B.3 | Ensure `main()` maps `BambuError` → JSON + exit (already mostly there) | |
| B.4 | Extract `bambu_cli/paths.py`, `bambu_cli/jsonio.py`, `bambu_cli/argutils.py` from `cli.py` | Break domain → cli imports |
| B.5 | Single `verify_cert_fingerprint(der, expected)` helper | mqtt + ftps + camera |
| B.6 | Start deleting `@mockable` call sites as tests re-patch real modules | No new `@mockable` |

#### Tests

| # | Task |
|---|------|
| B.7 | Migrate call-site tests from `pytest.raises(SystemExit)` → `BambuError` where handlers invoked directly |
| B.8 | Keep a thin `main()` integration layer that still asserts process exit codes |
| B.9 | **T4.1–T4.3**, **T2.1–T2.2** under new error paths (JSON unchanged) |

#### CI

- [ ] Grep gate: `sys.exit` allowed only in `cli.py` (and maybe `bambu.py` entry). Adjust if scripts need exit.
- [ ] Coverage fail-under → **88%**.

#### DoD

- [ ] `rg 'sys\.exit' bambu_cli` only hits entrypoints.
- [ ] `errors.py` docstring no longer says “not yet converted”.
- [ ] Agent JSON error payloads byte-for-byte compatible (contract tests green).
- [ ] `@mockable` usages reduced by ≥50% (track count in this file).

#### Score impact

| Area | Δ |
|------|---|
| Error model | C → A |
| Architecture | B → A− |
| Tests | A− → A |
| Correctness | A− → A |

---

### Phase C — Coverage completion + typing ratchet (tests → A, typing → A−)

**Goal:** Hit A testing metrics; type the core.

**Duration:** ~2–3 weeks.

#### Tests (finish queue)

| # | Task |
|---|------|
| C.1 | **T2** remaining wizard/mDNS cases |
| C.2 | **T3** full download/extract matrix |
| C.3 | **T4** slicer + doctor + print safety |
| C.4 | Hermetic **fake OrcaSlicer** script in `tests/fakes/orca_stub` (exit codes, stdout, profile paths) |
| C.5 | Coverage total ≥**92%**; module floors per scorecard A+ column for transport/setup/download |

#### Typing

| # | Task | mypy ratchet order |
|---|------|--------------------|
| C.6 | Annotate + enable mypy | `errors` → `netsafety` → `printer` → `protocols/*` → `download/*` → `utils` → `job` → `commands` → `setup_cmd/*` → `camera` → `cli` |
| C.7 | `check_untyped_defs = true` per module as it enters the list | |
| C.8 | Public functions: full param + return types; no bare `Any` without comment | |

#### Complexity budget

| # | Task |
|---|------|
| C.9 | Split `_cmd_snapshot`, large `build_parser` sections, worst slicer helpers | ruff C901 ≤10 on new/changed code |
| C.10 | Optional: enable C901 in CI for `bambu_cli` with baseline ignore file, then burn down | |

#### CI

```text
--cov-fail-under=92
mypy bambu_cli   # full package, or ratchet list = all modules
ruff check bambu_cli
ruff format --check bambu_cli
bandit + pip-audit blocking
```

#### DoD

- [ ] Scorecard **A** column green for Tests and Typing (Typing A− OK if strict not yet full-package).
- [ ] Fake Orca used by default in slice unit tests.
- [ ] `docs/test-backlog.md` reduced to “nice-to-have” only (or empty P1–P5).

#### Score impact

| Area | Δ |
|------|---|
| Tests | A → A (stretch A+) |
| Typing | D → A− |
| Architecture | A− → A |

---

### Phase D — Contracts, schemas, product polish (JSON + product → A+)

**Goal:** Agent surface is specified, versioned, and 1.0-ready.

**Duration:** ~1–2 weeks.

#### Engineering / docs

| # | Task |
|---|------|
| D.1 | `docs/schemas/*.json` for each command payload (ok + error envelope) |
| D.2 | Stability policy in `docs/api.md`: field deprecation = one minor release warning |
| D.3 | Support matrix: OS × Python × printer model × “last tested firmware” (even if partial) |
| D.4 | Remove remaining `mockable`; facade README: “compat only; do not add names” enforced by test that `_FACADE_MODULES` doesn’t grow without allowlist |
| D.5 | Structured logging option if needed by agents (`--log-format json` on stderr) — optional |
| D.6 | Prepare **1.0.0**: changelog, classifier Production/Stable, PyPI |

#### Tests

| # | Task |
|---|------|
| D.7 | **T5** schema validation suite |
| D.8 | **T6.1–T6.3** property tables |
| D.9 | Facade growth guard test |
| D.10 | Release rehearsal: full CI + `uv build` + package_contents + install smoke |

#### DoD

- [ ] Every `--json` command has a schema + contract test.
- [ ] No `@mockable` left (or documented single exception with removal date).
- [ ] Scorecard A+ for Agent JSON, Docs, Product polish.
- [ ] Tag `v1.0.0` only when §5 checklist is complete.

#### Score impact

| Area | Δ |
|------|---|
| Agent JSON UX | A− → A+ |
| Docs / governance | A− → A+ |
| Product polish | C+ → A+ |
| Tests | A → A+ |
| Architecture | A → A+ |

---

### Phase E — Stretch hardening (security / correctness A+)

**Goal:** Hostile inputs and supply chain. Can overlap late Phase C/D.

| # | Task | A+ area |
|---|------|---------|
| E.1 | Weekly fuzz job (ZIP + URL validation) | Security / Correctness |
| E.2 | SBOM on release + optional cosign/sigstore | CI / release |
| E.3 | Dependabot/Renovate + CI on dep PRs | CI |
| E.4 | Optional scheduled live-printer workflow (self-hosted runner) | Tests A+ |
| E.5 | Chaos: FTPS drop mid-STOR, MQTT silent publish | Transport |

#### DoD for A+ security/correctness

- [ ] Adversarial suite green in CI on every PR.
- [ ] Pin verification is one function, three call sites, fully tested.
- [ ] No open P0/P1 issues labeled `security` or `correctness`.

---

## 5. v1.0.0 ship checklist (all A+ gate)

- [ ] Phase 0–D DoD complete; Phase E optional but security adversarial suite required.
- [ ] Scoreboard every row A or A+; none below A.
- [ ] Coverage total ≥92%; mqtt/ftps/netsafety/download floors met.
- [ ] `sys.exit` only at entry; `BambuError` hierarchy used.
- [ ] JSON schemas published; contract tests green.
- [ ] `mockable` gone; no test-awareness in production (CI grep).
- [ ] mypy on full `bambu_cli` (or document residual ignores ≤ N with burn-down).
- [ ] bandit + pip-audit blocking.
- [ ] Single test command; CONTRIBUTING matches CI.
- [ ] Version single-sourced; tag `v1.0.0` matches.
- [ ] SECURITY.md, support matrix, api stability policy current.
- [ ] No known dead flags or stale backlog priorities.

---

## 6. CI end-state (A+)

```yaml
# Conceptual — implement incrementally across phases
jobs:
  test:
    matrix: [py39, py312, py314] x [ubuntu] + [macos/windows @ newest]
    steps:
      - pytest -W error::ResourceWarning
          --cov=bambu_cli --cov-fail-under=92
          -m "not live"
      - existing smokes: agent_cli, package_contents, privacy, help, release_readiness
  lint:
      - ruff check + format
      - mypy bambu_cli
      - bandit -c pyproject.toml -r bambu_cli -ll   # blocking
      - pip-audit                                   # blocking high+
      - no test-awareness grep
      - sys.exit only in entrypoints grep
  security:
      - pytest -m security
  contracts:
      - pytest -m contract
```

---

## 7. Execution model (how to run the plan)

### 7.1 PR sizing

| Phase | Suggested PR shape |
|-------|-------------------|
| 0 | 1 PR: flag wire + tests + bare except + backlog refresh |
| A | 2–4 PRs: fakes + mqtt tests; ftps/camera; netsafety; CI gates |
| B | 2–3 PRs: error migration by package; extract path/jsonio; mockable burn-down |
| C | Multiple small PRs: tests by module; mypy ratchet one module per PR |
| D | schemas PR; stability docs PR; mockable finale; 1.0 prep |
| E | independent as time allows |

### 7.2 Rules of engagement

1. **Tests first or with** behavior changes; never drop contract coverage.
2. **JSON payload compatibility** is sacred across Phase B (error rewrite).
3. **No new facade exports** unless a test absolutely requires it; prefer real module patches.
4. **Update this roadmap** in the same PR that completes a phase DoD.
5. **Do not** expand feature scope (new printer commands, cloud, etc.) until Phase B DoD — quality-only track.

### 7.3 Suggested ownership order if time-boxed

If only **two weeks** available, maximize grade:

1. Phase 0 (all)
2. Phase A tests T1 + T0 + coverage 85% + blocking bandit
3. Phase B.1–B.2 for `download` + `job` only (highest agent value)
4. Defer full mypy / schemas to next cycle

If **full A+** is the goal, follow phases 0→A→B→C→D in order; skip ahead only for pure test PRs that don’t depend on error-model changes.

---

## 8. Tracking table (update in PRs)

| Phase | Status | PR(s) | Completed date | Notes |
|-------|--------|-------|----------------|-------|
| 0 Trust & truth | **done** | local | 2026-07-08 | allow-private-ips, bare except, version single-source |
| A Testing foundation | **done** | local | 2026-07-08 | TLS suite, markers, transport tests, cov~80% |
| B Error model & seams | **partial** | #11 | 2026-07-08 | abort/BambuError; sys.exit entry-only; mockable removed. **B.4** paths/jsonio/argutils extract and **B.5** single pin helper still open (2026-07-17 audit) |
| C Coverage & typing | **in progress** | #18 | 2026-07-09 | full-package mypy + `check_untyped_defs` done; cov ~82% with CI floor **81** (target 92); per-module floors not enforced |
| D Contracts & 1.0 | **in progress** | local | — | schemas + contract harness + stability policy; remaining agent `--json` schemas land in follow-up PRs |
| E Stretch | not started | | | fuzz job, SBOM, dependabot, scheduled live-printer |
| Doc truth pass | **done** | local | 2026-07-17 | AGENTS/CONTRIBUTING/SECURITY/README/api/backlog/roadmap aligned with audit; no code changes required for docs-only pass |

> **Verified 2026-07-09** against a clean checkout — the "current scoreboard" above
> was corrected the same day. Coverage floor raised 79→**81** (multi-OS minimum:
> Linux ~82.3% / Windows ~81.9%). Prior claim of "A+ across the board" did not match
> measured coverage (~82%, not ≥99%), the previous CI floor (79, not 92), the mypy
> excludes, or schema coverage.
>
> **Re-verified 2026-07-17** (full audit): typing excludes remain **gone**; architecture
> grade corrected to **A−** (domain→cli coupling); test count ~618 collected; SECURITY
> known-limitations expanded (camera Docker bind, pin soft-fallback, HTTP downloads).

### mockable count (burn-down)

| Date | `@mockable` sites | Notes |
|------|-------------------|-------|
| 2026-07-08 | 7 (1 def + 6 uses: cli×1, mqtt×5, ftps×1) | Phase B start |
| 2026-07-09 | **0** | ✅ target met — fully removed |

### sys.exit count (burn-down)

| Date | Count in `bambu_cli/` | Notes |
|------|----------------------|-------|
| 2026-07-08 | 130 | Phase B start |
| 2026-07-09 | **entry-only** (`cli.py`; errors.py hits are docstrings) | ✅ target met |

---

## 9. Relationship to other docs

| Doc | Role after this roadmap |
|-----|-------------------------|
| `docs/test-backlog.md` | Short **remaining** test gaps only; refreshed each phase; no stale % |
| `docs/api.md` | Human API reference + stability policy; links schemas |
| `docs/schemas/` | Machine contracts |
| `SECURITY.md` | Threat model + known limitations; update when controls change |
| `CONTRIBUTING.md` | Point at this roadmap + single test command + full gate list |
| `AGENTS.md` | Agent/runtime rules; architecture debt called out honestly |
| `README.md` | User-facing features + full config reference + doc index |

---

## 10. Definition of “we’re done”

You can claim **A+ across the board** when:

1. The scoreboard in §1 is all A+.
2. The testing scorecard A+ column in §3.1 is met.
3. The v1.0.0 checklist in §5 is fully ticked.
4. A cold reader can run **one** documented test command, trust CI, and integrate via **schemas** without reading source.

Until then, advertise **Beta** honestly and ship quality phases in the open via this document.
