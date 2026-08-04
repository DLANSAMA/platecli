"""Tests for bambu_cli.contracts — the typed source of every --json payload.

The claim this file has to defend is "zero drift": the published schemas in
``docs/schemas`` cannot disagree with the code that produces the payloads.
Three things enforce it, and each is tested here:

1. Every contract generates its schema, and the committed file matches
   (``scripts/gen_schemas.py --check``, also a blocking CI step).
2. Every contract's own ``to_payload()`` validates against the schema it
   generated — so the model, the schema, and the emitted dict agree.
3. Every published schema has a contract behind it, and vice versa.

Point 2 is the one that catches a bad model: a schema derived from a model is
trivially consistent with itself, but it is *not* trivially consistent with
what ``to_payload`` actually emits (omitted-vs-null, key order, defaults).

Runtime import must work on the 3.9 floor; only schema *generation* needs 3.10+
(the contracts annotate optionals as ``X | None``, which 3.9 cannot evaluate).
That split is asserted below.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import contracts  # noqa: E402
from bambu_cli.contracts import Contract, all_contracts  # noqa: E402

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "schemas"

_needs_generator = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="schema generation needs 3.10+ to evaluate `X | None`; runtime does not",
)


# --- the registry ------------------------------------------------------------


def test_contracts_are_discovered():
    found = all_contracts()
    assert found, "no contracts discovered — all_contracts() derivation is broken"
    assert len({c.schema_name for c in found}) == len(found), "two contracts claim the same schema_name"


def test_every_schema_file_has_a_contract_and_vice_versa():
    """Drift in both directions is a failure.

    A schema with no contract keeps being published while nothing generates or
    checks it; a contract with no schema means the generator was never run.
    """
    on_disk = {p.stem for p in SCHEMA_DIR.glob("*.json")}
    modelled = {c.schema_name for c in all_contracts()}
    assert on_disk == modelled, (
        f"schema-only={sorted(on_disk - modelled)}, contract-only={sorted(modelled - on_disk)}"
    )


def test_every_contract_declares_a_title():
    for contract in all_contracts():
        assert contract.schema_title, f"{contract.__name__} has no schema_title"


# --- to_payload semantics ----------------------------------------------------


def test_unset_optionals_are_omitted_not_nulled():
    payload = contracts.Pause(status="paused", command="pause", paused=True).to_payload()
    assert payload == {"status": "paused", "command": "pause", "paused": True}
    assert "next_command" not in payload


def test_keep_none_fields_are_emitted_as_null():
    # setup reports model/nozzle as null rather than dropping them: a consumer
    # distinguishes "not configured" from "key absent because of an old version".
    payload = contracts.Setup(
        status="configured",
        command="setup",
        config_path="/tmp/config.json",
        printer_ip_configured=True,
        serial_configured=True,
        access_code_storage="file",
    ).to_payload()
    assert payload["model"] is None
    assert payload["nozzle"] is None


def test_key_order_follows_field_order():
    # Agents pattern-match on the leading status/command pair.
    payload = contracts.Light(status="light_changed", command="light", action="on", changed=True).to_payload()
    assert list(payload)[:2] == ["status", "command"]


def test_extra_keys_pass_through():
    # The schemas allow additional properties; commands add detail beyond the
    # guaranteed shape and must not have it silently dropped.
    payload = contracts.Light(
        status="light_changed", command="light", action="on", changed=True
    ).to_payload(sequence_id="42")
    assert payload["sequence_id"] == "42"


def test_extra_none_is_dropped_like_a_declared_optional():
    payload = contracts.Light(
        status="light_changed", command="light", action="on", changed=True
    ).to_payload(irrelevant=None)
    assert "irrelevant" not in payload


def test_contracts_are_frozen():
    light = contracts.Light(status="light_changed", command="light", action="on", changed=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        light.changed = False  # type: ignore[misc]


# --- runtime does not need the generator's Python -----------------------------


def test_contracts_import_without_evaluating_annotations():
    """The package must not call get_type_hints() on these models.

    ``X | None`` annotations only evaluate on 3.10+. If any runtime path
    resolved them, importing bambu_cli would break on the supported 3.9 floor —
    a failure CI would only catch on one leg.
    """
    for contract in all_contracts():
        fields = dataclasses.fields(contract)
        assert fields, f"{contract.__name__} declares no fields"
        # dataclasses keeps annotations as strings; resolving is the generator's job.
        assert all(isinstance(f.type, str) for f in fields), (
            f"{contract.__name__} has resolved annotations — something called get_type_hints()"
        )


def test_pydantic_is_not_a_runtime_dependency():
    """Importing the package must never pull pydantic in.

    It is declared in the `test` extra for scripts/gen_schemas.py only. If this
    fails, a compiled dependency has leaked into every user's install.
    """
    import subprocess  # noqa: S404 -- fixed argv, no shell

    code = "import bambu_cli.contracts, bambu_cli.utils, sys; print('pydantic' in sys.modules)"
    out = subprocess.run(  # noqa: S603 -- fixed argv
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", "importing bambu_cli pulled in pydantic"


# --- generated schemas agree with the models AND with to_payload --------------


@_needs_generator
def test_committed_schemas_match_the_contracts():
    """The anti-drift gate, run as a test as well as a CI step."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import gen_schemas

    assert gen_schemas.main(["--check"]) == 0, "docs/schemas is stale — run python scripts/gen_schemas.py"


