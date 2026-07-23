# Doctor-demo recording for README (mid-page). Regenerate from the repo root:
#   vhs docs/doctor-dark.tape
# Requires: vhs, an installed `plate` in .venv/bin, and a reachable printer
# (doctor is read-only). Styling matches docs/demo.tape / the social card.

Output docs/doctor-dark.gif

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1100
Set Height 480
Set Padding 24
Set Margin 36
Set MarginFill "#14161b"
Set BorderRadius 14
Set WindowBar Colorful
Set WindowBarSize 44
Set Theme { "name": "platecli", "background": "#1c1f26", "foreground": "#e6edf3", "cursor": "#5eead4", "black": "#2a2e38", "red": "#ff5f57", "green": "#28c840", "yellow": "#febc2e", "blue": "#58a6ff", "magenta": "#bc8cff", "cyan": "#5eead4", "white": "#e6edf3", "brightBlack": "#8b949e", "brightRed": "#ff7b72", "brightGreen": "#3fb950", "brightYellow": "#d29922", "brightBlue": "#79c0ff", "brightMagenta": "#d2a8ff", "brightCyan": "#5eead4", "brightWhite": "#f5f5f5" }

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Show

Type "plate doctor"
Sleep 500ms
Enter
Sleep 9s
