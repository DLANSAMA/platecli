# Demo recording for README. Regenerate from the repo root with:
#   vhs docs/demo-light.tape
# Requires: vhs, an installed `plate` in .venv/bin, a reachable printer
# (status is read-only), and side-v2a.stl (or any STL) in the repo root.
# Styling matches the repo social-preview card (dark, rounded, traffic lights).

Output docs/demo-light.gif

Set Shell "bash"
Set FontFamily "Hack"
Set FontSize 15
Set Width 1100
Set Height 420
Set Padding 24
Set Margin 36
Set MarginFill "#1e3a8a"
Set BorderRadius 14
Set WindowBar Colorful
Set WindowBarSize 44
Set Theme { "name": "platecli-light", "background": "#ffffff", "foreground": "#1f2328", "cursor": "#0f766e", "black": "#24292f", "red": "#cf222e", "green": "#1a7f37", "yellow": "#9a6700", "blue": "#0969da", "magenta": "#8250df", "cyan": "#0f766e", "white": "#6e7781", "brightBlack": "#57606a", "brightRed": "#a40e26", "brightGreen": "#1a7f37", "brightYellow": "#bf8700", "brightBlue": "#218bff", "brightMagenta": "#a475f9", "brightCyan": "#0f766e", "brightWhite": "#1f2328" }

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Show

Type "plate status"
Sleep 500ms
Enter
Sleep 6s

Type "plate slice side-v2a.stl"
Sleep 500ms
Enter
Sleep 6s
