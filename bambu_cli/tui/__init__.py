"""The ``plate tui`` full-screen terminal UI (Textual, optional extra).

A new front-end over existing machinery, not new machinery: the TUI reads the
same ``printer.status()`` / ``parse_ams`` the CLI does and (in later phases)
drives the same download/slice/job pipeline through the injectable ``GoSteps``
seam. Textual is an *optional* dependency (`pip install 'platecli[tui]'`); this
module keeps its imports light so that resolving ``cmd_tui`` never pulls Textual
in eagerly, and ``cmd_tui`` aborts with a clear message when the extra is absent.

Only ``cmd_tui`` is exported at package level; everything heavy (the Textual
``App``, screens, widgets) is imported lazily inside ``cmd_tui``.
"""

from __future__ import annotations

from bambu_cli.tui.entry import cmd_tui

__all__ = ["cmd_tui"]
