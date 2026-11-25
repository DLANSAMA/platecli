# TUI recording for the README. Regenerate from the repo root with:
#   vhs docs/tui.tape
# Requires: vhs, an installed `plate` in .venv/bin WITH the tui extra
# (uv pip install -e '.[tui]'), OrcaSlicer configured, and 3dbenchy.stl in the
# repo root (the README's example model; *.stl is gitignored):
#   plate download "https://www.printables.com/model/3161-3d-benchy" --output .
# Uses `--sim` so it shows populated AMS tray cards, the two-column Prepare
# screen, the interactive Orientation editor (`o`), and the sliced result (pose
# drawing, time and filament) without needing a live printer.
#
# There is no dark/light pair for this one, unlike demo-*/doctor-*: Textual
# paints its own background, so the terminal theme barely shows through. One
# capture serves both README color schemes.
#
# Window must be at least 100 COLUMNS wide: below that the prepare screen
# deliberately collapses to a single column (see PrepareScreen._apply_layout),
# which is not the layout we want to show off. vhs Width/Height are PIXELS,
# not cells — at FontSize 15 Hack a cell is ~10.5x18.5 px. Margin(36)+
# Padding(24) eat 120 px of each axis and WindowBarSize(44) another 44 px of
# the height, so 1100 px is only ~93 columns and records a layout no user
# sees. rows = (Height - 164) / 18.5, so 1200x850 is ~103x37 (570 is only 22
# rows, 610 is the supported 24-row minimum). 37 rows is deliberate: the
# Result column needs about 35 to show the pose drawing, the summary and
# "Start print…" at once. Shorter than that it scrolls to the button, the
# pose sits above the fold, and the scrollbar breaks the drawing. If you change
# FontSize, Padding or Height, recompute this and re-check the frames.
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
Set Width 1200
Set Height 850
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

Type "plate tui --sim"
Sleep 500ms
Enter

# Dashboard: redesigned top bar, heater cards, and color-coded AMS A trays.
Sleep 4.5s

# New print (two-column Prepare screen).
Type "n"
Sleep 1.5s
Type "3dbenchy.stl"
Sleep 1.2s

# Move focus off the input and open the interactive 3D Orientation editor.
Tab
Sleep 400ms
Type "o"
Sleep 3.5s

# Cycle through ranked faces, then snap back to auto and accept.
Type "f"
Sleep 2s
Type "f"
Sleep 2s
Type "a"
Sleep 1.5s
Enter
Sleep 1s

# Run Prepare (real local OrcaSlicer slice) to show the pose + time/filament preview.
Tab
Sleep 200ms
Tab
Sleep 200ms
Tab
Sleep 300ms
Enter
Sleep 10s

# Return to dashboard and quit cleanly.
Escape
Sleep 1s
Type "q"
Sleep 1.5s
