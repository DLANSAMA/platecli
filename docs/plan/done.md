# Done

One line per finished plan box, newest first: `- date · id · what happened · commit`.

- 2026-11-06 · L-3 · closed: folded into L-2, which checks the 0.6.0 install from PyPI (a 0.5.1 check is stale once 0.6.0 ships) · this commit
- 2026-11-06 · D-1 · PR #153 (audit round 4) squash-merged after all 6 checks passed on its last commit · e925f96
- 2026-11-05 · S-3 · setup shows a lone mDNS result and asks (no → manual entry); unspecified/multicast addresses are skipped, public/loopback ones need an explicit yes, also when picked from several · this commit
- 2026-11-05 · S-2 · real (the slot was read once, at material choice): `plate go` and the TUI re-read the AMS on Start print and refuse with nothing sent if it changed or can't be read; confirm summary gains Feed and Temperatures rows; pilot + wizard tests · 8cae64c
- 2026-11-05 · S-1 · `upload`/`job`/`send` scan every `.gcode` member of a 3MF (and plain `.gcode`) for M104/M109/M140/M190 over 350/150 °C before upload; `job --dry-run` reports it too; unreadable members fail closed; 0.5 s for 2M lines · ef01a1d
