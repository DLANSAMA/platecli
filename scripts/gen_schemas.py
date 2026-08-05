#!/usr/bin/env python3
"""Generate docs/schemas/*.json from the contracts in bambu_cli.contracts.

The schemas used to be hand-written, which meant they drifted from what the
commands actually emitted. Now they are derived, and CI regenerates and diffs
them, so drift is a build failure instead of a support ticket.

    python scripts/gen_schemas.py            # write docs/schemas/
    python scripts/gen_schemas.py --check    # fail if anything is stale

**Pydantic is a dev-only dependency.** It is used here, at build time, purely
to turn dataclass annotations into JSON Schema. It is not a runtime dependency
and is never imported by the package — ``bambu_cli.utils.emit_json`` still owns
serialization, because that pass applies credential redaction that a
``model_dump_json()`` would bypass.

Requires Python 3.10+: the contracts annotate optionals as ``X | None``, which
only *evaluates* on 3.10+. The package itself never evaluates them (it reads
``dataclasses.fields()``), so runtime support for the 3.9 floor is unaffected.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "docs" / "schemas"
SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_BASE = "https://platecli.local/schemas"

if sys.version_info < (3, 10):  # pragma: no cover -- guarded in CI by job config
    raise SystemExit(
        "gen_schemas.py needs Python 3.10+ to evaluate `X | None` annotations.\n"
        "This is a dev/build tool only — the package still supports 3.9."
    )

sys.path.insert(0, str(ROOT))

try:
    from pydantic import TypeAdapter
except ModuleNotFoundError:  # pragma: no cover -- dev dependency
    raise SystemExit(
        "pydantic is required to generate schemas (dev-only dependency).\n"
        "Install it with:  uv pip install '.[test]'"
    ) from None

from bambu_cli.contracts import all_contracts  # noqa: E402


def _strip_noise(node):
    """Remove pydantic bookkeeping that is not part of the published contract.

    ``title`` is derived from the Python identifier and would leak field naming
    into the public schema; ``default`` restates what ``required`` already says
    and would churn the diff whenever a default changes.
    """
    if isinstance(node, dict):
        return {k: _strip_noise(v) for k, v in node.items() if k not in ("title", "default")}
    if isinstance(node, list):
        return [_strip_noise(v) for v in node]
    return node


def _inline_defs(schema):
    """Inline ``$defs``/``$ref`` so each published schema stands alone.

    Consumers read one file; a ``$ref`` into ``$defs`` would make them resolve
    references for no benefit at this size.
    """
    defs = schema.pop("$defs", None)
    if not defs:
        return schema

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                # The referring site wins. A field's own description and its
                # `requires_keys` are more specific than anything on the shared
                # model, and letting the target overwrite them silently dropped
                # `status.printer`'s description and required list.
                merged = walk(dict(target))
                merged.update({k: v for k, v in node.items() if k != "$ref"})
                return merged
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _normalize_optional(prop, *, nullable):
    """Turn pydantic's ``anyOf: [X, null]`` into what this project publishes.

    Two reasons not to ship the ``anyOf`` form:

    * It is inaccurate for most fields. ``Contract.to_payload`` *omits* an unset
      optional rather than emitting ``null``, so ``null`` is not a value the
      field can actually take — only ``keep_none`` fields are emitted as null.
    * The contract test's validator (tests/contracts/test_schema_validation.py)
      understands ``type`` but not ``anyOf``, so an ``anyOf`` property would be
      silently skipped — a schema that looks stricter while checking less.

    So: collapse to the bare type, or to ``type: [X, "null"]`` when null really
    is emitted (the form ``setup.model`` already used).
    """
    branches = prop.get("anyOf")
    if not branches:
        return prop
    non_null = [b for b in branches if b.get("type") != "null"]
    has_null = len(non_null) != len(branches)
    if len(non_null) != 1 or not has_null:
        return prop

    merged = {k: v for k, v in prop.items() if k != "anyOf"}
    merged.update(non_null[0])
    if nullable and isinstance(merged.get("type"), str):
        merged["type"] = [merged["type"], "null"]
    return merged


def _apply_field_metadata(contract, properties):
    """Fold each field's declared constraints into its property schema.

    pydantic derives type/const/enum from the annotation; ``spec(...)`` metadata
    on the dataclass field carries the rest of the published contract
    (minLength, minimum, description, nested required) so it stays on the model
    rather than in a side table.
    """
    extra_required = set()
    keep_none = set(getattr(contract, "keep_none", frozenset()))
    for f in dataclasses.fields(contract):
        prop = properties.get(f.name)
        if prop is None:
            continue
        prop = _normalize_optional(prop, nullable=f.name in keep_none)
        properties[f.name] = prop
        if f.metadata.get("contract_required"):
            extra_required.add(f.name)
        if "min_length" in f.metadata:
            prop["minLength"] = f.metadata["min_length"]
        if "minimum" in f.metadata:
            prop["minimum"] = f.metadata["minimum"]
        if "description" in f.metadata:
            prop["description"] = f.metadata["description"]
        if "requires_keys" in f.metadata:
            prop["required"] = list(f.metadata["requires_keys"])
    return extra_required


def _dataclass_named(name):
    """Look up a nested model by the class name pydantic used as its $defs key."""
    from bambu_cli.contracts import models

    obj = getattr(models, name, None)
    return obj if dataclasses.is_dataclass(obj) else None


def _apply_to_object(model, node):
    """Apply a model's declared constraints to its object schema node, in place."""
    properties = node.setdefault("properties", {})
    extra_required = _apply_field_metadata(model, properties)
    derived = set(node.get("required", [])) | extra_required
    node["required"] = [f for f in properties if f in derived]
    # Nested objects stay permissive, same as the top-level contracts: they name
    # the guaranteed keys and tolerate extra detail from the printer/slicer.
    node.setdefault("additionalProperties", True)


