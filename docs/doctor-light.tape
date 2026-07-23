# Doctor-demo recording for README (mid-page). Regenerate from the repo root:
#   vhs docs/doctor-light.tape
# Requires: vhs, an installed `plate` in .venv/bin, and a reachable printer
# (doctor is read-only). Styling matches docs/demo.tape / the social card.

Output docs/doctor-light.gif

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1100
Set Height 480
Set Padding 24
Set Margin 36
Set MarginFill "#f6f8fa"
Set BorderRadius 14
Set WindowBar Colorful
Set WindowBarSize 44
Set Theme { "name": "platecli-light", "background": "#ffffff", "foreground": "#1f2328", "cursor": "#0f766e", "black": "#24292f", "red": "#cf222e", "green": "#1a7f37", "yellow": "#9a6700", "blue": "#0969da", "magenta": "#8250df", "cyan": "#0f766e", "white": "#6e7781", "brightBlack": "#57606a", "brightRed": "#a40e26", "brightGreen": "#1a7f37", "brightYellow": "#bf8700", "brightBlue": "#218bff", "brightMagenta": "#a475f9", "brightCyan": "#0f766e", "brightWhite": "#1f2328" }

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Show

Type "plate doctor"
Sleep 500ms
Enter
Sleep 9s
