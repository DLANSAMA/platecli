# platecli — the plan

The work we chose, in order. **No task from the user? Take the top 🤖 box under *Next* that isn't claimed or blocked.** Everything else noticed goes in `docs/plan/inbox.md`. Rules: the `master-plan` skill. Limits: 300 lines; a box is at most 5 lines.

`docs/plans/` holds finished design records (TUI, guided mode, the A+ and post-audit gameplans); `docs/quality-roadmap.md` is the frozen quality scoreboard. Neither is a to-do list.

## Goals

`plate` lets a person or an agent go from a model file or Printables link to a print on a Bambu Lab printer over the LAN, safely: nothing moves or heats without `--confirm`, every command speaks `--json`, and no secret reaches a log. Engineering is at an honest A−; the 2026-09-01 audit froze quality work until there are outside users, so launch steps come first.

## Waiting on you 🧑

- [ ] **D-2 · Dependabot PRs #150 (actions) and #152 (python deps)** · P2 · 🧑
  Open since 2026-10-26 and 2026-11-02. *Done when:* merged or closed.
- [ ] **L-1 · Post to r/BambuLab** · P1 · both
  🤖 rewrites the held copy to lead with `plate tui`/`plate go`; 🧑 posts (Tue–Thu, 10–11 EDT).

## Next

- [ ] **L-4 · Re-record the hero GIF with `plate job`** · P2 · both
  The README hero shows `status`/`slice`, not the command the pitch rests on. Recording starts a real print: 🧑 says yes and is at the printer.

## Later

Nothing chosen yet; new findings go in `docs/plan/inbox.md`.

**Highest ids given:** D-3 · L-4 · S-3 · retired: D-1, D-3, L-2, L-3, S-1, S-2, S-3
