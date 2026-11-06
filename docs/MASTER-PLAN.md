# platecli — the plan

The work we chose, in order. **No task from the user? Take the top 🤖 box under *Next* that isn't claimed or blocked.** Everything else noticed goes in `docs/plan/inbox.md`. Rules: the `master-plan` skill. Limits: 300 lines; a box is at most 5 lines.

`docs/plans/` holds finished design records (TUI, guided mode, the A+ and post-audit gameplans); `docs/quality-roadmap.md` is the frozen quality scoreboard. Neither is a to-do list.

## Goals

`plate` lets a person or an agent go from a model file or Printables link to a print on a Bambu Lab printer over the LAN, safely: nothing moves or heats without `--confirm`, every command speaks `--json`, and no secret reaches a log. Engineering is at an honest A−; the 2026-09-01 audit froze quality work until there are outside users, so launch steps come first.

## Waiting on you 🧑

- [ ] **D-1 · Merge the audit-round4 PR (#153)** · P1 · 🧑
  Branch `fix/audit-round4`: at-most-once MQTT delivery, pin precedence, heater limits for raw and pre-sliced G-code, AMS re-read before a guided print, terminal-escape hardening, config value checks. Needs the 6 CI checks green.
- [ ] **D-2 · Dependabot PRs #150 (actions) and #152 (python deps)** · P2 · 🧑
  Open since 2026-10-26 and 2026-11-02. *Done when:* merged or closed.
- [ ] **D-3 · Keep `"bambu"` in the PyPI keywords?** · P2 · 🧑
  Unruled since 2026-07-25; recommendation was keep (nominative search term, not a name).
- [ ] **L-1 · Post to r/BambuLab** · P1 · both · after L-2
  🤖 rewrites the held copy to lead with `plate tui`/`plate go`; 🧑 posts (Tue–Thu, 10–11 EDT).
- [ ] **L-2 · Cut 0.6.0** · P1 · both · after D-1
  `[Unreleased]` holds the A1 slicing fix and two audit waves. 🤖 prepares the release PR per `docs/releasing.md`; 🧑 approves the tag push (it publishes to PyPI).

## Next

- [ ] **S-3 · Setup takes a lone mDNS result without asking** · P2 · 🤖
  The wizard auto-selects a single discovered printer and does not sanity-check its IP (private range, not 0.0.0.0).
  *Done when:* a single result is shown and confirmed, a public or unspecified IP is flagged, and a test pins both.
- [ ] **L-3 · Check that 0.5.1 installs from PyPI** · P2 · 🤖
  Never verified after the 2026-08-28 publish. Clean venv, `pip install platecli==0.5.1`, `plate --version`, `plate --sim status`.
  *Done when:* the result is recorded in `docs/plan/done.md`.

## Later

- [ ] **L-4 · Re-record the hero GIF with `plate job`** · P2 · both
  The README hero shows `status`/`slice`, not the command the pitch rests on. Recording starts a real print: 🧑 says yes and is at the printer.

**Highest ids given:** D-3 · L-4 · S-3 · retired: S-1, S-2
