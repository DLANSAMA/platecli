#!/usr/bin/env python3
"""Enforce the package's layer boundaries.

``bambu_cli`` is layered: a module may import from a *strictly lower* rank, and
from its own rank only when that rank is declared cohesive. The point is the
rule in AGENTS.md — a change to the OrcaSlicer runner or the Printables scraper
must not reach into the Bambu protocol code, and vice versa.

Directories alone never enforced this: ``protocols/``, ``slicer/`` and
``download/`` already existed as separate packages and drifted anyway (a slicer
module was importing a private FTPS helper). Hence a checker in the lint job.

Deferred (function-local) imports count. They break the *import* cycle but not
the *dependency* — a module that reaches for a collaborator inside a function
body is still coupled to it, and still untestable without it. They are reported
separately only so the output says which kind you are looking at.

Run:  python scripts/check_layers.py
Exit: 0 clean, 1 on any un-allowlisted violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "bambu_cli"

# ---------------------------------------------------------------------------
# The layering. Lower rank = more foundational.
# ---------------------------------------------------------------------------
# Ranks are spaced so a unit can be inserted without renumbering the world.
RANKS: dict[str, int] = {
    # 10 — primitives. No knowledge of config, transport, or commands.
    "constants": 10,
    "errors": 10,
    "paths": 10,
    "logging_utils": 10,
    "argutils": 10,
    "jsonio": 10,
    "tlspin": 10,
    "fsutil": 10,
    # Typed --json payload shapes. Pure data: stdlib only, imports nothing from
    # the package, and generates docs/schemas/. Any layer may build one.
    "contracts": 10,
    # 20 — core services: process-wide config/runtime state and shared helpers.
    "utils": 20,
    "config": 20,
    "context": 20,
    "netsafety": 20,
    "ams": 20,
    # 25 — the declarative argparse tree. Imports only constants/utils, so any
    #      layer may build a namespace from it without touching the entrypoint.
    "cliparse": 25,
    # 30 — adapters to the outside world. SIBLINGS MUST NOT IMPORT EACH OTHER.
    #      This is the boundary the whole refactor exists to protect.
    "protocols": 30,  # IoT: Bambu MQTT / FTPS / camera
    "slicer": 30,  # subprocess: OrcaSlicer
    "printables": 30,  # web: the undocumented Printables API
    # 35 — transport facade over protocols/.
    "printer": 35,
    # 40 — use cases.
    "download": 40,
    "setup_cmd": 40,
    # 45 — job orchestrates the use cases below it (download -> slice -> print).
    "job": 45,
    # 50 — user interfaces.
    "commands": 50,
    "interactive": 50,
    "tui": 50,
    # 70 — entrypoint. sys.exit lives here and nowhere else.
    "cli": 70,
    # 80 — the __main__ shim that console_scripts and `python -m` land on.
    "bambu": 80,
}

# Ranks whose members may import each other freely. Rank 30 is deliberately
# absent: adapter isolation is the invariant under protection.
COHESIVE_RANKS = {10, 20, 50}

# ---------------------------------------------------------------------------
# Accepted debt. Every entry is a real violation that predates this checker and
# is scheduled, not excused. Shrink this list; do not grow it. Empty: the last
# allowlisted edge (context -> printer) was replaced by a printer factory
# registered downward from bambu_cli.printer.
# ---------------------------------------------------------------------------
ALLOWED: dict[tuple[str, str], str] = {}


# ---------------------------------------------------------------------------
# Package internals that must not be imported from outside their own package.
# An adapter is only a sandbox if callers cannot reach past it.
# ---------------------------------------------------------------------------
SEALED: dict[str, str] = {
    "bambu_cli.printables.client": (
        "the raw Printables GraphQL wire format — import from bambu_cli.printables instead, "
        "so a schema change stays contained in the adapter"
    ),
    "bambu_cli.printables.adapter": (
        "internal; the public names are re-exported from bambu_cli.printables"
    ),
}


def unit_of(module: str) -> str | None:
    """Map a dotted module path to the layer unit that owns it."""
    parts = module.split(".")
    if not parts or parts[0] != "bambu_cli":
        return None
    if len(parts) == 1:
        return None  # bare `import bambu_cli` — no edge
    return parts[1]


def source_unit(path: Path) -> str:
    rel = path.relative_to(PKG).parts
    return rel[0] if (PKG / rel[0]).is_dir() else path.stem


def iter_raw_imports():
    """Yield (file, lineno, dotted_module) for every bambu_cli import."""
    for file in sorted(PKG.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                if node.module.startswith("bambu_cli"):
                    yield file, node.lineno, node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("bambu_cli"):
                        yield file, node.lineno, alias.name


def sealed_violations():
    """Imports that reach into another package's sealed internals."""
    out = []
    for file, lineno, module in iter_raw_imports():
        for sealed, why in SEALED.items():
            if module == sealed or module.startswith(sealed + "."):
                owner = PKG / sealed.split(".")[1]
                if owner not in file.parents:
                    out.append((file, lineno, module, why))
    return out


