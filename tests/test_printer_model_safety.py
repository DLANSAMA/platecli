"""The configured printer model decides which machine profile G-code is sliced for.

Slicing for the wrong printer is a physical hazard: a P1P profile has a 256 mm
bed, an A1 mini has 180 mm. platecli used to fall back to the P1P profile for any
model name it did not recognise (including the A1 mini's own product name, "A1
mini") and for any missing machine profile, without a warning. These tests pin
the fail-closed behaviour: an unknown model or a missing machine profile stops
the slice with a config error instead of producing G-code for another printer.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from bambu_cli.config import MODEL_MAPPING, resolve_printer_model
from bambu_cli.constants import EXIT_CONFIG_ERROR
from bambu_cli.context import Settings
from bambu_cli.errors import BambuError
from bambu_cli.slicer import cmd_slice
from bambu_cli.slicer.profiles import _discover_process_profile
from tests.bambu_test_base import settings_ctx
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl


def _slice_args(model_path, outdir):
    return argparse.Namespace(
        file=model_path,
        output=outdir,
        quality="standard",
        copies=1,
        infill=15,
        pattern="3dhoneycomb",
        supports=False,
        nozzle_temp=None,
        bed_temp=None,
        filament="PLA Basic",
        json=False,
        threads=None,
        list_settings=False,
    )


@pytest.fixture
def stub_slice(tmp_path, monkeypatch):
    """Run cmd_slice against the fake OrcaSlicer with a P1P-only profiles dir."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ORCA_STUB_SCENARIO", "success")
    launcher = make_orca_launcher(str(tmp_path))
    profiles = build_profiles_dir(str(tmp_path))
    model = write_stl(str(tmp_path / "model.stl"))
    outdir = tmp_path / "out"
    outdir.mkdir()

    def run(printer_model, nozzle_size="0.4"):
        with settings_ctx(
            orca_slicer=launcher, profiles_dir=profiles, printer_model=printer_model, nozzle_size=nozzle_size
        ):
            return cmd_slice(_slice_args(model, str(outdir)))

    run.outdir = str(outdir)
    run.profiles = profiles
    return run


# --- model name resolution ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("P1P", "P1P"),
        ("p1s", "P1S"),
        ("A1 mini", "A1M"),
        ("A1MINI", "A1M"),
        ("a1-mini", "A1M"),
        ("Bambu Lab A1 mini", "A1M"),
        ("X1 Carbon", "X1C"),
        ("Bambu Lab X1 Carbon", "X1C"),
        (" x1e ", "X1E"),
    ],
)
def test_resolve_printer_model_accepts_product_names(raw, expected):
    assert resolve_printer_model(raw) == expected


@pytest.mark.parametrize("raw", ["H2D", "P2S", "A2", "", None, 123, "P1P 0.4 nozzle"])
def test_resolve_printer_model_rejects_unknown(raw):
    assert resolve_printer_model(raw) is None


def test_settings_resolve_product_name_to_model_key():
    assert Settings.from_config({"model": "A1 mini"}).printer_model == "A1M"


def test_settings_keep_unknown_model_visible_instead_of_defaulting():
    # The raw name must survive so the slicer can refuse it by name; turning it
    # into "P1P" here is exactly the silent fallback being removed.
    assert Settings.from_config({"model": "H2D"}).printer_model == "H2D"


# --- slicing -----------------------------------------------------------------


def test_unknown_model_refuses_to_slice(stub_slice):
    with pytest.raises(BambuError) as caught:
        stub_slice("H2D")
    assert caught.value.exit_code == EXIT_CONFIG_ERROR
    assert "H2D" in str(caught.value)
    assert os.listdir(stub_slice.outdir) == []


def test_missing_machine_profile_does_not_fall_back_to_p1p(stub_slice):
    # A1M is a known model, but this profiles dir only holds the P1P machine
    # profile. The old code sliced with the P1P profile instead.
    with pytest.raises(BambuError) as caught:
        stub_slice("A1M")
    assert caught.value.exit_code == EXIT_CONFIG_ERROR
    assert "Bambu Lab A1 mini 0.4 nozzle" in str(caught.value)
    assert os.listdir(stub_slice.outdir) == []