def schema_for(contract):
    """Build the published schema document for one contract."""
    raw = _strip_noise(TypeAdapter(contract).json_schema())

    # Nested models carry their own spec() metadata, so process $defs *before*
    # inlining — once inlined there is no way back to the owning dataclass.
    for name, node in (raw.get("$defs") or {}).items():
        model = _dataclass_named(name)
        if model is not None and node.get("type") == "object":
            # pydantic publishes a dataclass's docstring as `description`.
            # Docstrings are for developers; only descriptions declared through
            # spec(...)/schema_description belong in the published contract.
            node.pop("description", None)
            _apply_to_object(model, node)

    properties = raw.get("properties", {})
    extra_required = _apply_field_metadata(contract, properties)
    derived = set(raw.get("required", [])) | extra_required
    # Ordered by declaration, so the schema's `required` reads like the payload.
    required = [f for f in properties if f in derived]

    raw["properties"] = properties
    raw = _inline_defs(raw)
    properties = raw.get("properties", {})

    doc = {
        "$schema": SCHEMA_URI,
        "$id": f"{SCHEMA_ID_BASE}/{contract.schema_name}.json",
        "title": contract.schema_title,
        "type": "object",
    }
    if contract.schema_description:
        doc["description"] = contract.schema_description
    doc["required"] = required
    doc["properties"] = properties
    doc["additionalProperties"] = contract.additional_properties
    return doc


def render(contract):
    return json.dumps(schema_for(contract), indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if any schema is stale (CI drift gate)")
    args = parser.parse_args(argv)

    contracts = all_contracts()
    if not contracts:
        raise SystemExit("no contracts found in bambu_cli.contracts.models")

    expected = {f"{c.schema_name}.json": render(c) for c in contracts}
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    on_disk = {p.name for p in SCHEMA_DIR.glob("*.json")}

    stale, missing = [], []
    for name, body in expected.items():
        path = SCHEMA_DIR / name
        if not path.is_file():
            missing.append(name)
        elif path.read_text(encoding="utf-8") != body:
            stale.append(name)

    # A schema with no contract is drift in the other direction: it would keep
    # being published while nothing generates or checks it.
    orphaned = sorted(on_disk - set(expected))

    if args.check:
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(sorted(missing))}")
        if stale:
            problems.append(f"stale: {', '.join(sorted(stale))}")
        if orphaned:
            problems.append(f"no contract generates: {', '.join(orphaned)}")
        if problems:
            print("docs/schemas is out of sync with bambu_cli.contracts:")
            for line in problems:
                print(f"  - {line}")
            print("\nRun: python scripts/gen_schemas.py")
            return 1
        print(f"docs/schemas up to date ({len(expected)} schemas).")
        return 0

    if orphaned:
        print(f"warning: {', '.join(orphaned)} has no contract — delete it or add a model.")
    written = 0
    for name, body in expected.items():
        path = SCHEMA_DIR / name
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written += 1
            print(f"wrote {path.relative_to(ROOT)}")
    print(f"{len(expected)} schemas ({written} changed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