def iter_edges():
    """Yield (src_unit, dst_unit, file, lineno, deferred)."""
    for file in sorted(PKG.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        src = source_unit(file)
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))

        # Mark nodes that sit inside a function body: those are deferred imports.
        deferred_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    deferred_nodes.add(id(inner))

        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:  # relative import — same unit by construction
                    continue
                if node.module and node.module.startswith("bambu_cli"):
                    targets = [node.module]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names if a.name.startswith("bambu_cli")]
            for target in targets:
                dst = unit_of(target)
                if dst is None or dst == src:
                    continue
                yield src, dst, file, node.lineno, id(node) in deferred_nodes


def main() -> int:
    unknown: list[str] = []
    violations: list[tuple[str, str, Path, int, bool, str]] = []
    used_allowances: set[tuple[str, str]] = set()

    for src, dst, file, lineno, deferred in iter_edges():
        if src not in RANKS:
            note = f"unit {src!r} has no rank in scripts/check_layers.py"
            if note not in unknown:
                unknown.append(note)
            continue
        if dst not in RANKS:
            unknown.append(f"{file}:{lineno}: imports unranked unit {dst!r}")
            continue

        src_rank, dst_rank = RANKS[src], RANKS[dst]
        if src_rank > dst_rank:
            continue  # downward — always fine
        if src_rank == dst_rank and src_rank in COHESIVE_RANKS:
            continue

        if (src, dst) in ALLOWED:
            used_allowances.add((src, dst))
            continue

        if src_rank == dst_rank:
            why = f"sibling import at rank {src_rank} (adapters must stay isolated)"
        else:
            why = f"upward import: rank {src_rank} -> rank {dst_rank}"
        violations.append((src, dst, file, lineno, deferred, why))

    for message in unknown:
        print(f"UNRANKED  {message}")

    for src, dst, file, lineno, deferred, why in violations:
        kind = "deferred" if deferred else "module-level"
        rel = file.relative_to(ROOT)
        print(f"VIOLATION {rel}:{lineno}: {src} -> {dst} ({why}, {kind})")

    sealed = sealed_violations()
    for file, lineno, module, why in sealed:
        print(f"SEALED    {file.relative_to(ROOT)}:{lineno}: imports {module} — {why}")

    stale = set(ALLOWED) - used_allowances
    for src, dst in sorted(stale):
        print(f"STALE     allowance {src} -> {dst} is no longer needed; remove it from ALLOWED")

    failures = len(violations) + len(unknown) + len(stale) + len(sealed)
    if failures:
        print(f"\n{failures} layering problem(s). See the rank table in {Path(__file__).name}.")
        return 1

    allowed_note = f" ({len(ALLOWED)} allowlisted debt edge(s))" if ALLOWED else ""
    print(f"Layer boundaries OK{allowed_note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
