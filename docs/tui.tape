# TUI recording for the README. Regenerate from the repo root with:
#   vhs docs/tui.tape
# Requires: vhs, an installed `plate` in .venv/bin WITH the tui extra
# (uv pip install -e '.[tui]'), and a reachable printer. Recorded against a
# REAL printer, mid-print: the dashboard is a live view, and an idle printer
# records as 0% with a static progress row, which undersells it. Start the
# print first, wait for RUNNING, then run this tape.
#
# `plate tui --sim` also records fine and needs no printer, if you ever want a
# capture that shows populated AMS trays on a machine that has no AMS.
#
# There is no dark/light pair for this one, unlike demo-*/doctor-*: Textual
# paints its own background, so the terminal theme barely shows through. One
# capture serves both README color schemes.
#
# Window must be at least 100 COLUMNS wide: below that the prepare screen
# deliberately collapses to a single column (see PrepareScreen._apply_layout),
# which is not the layout we want to show off. vhs Width/Height are PIXELS,
# not cells — at FontSize 15 Hack a cell is ~10.5x18.6 px, and Margin(36)+
# Padding(24) eat 120 px of each axis, so 1100 px is only ~93 columns and
# records the narrow layout. 1400x700 gives ~122x31 — tall enough that the
# prepare form shows its Prepare/Settings buttons without scrolling. If you change FontSize
# or Padding, recompute this and re-check the frames.
#
# Before committing a re-record: extract frames with ffmpeg and LOOK at them
# for IP/serial/home-path leaks — do not trust the tape (media lesson,
# 2026-07-26; the doctor GIFs once shipped a printer IP and a cert
# fingerprint). --sim avoids real printer data, but the shell prompt and any
# paths on screen are still yours.

Output docs/tui.gif

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1400
Set Height 700
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

Type "plate tui"
Sleep 500ms
Enter

# Dashboard: state, temperatures, layer and progress, AMS trays — refreshing
# on a timer against the real printer.
Sleep 8s

# The job monitor: follows the running print toward its terminal state.
Type "m"
Sleep 7s
Escape
Sleep 1s

# The prepare screen — the two-column form the 0.5.0 restructure is about.
Type "n"
Sleep 5s

# Back out and quit cleanly so the last frame is a shell prompt, not a
# half-torn-down alternate screen buffer.
Escape
Sleep 1s
Type "q"
Sleep 2s
