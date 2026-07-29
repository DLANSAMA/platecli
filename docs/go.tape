# Demo recording for the `plate go` guided wizard. Regenerate from the repo root:
#   vhs docs/go.tape
# Requires: vhs, an installed `plate` in .venv/bin, a reachable configured
# printer (the wizard's final step starts a real print — record against a
# printer you are ready to run, or Ctrl-C before the final confirm).
# Styling matches docs/demo-dark.tape (dark, rounded, traffic lights).

Output docs/go-dark.gif

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1100
Set Height 560
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

# Bare `plate` on a terminal drops straight into the guided wizard.
Type "plate go"
Sleep 500ms
Enter
Sleep 2s

# Step 1 — paste a model URL.
Type "https://www.printables.com/model/3161-3d-benchy"
Sleep 500ms
Enter
Sleep 2s

# Step 2 — confirm the printer (default yes).
Enter
Sleep 1s

# Step 3 — material (AMS default pre-selected; accept it).
Enter
Sleep 1s

# Step 4 — quality (standard default).
Enter
Sleep 1s

# Step 5 — supports (default no).
Enter
Sleep 6s

# Step 7 — the preview shows, then the default-No confirm.
# Stop here in the recording rather than starting a physical print.
Sleep 2s
