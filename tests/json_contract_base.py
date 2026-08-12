"""Shared harness for the `--json` contract regression tests.

Extracted from the former 1031-line test_json_contracts.py so the per-command
contract modules share one copy of the shape checker and the main() driver
rather than duplicating them.

These are SHAPE-locking regression tests, not spec tests: where docs/api.md
disagrees with actual CLI output we assert the actual output and flag the
discrepancy, so an accidental shape change is caught here.

Ground rules (docs/test-backlog.md): never touch a real printer/network -- use
`--sim` and a scratch config path; drive the real argv/parser path through
`bambu_cli.cli.main()`.
"""

import argparse
import json
import sys
import zipfile
from unittest.mock import MagicMock

import pytest

# paho-mqtt is an optional/heavy dep; stub it the same way other tests do so
# importing the package never fails on environments without it installed.
_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)
sys.modules.setdefault("paho.mqtt.client", _mock_mqtt)

from bambu_cli import bambu  # noqa: E402
from bambu_cli import utils  # noqa: E402
from bambu_cli.cli import build_parser, main  # noqa: E402


# ---------------------------------------------------------------------------
# assert_shape: a small, self-contained schema-shape checker (no jsonschema
# dependency available/allowed).
# ---------------------------------------------------------------------------


def assert_shape(payload, spec, path="$"):
    """Validate `payload` against a small hand-rolled spec.

    spec keys:
      - "type": a type or tuple of types the value must be an instance of.
      - "required": {key: subspec, ...} keys that MUST be present.
      - "optional": {key: subspec, ...} keys that MAY be present; validated
        only if present.
      - "enum": iterable of allowed values for this exact node.
      - "items": subspec applied to every element when type is list.
    """
    if "type" in spec:
        expected_type = spec["type"]
        assert isinstance(payload, expected_type), (
            f"{path}: expected type {expected_type}, got {type(payload).__name__} ({payload!r})"
        )

    if "enum" in spec:
        assert payload in spec["enum"], f"{path}: {payload!r} not in allowed enum {spec['enum']!r}"

    if isinstance(payload, dict):
        required = spec.get("required", {})
        for key, subspec in required.items():
            assert key in payload, f"{path}: missing required key {key!r} in {sorted(payload.keys())}"
            assert_shape(payload[key], subspec, path=f"{path}.{key}")
        optional = spec.get("optional", {})
        for key, subspec in optional.items():
            if key in payload:
                assert_shape(payload[key], subspec, path=f"{path}.{key}")

    if isinstance(payload, list) and "items" in spec:
        for idx, item in enumerate(payload):
            assert_shape(item, spec["items"], path=f"{path}[{idx}]")


ANY = {}
STR = {"type": str}
BOOL = {"type": bool}
INT = {"type": int}
NUM = {"type": (int, float)}
DICT = {"type": dict}
LIST = {"type": list}

BASE_OK = {"type": dict, "required": {"status": {"enum": ["ok"]}, "command": STR}}


def base_error_spec(command=None, require_failed_step=True):
    required = {
        "status": {"enum": ["error"]},
        "command": {"enum": [command]} if command else STR,
        "exit_code": INT,
        "error": STR,
    }
    if require_failed_step:
        required["failed_step"] = STR
    return {"type": dict, "required": required}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_json_state():
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None
    yield
    utils._JSON_EMITTED = False
    utils._LAST_ERROR_PAYLOAD = None
    utils._LAST_DOWNLOAD_PAYLOAD = None


def run_main(monkeypatch, tmp_path, argv, config_path=None):
    """Drive bambu_cli.cli.main() with a scratch config path so no real
    on-disk config is ever touched, and return the SystemExit (or None)."""
    import bambu_cli.cli as cli_mod
    import bambu_cli.config as config_mod

    monkeypatch.setattr(sys, "argv", ["plate"] + list(argv))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path or str(tmp_path / "no-such-config" / "config.json"))
    monkeypatch.setattr(cli_mod, "setup_logging", lambda *a, **k: None)
    exc = None
    try:
        main()
    except SystemExit as e:
        exc = e
    return exc


def read_json(capsys):
    out = capsys.readouterr().out
    return json.loads(out)


def make_ready_file(tmp_path, name="ready.3mf", content="simulated 3mf content"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path