def test_missing_nozzle_profile_does_not_fall_back_to_another_nozzle(stub_slice):
    with pytest.raises(BambuError) as caught:
        stub_slice("P1P", nozzle_size="0.6")
    assert caught.value.exit_code == EXIT_CONFIG_ERROR
    assert os.listdir(stub_slice.outdir) == []


def test_known_model_with_its_profile_still_slices(stub_slice):
    assert stub_slice("P1P").endswith("model_sliced.3mf")


def test_process_discovery_never_borrows_another_models_profile(tmp_path):
    proc = tmp_path / "process"
    proc.mkdir()
    (proc / "0.20mm Standard @BBL P1P.json").write_text(
        json.dumps({"compatible_printers": ["Bambu Lab P1P 0.4 nozzle"]}), encoding="utf-8"
    )
    quality_map = {"standard": "0.20mm Standard @BBL A1M"}
    found = _discover_process_profile(
        "standard",
        quality_map,
        model_code="A1M",
        compatible_printer="Bambu Lab A1 mini 0.4 nozzle",
        profiles_dir=str(tmp_path),
    )
    assert found is None


# --- setup / validation ------------------------------------------------------


def test_setup_normalize_model_rejects_unknown_instead_of_defaulting():
    from bambu_cli.setup_cmd.common import _normalize_model

    assert _normalize_model("A1 mini") == "A1M"
    with pytest.raises(ValueError, match="H2D"):
        _normalize_model("H2D")


def test_noninteractive_setup_requires_a_model(tmp_path, monkeypatch, capsys):
    from bambu_cli.setup_cmd import common as common_mod
    from bambu_cli.setup_cmd.wizard import _cmd_setup_noninteractive

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    monkeypatch.setenv("PLATE_TEST_CODE", "12345678")
    args = argparse.Namespace(
        printer_ip="192.0.2.10",
        serial="01S00A000000000",
        access_code=None,
        access_code_env="PLATE_TEST_CODE",
        access_code_file=str(tmp_path / "access_code"),
        model=None,
        nozzle=None,
        orca_slicer=None,
        profiles_dir=None,
        cert_fingerprint=None,
        insecure_tls=False,
        force=False,
        json=True,
    )
    with pytest.raises(BambuError) as caught:
        _cmd_setup_noninteractive(args)
    assert caught.value.exit_code == EXIT_CONFIG_ERROR
    assert "--model" in str(caught.value.extra) or "--model" in str(caught.value)
    assert not cfg_path.exists()


def test_config_validate_flags_unknown_model(tmp_path, monkeypatch):
    from bambu_cli import config as config_mod
    from bambu_cli.setup_cmd import common as common_mod
    from bambu_cli.setup_cmd.preflight import collect_preflight_checks

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {"printer_ip": "192.0.2.10", "serial": "01S00A000000000", "model": "H2D", "access_code": "12345678"}
        ),
        encoding="utf-8",
    )
    os.chmod(cfg_path, 0o600)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(common_mod, "_config_path", lambda: str(cfg_path))
    checks = {check["name"]: check for check in collect_preflight_checks()}
    assert checks["printer-model"]["status"] == "error"
    assert "H2D" in checks["printer-model"]["message"]


def test_every_supported_model_resolves_to_itself():
    for key in MODEL_MAPPING:
        assert resolve_printer_model(key) == key


def test_interactive_model_prompt_reasks_until_supported(monkeypatch):
    from bambu_cli.setup_cmd import wizard as wizard_mod

    answers = iter(["H2D", "", "a1 mini"])
    monkeypatch.setattr(wizard_mod, "_prompt_text", lambda msg, args=None: next(answers))
    # Nothing detected: a blank answer is not accepted as "P1P".
    assert wizard_mod._prompt_printer_model(argparse.Namespace(json=False), None) == "A1M"


def test_interactive_model_prompt_gives_up_without_saving(monkeypatch):
    from bambu_cli.setup_cmd import wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_prompt_text", lambda msg, args=None: "Ender 3")
    with pytest.raises(BambuError) as caught:
        wizard_mod._prompt_printer_model(argparse.Namespace(json=False), None)
    assert caught.value.exit_code == EXIT_CONFIG_ERROR


def test_interactive_model_prompt_blank_takes_detected_model(monkeypatch):
    from bambu_cli.setup_cmd import wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_prompt_text", lambda msg, args=None: "")
    assert wizard_mod._prompt_printer_model(argparse.Namespace(json=False), "X1C") == "X1C"
