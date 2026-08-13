"""Base machinery for command-output contracts.

A contract is a frozen dataclass describing the JSON one command emits. It is
the single source of truth: ``docs/schemas/*.json`` is *generated* from these
(``scripts/gen_schemas.py``), and CI fails if the committed schemas drift from
what the models say. There is no hand-maintained schema any more.

Two deliberate choices:

**Plain dataclasses, not pydantic models.** ``bambu_cli.utils.emit_json``
already owns serialization, and that pass is security-critical — it redacts URL
credentials and compacts home directories on *every* emitted string. A pydantic
``.model_dump_json()`` would bypass it. Pydantic is a **dev-only** dependency
used by the generator to derive JSON Schema from these dataclasses; it is never
imported at runtime and never ships to users.

**Annotations are never evaluated at runtime.** They use ``X | None`` (PEP 604),
which only *evaluates* on Python 3.10+. ``from __future__ import annotations``
keeps them as strings, and everything here reads fields via
``dataclasses.fields()`` rather than ``typing.get_type_hints()``, so the package
imports and works fine on the 3.9 floor. Only the generator resolves them, and
it requires 3.10+.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

_UNSET = object()


def spec(
    *,
    default=_UNSET,
    default_factory=_UNSET,
    required: bool = False,
    min_length: int | None = None,
    minimum: int | None = None,
    description: str | None = None,
    requires_keys: tuple[str, ...] | None = None,
):
    """Declare a contract field with its published JSON Schema constraints.

    The constraints live on the field so the generator can derive the schema
    from the model alone — a separate constraints table would be exactly the
    hand-maintained parallel list this refactor is removing.

    ``required=True`` marks a field contractually required even though it
    carries a Python default. That combination is unavoidable: dataclasses
    force defaulted fields last, but the published key order is part of the
    contract and several required keys follow optional ones.

    ``requires_keys`` is the nested ``required`` list for an object-typed field
    (e.g. ``status.printer`` guarantees gcode_state/mc_percent/…).
    """
    metadata: dict[str, Any] = {}
    if required:
        metadata["contract_required"] = True
    if min_length is not None:
        metadata["min_length"] = min_length
    if minimum is not None:
        metadata["minimum"] = minimum
    if description:
        metadata["description"] = description
    if requires_keys:
        metadata["requires_keys"] = tuple(requires_keys)

    kwargs: dict[str, Any] = {"metadata": metadata}
    if default_factory is not _UNSET:
        kwargs["default_factory"] = default_factory
    elif default is not _UNSET:
        kwargs["default"] = default
    return dataclasses.field(**kwargs)


@dataclasses.dataclass(frozen=True)
class Contract:
    """Base for a command's ``--json`` output.

    Subclasses declare their fields and set the class vars below. Optional
    fields default to ``None`` and are dropped from the payload unless listed in
    ``keep_none`` — matching what each command emits today, where an
    inapplicable key is usually absent rather than null.
    """

    #: Basename (without .json) under docs/schemas/.
    schema_name: ClassVar[str] = ""
    #: Human title written into the generated schema.
    schema_title: ClassVar[str] = ""
    #: Optional prose written into the generated schema's top-level description.
    schema_description: ClassVar[str] = ""
    #: Whether unknown keys are allowed. Every published schema but `version`
    #: says yes: they describe the *guaranteed* keys, and commands are free to
    #: add detail. Tightening this would silently break existing consumers.
    additional_properties: ClassVar[bool] = True
    #: Fields emitted as ``null`` rather than omitted when unset.
    keep_none: ClassVar[frozenset[str]] = frozenset()

    def to_payload(self, **extra: Any) -> dict[str, Any]:
        """Render to the dict ``emit_json`` takes.

        Nested dataclasses (and nested ``Contract`` instances) become plain
        dicts so ``json.dumps`` never sees a model object. ``extra`` carries
        command-specific keys that are not part of the guaranteed contract —
        legal because the schemas allow additional properties. Redaction still
        happens downstream in ``emit_json``.
        """
        payload: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if value is None and field.name not in self.keep_none:
                continue
            payload[field.name] = _to_jsonable(value)
        for key, value in extra.items():
            if value is not None or key in self.keep_none:
                payload[key] = _to_jsonable(value)
        return payload


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Contract):
        return value.to_payload()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for key, item in dataclasses.asdict(value).items():
            if item is None:
                continue
            out[key] = _to_jsonable(item)
        return out
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def all_contracts() -> list[type[Contract]]:
    """Every concrete contract, discovered from the registry module.

    Derived rather than hand-listed, for the same reason the package inventory
    and CLI help coverage are derived (see AGENTS.md): a parallel list drifts.
    """
    from bambu_cli.contracts import models

    found: list[type[Contract]] = []
    for name in dir(models):
        obj = getattr(models, name)
        # `schema_name` is empty on the base and on any abstract helper, so it
        # doubles as the "is this actually published?" test.
        if isinstance(obj, type) and issubclass(obj, Contract) and obj is not Contract and obj.schema_name:
            found.append(obj)
    return sorted(found, key=lambda c: c.schema_name)