# One representative instance per contract. Kept explicit rather than
# auto-constructed: the point is to check a *realistic* payload shape.
SAMPLES = [
    contracts.OkEnvelope(status="ok", command="status"),
    contracts.ErrorEnvelope(status="error", command="print", exit_code=2, error="boom", failed_step="mqtt"),
    contracts.Status(
        status="ok",
        command="status",
        # All four of these are contractually guaranteed inside `printer`; a
        # partial delta is a failure, not a payload (see the schema description).
        printer=contracts.PrinterState(
            gcode_state="IDLE", mc_percent=0, bed_temper=25.0, nozzle_temper=30.0
        ),
    ),
    contracts.StatusEvent(event="update", command="status", gcode_state="RUNNING", mc_percent=42),
    contracts.Light(status="light_changed", command="light", action="on", changed=True),
    contracts.Pause(status="paused", command="pause", paused=True),
    contracts.Resume(status="resumed", command="resume", resumed=True),
    contracts.Stop(status="stopped", command="stop", stopped=True),
    contracts.Gcode(status="ok", command="gcode", gcode="G28", sent=True),
    contracts.Files(status="ok", command="files", count=1, files=[contracts.RemoteFile(name="a.3mf", path="/a.3mf")]),
    contracts.Delete(status="ok", command="delete", file="a.3mf", deleted=True),
    contracts.Upload(status="uploaded", command="upload", file="a.3mf", remote_name="a.3mf", bytes=10, uploaded=True),
    contracts.Print(status="ok", command="print", file="a.3mf", printed=True),
    contracts.Snapshot(
        status="saved",
        command="snapshot",
        output="/tmp/a.jpg",
        size_bytes=100,
        captured_at="2026-07-24T19:15:30Z",
        sha256="ab" * 32,
    ),
    contracts.Download(
        status="downloaded",
        command="download",
        source="https://example.com/a.stl",
        download_url="https://example.com/a.stl",
        path="/tmp/a.stl",
        filename="a.stl",
        bytes=7,
    ),
    contracts.Slice(
        status="sliced", command="slice", file="a.stl", path="/tmp/a.3mf", filename="a.3mf", bytes=9, step_converted=False
    ),
    contracts.SliceListSettings(
        status="ok",
        command="slice",
        action="list_settings",
        process=contracts.ProcessSettings(count=1, settings={"layer_height": "0.2"}),
        filament=contracts.FilamentSettings(count=1, settings={"filament_flow_ratio": "0.98"}),
    ),
    contracts.Version(status="ok", command="version", version="0.5.0"),
    contracts.Setup(
        status="configured",
        command="setup",
        config_path="/tmp/config.json",
        printer_ip_configured=True,
        serial_configured=True,
        access_code_storage="file",
    ),
    contracts.ConfigCmd(status="ok", command="config", action="show"),
    contracts.Preflight(
        status="ok",
        command="preflight",
        checks=[contracts.PreflightCheck(status="ok", name="orca", message="found")],
    ),
    contracts.Doctor(status="ok", command="doctor"),
    contracts.JobOk(status="uploaded", command="job"),
    contracts.JobError(status="error", command="job", exit_code=2, error="boom", failed_step="upload"),
    contracts.Go(status="error", command="go", exit_code=5, error="interactive only", failed_step="parse"),
]


def test_samples_cover_every_contract():
    assert {type(s).schema_name for s in SAMPLES} == {c.schema_name for c in all_contracts()}


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: type(s).schema_name)
def test_payload_validates_against_its_generated_schema(sample):
    """A model is only useful if what it *emits* matches what it *publishes*."""
    from tests.contracts.test_schema_validation import _validate

    schema = json.loads((SCHEMA_DIR / f"{type(sample).schema_name}.json").read_text(encoding="utf-8"))
    payload = json.loads(json.dumps(sample.to_payload(), default=_as_plain))
    _validate(payload, schema)


def _as_plain(obj):
    """Nested contracts/dataclasses render as plain dicts, same as emit_json sees."""
    if isinstance(obj, Contract):
        return obj.to_payload()
    if dataclasses.is_dataclass(obj):
        return {k: v for k, v in dataclasses.asdict(obj).items() if v is not None}
    raise TypeError(type(obj))
