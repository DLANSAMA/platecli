#!/usr/bin/env python3
"""Run the CLI straight from a source checkout, without installing it.

Python puts *this file's* directory (``scripts/``) on ``sys.path`` — not the
repo root — so ``bambu_cli`` is not importable here by default. Prepend the
repo root before importing, or ``python scripts/bambu.py`` fails with
``ModuleNotFoundError: No module named 'bambu_cli'`` on a clean checkout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bambu_cli.bambu import main  # noqa: E402  (must follow the sys.path fix)

if __name__ == "__main__":
    main()
