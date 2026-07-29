# Hero recording for the Reddit/launch post: one real `plate job` run,
# Printables URL -> download -> slice -> upload -> print started.
# Regenerate from the repo root with:
#   vhs docs/job-hero.tape
# Requires: vhs, an installed `plate` in .venv/bin, a reachable printer with
# LAN mode on, OrcaSlicer installed, and ~15 min of margin.
#
# THIS STARTS A REAL PRINT (3DBenchy, ~13 g). Cancel afterwards with
#   plate stop --confirm
# or from the printer screen. Delete slice strays afterwards (result.json,
# *_sliced.3mf, the downloaded STL) or privacy_smoke gains lines.
#
# The raw capture is long (slicing takes ~1-2 min of dead air); produce the
# postable cut with scripts/trim_hero.sh (speeds up the quiet middle).
# Before posting: extract frames with ffmpeg and LOOK at them for IP/serial
# leaks — do not trust the tape (docs/media lesson, 2026-07-26).

Output docs/job-hero.mp4

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1100
Set Height 420
Set Padding 24
Set Margin 36
Set MarginFill "#16295e"
Set BorderRadius 14
Set WindowBar Colorful
Set WindowBarSize 44
Set Theme { "name": "platecli", "background": "#1c1f26", "foreground": "#e6edf3", "cursor": "#5eead4", "black": "#2a2e38", "red": "#ff5f57", "green": "#28c840", "yellow": "#febc2e", "blue": "#58a6ff", "magenta": "#bc8cff", "cyan": "#5eead4", "white": "#e6edf3", "brightBlack": "#8b949e", "brightRed": "#ff7b72", "brightGreen": "#3fb950", "brightYellow": "#d29922", "brightBlue": "#79c0ff", "brightMagenta": "#d2a8ff", "brightCyan": "#5eead4", "brightWhite": "#f5f5f5" }

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Show

Sleep 1s
Type "plate job https://www.printables.com/model/3161-3d-benchy --confirm"
Sleep 800ms
Enter
# Real run: download (~10 s) + slice (~1-2 min) + upload + print start.
# Generous ceiling; trim_hero.sh compresses the dead air afterwards.
Sleep 300s

Sleep 2s
Type "plate status"
Sleep 500ms
Enter
Sleep 8s
Sleep 3s
