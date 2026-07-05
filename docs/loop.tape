# docs/loop.tape — renders docs/loop.gif.
#
# Re-render on demand (from the repo root):   vhs docs/loop.tape
# Requires: vhs + ttyd + ffmpeg  (brew install vhs).
#
# The gif's content is produced by docs/loop-demo.sh, a deterministic presenter
# that reproduces what /wrap and /pickup actually print. See that file's header
# for the faithfulness rule. This tape only sets the terminal up and records it.

Output docs/loop.gif

Set Shell bash
Set FontSize 19
Set Width 1280
Set Height 600
Set Padding 26
Set Margin 0
Set BorderRadius 0
Set Framerate 24
Set Theme { "name": "legwork", "background": "#0c0c0f", "foreground": "#d4d4d4", "cursor": "#d99a5c", "black": "#1a1a1e", "red": "#e06c75", "green": "#98c379", "yellow": "#d99a5c", "blue": "#61afef", "magenta": "#c678dd", "cyan": "#56b6c2", "white": "#d4d4d4", "brightBlack": "#5c6370", "brightRed": "#e06c75", "brightGreen": "#98c379", "brightYellow": "#d99a5c", "brightBlue": "#61afef", "brightMagenta": "#c678dd", "brightCyan": "#56b6c2", "brightWhite": "#ffffff" }

# Launch the presenter unrecorded; it clears and holds briefly, then we record.
Hide
Type "bash docs/loop-demo.sh" Enter
Show
Sleep 16s
