"""OrcaSlicer 2.5.x packed profiles (``profiles/BBL.opc``): reader, unpack, and callers.

Every cache here is written by ``tests/fakes/opc_builder.py`` under ``tmp_path``
and unpacked into a ``tmp_path`` cache directory, so nothing depends on an
OrcaSlicer install or on the user's cache folder.
"""

from __future__ import annotations

import argparse
import json
import os
from unittest.mock import patch

import pytest

from bambu_cli import setup_cmd
from bambu_cli.errors import BambuError
from bambu_cli.interactive.core import slicer_preflight_problem
from bambu_cli.paths import json_path
from bambu_cli.setup_cmd.preflight import _profiles_dir_check
from bambu_cli.slicer import cmd_slice
from bambu_cli.slicer.options import _override_safety_problem, setting_catalog
from bambu_cli.slicer.packed_profiles import (
    NEWEST_SUPPORTED_ORCA,
    PackedProfilesError,
    default_cache_root,
    packed_cache_path,
    read_packed_profiles,
    resolve_profiles_dir,
)
from bambu_cli.slicer.profiles import _create_temp_machine, _load_flattened_profile, machine_profile_path
from bambu_cli.utils import _display_path
from tests.bambu_test_base import settings_ctx
from tests.fakes import opc_builder as opc
from tests.fakes.orca_stub import build_profiles_dir, make_orca_launcher, write_stl

V = opc.VECTOR


def _write(path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _preset(packed, kind: str, name: str):
    return next(p for p in packed.presets[kind] if p.name == name)


# --- reader -------------------------------------------------------------------


def test_reads_presets_with_their_references(tmp_path):
    packed = read_packed_profiles(_write(tmp_path / "BBL.opc", opc.build_opc(opc.P1P_PRESETS)))

    assert packed.vendor == "BBL"
    assert {kind: len(items) for kind, items in packed.presets.items()} == {"process": 2, "filament": 2, "machine": 3}
    machine = _preset(packed, "machine", "Bambu Lab P1P 0.4 nozzle")
    assert machine.inherits == "fdm_machine_common"
    assert machine.includes == (opc.START_GCODE_TEMPLATE,)
    assert machine.setting_id == "GM014"
    assert machine.config["nozzle_diameter"] == ["0.4"]
    assert _preset(packed, "filament", "Bambu PLA Basic @base").filament_id == "GFA00"


def test_every_value_type_becomes_profile_json_text(tmp_path):
    nan = float("nan")
    key_types = {
        "f": opc.FLOAT,
        "fs": opc.FLOAT + V,
        "i": opc.INT,
        "is": opc.INT + V,
        "pct": opc.PERCENT,
        "pcts": opc.PERCENT + V,
        "fop": opc.FLOAT_OR_PERCENT,
        "fops": opc.FLOAT_OR_PERCENT + V,
        "pt": opc.POINT,
        "pts": opc.POINT + V,
        "pt3": opc.POINT3,
        "b": opc.BOOL,
        "bs": opc.BOOL + V,
        "e": opc.ENUM,
        "es": opc.ENUM + V,
        "groups": opc.POINTS_GROUPS,
        "int_groups": opc.INTS_GROUPS,
    }
    config = {
        "f": 0.2,
        "fs": [220.0, 0.42, nan],
        "i": -3,
        "is": [1, opc.INT_NIL],
        "pct": 15.0,
        "pcts": [50.0, nan],
        "fop": (50.0, True),
        "fops": [(0.4, False), (80.0, True), (nan, False)],
        "pt": (0.5, 0.5),
        "pts": [(0.0, 0.0), (256.0, 0.0)],
        "pt3": (1.0, 2.0, 3.5),
        "b": True,
        "bs": [1, 0, opc.BOOL_NIL],
        "e": "arachne",
        "es": ["classic", 7, opc.INT_NIL],
        "groups": [[(0.0, 0.0), (325.0, 0.0)], [(25.0, 0.0)]],
        "int_groups": [[1, 2], [3]],
        "s": "plain",
        "ss": ["a", "b"],
    }
    data = opc.build_opc({"machine": [{"name": "m", "config": config}]}, key_types=key_types)

    read = _preset(read_packed_profiles(_write(tmp_path / "BBL.opc", data)), "machine", "m").config

    assert read == {
        "f": "0.2",
        "fs": ["220", "0.42", "nil"],
        "i": "-3",
        "is": ["1", "nil"],
        "pct": "15%",
        # A nil percent is "nil": OrcaSlicer reads "nil%" as 0%.
        "pcts": ["50%", "nil"],
        "fop": "50%",
        "fops": ["0.4", "80%", "nil"],
        "pt": "0.5x0.5",
        "pts": ["0x0", "256x0"],
        "pt3": "1,2,3.5",
        "b": "1",
        "bs": ["1", "0", "nil"],
        "e": "arachne",
        "es": ["classic", "7", "nil"],
        "groups": ["0x0,325x0", "25x0"],
        "int_groups": ["1,2", "3"],
        "s": "plain",
        "ss": ["a", "b"],
    }


_BODY = opc.build_body(opc.P1P_PRESETS)

_BAD_CACHES = {
    "not a cache": opc.wrap(_BODY, magic=0x12345678),
    "newer format": opc.wrap(_BODY, format_version=3),
    "newer preset cache version": opc.wrap(opc.build_body(opc.P1P_PRESETS, cache_version=3)),
    "checksum": opc.wrap(_BODY, crc=1),
    "size field": opc.wrap(_BODY, size=len(_BODY) + 1),
    "cut short": opc.wrap(_BODY[:-40]),
    "extra bytes": opc.wrap(_BODY + b"\x00"),
    "header only half there": opc.wrap(_BODY)[:10],
    "empty file": b"",
    "unknown value type": opc.wrap(
        opc.build_body({"machine": [{"name": "m", "config": {"odd": "x"}}]}, key_types={"odd": 99})
    ),
    "setting outside the dictionary": opc.wrap(opc.build_body(opc.P1P_PRESETS, bad_key_index=True)),
    "preset errors recorded by OrcaSlicer": opc.wrap(opc.build_body(opc.P1P_PRESETS, parse_errors=2)),
    "list longer than the data": opc.wrap(_BODY[:4] + b"\xff" * 8 + _BODY[12:]),
}


@pytest.mark.parametrize("data", _BAD_CACHES.values(), ids=_BAD_CACHES.keys())
def test_anything_unrecognised_is_refused_by_name_of_newest_supported_version(tmp_path, data):
    with pytest.raises(PackedProfilesError) as caught:
        read_packed_profiles(_write(tmp_path / "BBL.opc", data))

    message = str(caught.value)
    assert "not supported yet" in message
    assert NEWEST_SUPPORTED_ORCA in message
    assert caught.value.failed_step == "profiles"
    assert caught.value.extra["newest_supported_orca"] == NEWEST_SUPPORTED_ORCA


def test_an_infinite_setting_is_refused(tmp_path):
    data = opc.build_opc({"machine": [{"name": "m", "config": {"f": float("inf")}}]}, key_types={"f": opc.FLOAT})

    with pytest.raises(PackedProfilesError, match="infinite"):
        read_packed_profiles(_write(tmp_path / "BBL.opc", data))


def test_a_missing_cache_file_is_refused_not_raised_as_oserror(tmp_path):
    with pytest.raises(PackedProfilesError):
        read_packed_profiles(str(tmp_path / "gone.opc"))


# --- detection ----------------------------------------------------------------


def test_packed_install_is_a_profiles_folder_without_machine_beside_an_opc(tmp_path):
    packed = opc.build_packed_install(str(tmp_path))

    assert packed_cache_path(packed) == f"{packed}.opc"
    assert packed_cache_path(packed + os.sep) == f"{packed}.opc"


def test_loose_tree_is_never_treated_as_packed(tmp_path):
    loose = build_profiles_dir(str(tmp_path))
    (tmp_path / "profiles.opc").write_bytes(opc.build_opc(opc.P1P_PRESETS))

    assert packed_cache_path(loose) is None
    assert resolve_profiles_dir(loose, cache_root=str(tmp_path / "cache")) == loose
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("value", [None, ""])
def test_unset_profiles_dir_is_not_packed(value):
    assert packed_cache_path(value) is None


def test_missing_or_plain_folders_come_back_unchanged(tmp_path):
    missing = str(tmp_path / "nowhere")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert resolve_profiles_dir(missing, cache_root=str(tmp_path / "cache")) == missing
    assert resolve_profiles_dir(str(plain), cache_root=str(tmp_path / "cache")) == str(plain)


def test_cache_root_follows_plate_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "c"))

    assert default_cache_root() == str(tmp_path / "c" / "orca-profiles")


# --- unpack -------------------------------------------------------------------


def test_unpack_writes_a_loose_tree_once(tmp_path):
    packed = opc.build_packed_install(str(tmp_path))
    cache_root = str(tmp_path / "cache")

    effective = resolve_profiles_dir(packed, cache_root=cache_root)

    assert os.path.dirname(effective) == cache_root
    assert sorted(os.listdir(os.path.join(effective, "machine"))) == [
        "Bambu Lab P1P 0.4 nozzle template machine_start_gcode.json",
        "Bambu Lab P1P 0.4 nozzle.json",
        "fdm_machine_common.json",
    ]
    # The lookup tables a 2.5.x release leaves in profiles/BBL/filament are not presets.
    assert sorted(os.listdir(os.path.join(effective, "filament"))) == [
        "Bambu PLA Basic @base.json",
        "fdm_filament_pla.json",
    ]
    machine = _read_json(machine_profile_path(effective, "Bambu Lab P1P", "0.4"))
    assert machine["inherits"] == "fdm_machine_common"
    assert machine["include"] == [opc.START_GCODE_TEMPLATE]
    assert machine["name"] == "Bambu Lab P1P 0.4 nozzle"
    assert machine["type"] == "machine"
    assert "machine_start_gcode" not in machine  # only the template carries it

    marker = os.path.join(effective, "plate-unpacked.json")
    before = os.stat(marker).st_mtime_ns
    assert resolve_profiles_dir(packed, cache_root=cache_root) == effective
    assert os.stat(marker).st_mtime_ns == before
    assert [name for name in os.listdir(cache_root) if name.startswith(".unpack-")] == []


def test_a_changed_cache_gets_its_own_unpack(tmp_path):
    packed = opc.build_packed_install(str(tmp_path))
    cache_root = str(tmp_path / "cache")
    first = resolve_profiles_dir(packed, cache_root=cache_root)

    changed = {**opc.P1P_PRESETS, "process": [{"name": "0.20mm Standard @BBL P1P", "config": {"layer_height": "0.2"}}]}
    with open(f"{packed}.opc", "wb") as handle:
        handle.write(opc.build_opc(changed))
    second = resolve_profiles_dir(packed, cache_root=cache_root)

    assert second != first
    assert os.listdir(os.path.join(second, "process")) == ["0.20mm Standard @BBL P1P.json"]


def test_files_are_named_after_the_preset_never_after_sub_path(tmp_path):
    presets = {
        "filament": [
            {"name": "Bambu Support For PA/PET @base", "sub_path": "../../../escaped.json", "config": {}},
            {"name": "plain", "sub_path": "/abs/elsewhere.json", "config": {}},
        ]
    }
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.build_opc(presets))

    effective = resolve_profiles_dir(packed, cache_root=str(tmp_path / "cache"))

    assert sorted(os.listdir(os.path.join(effective, "filament"))) == [
        "Bambu Support For PA PET @base.json",
        "plain.json",
    ]
    stored = _read_json(os.path.join(effective, "filament", "Bambu Support For PA PET @base.json"))
    assert stored["name"] == "Bambu Support For PA/PET @base"
    everything = [name for _root, _dirs, files in os.walk(tmp_path) for name in files]
    assert "escaped.json" not in everything and "elsewhere.json" not in everything


@pytest.mark.parametrize(
    "names",
    [["..", "ok"], ["/", "ok"], ["bad\nname"], ["Same Name", "same name"]],
    ids=["dot-dot", "only a separator", "control character", "names differing only in case"],
)
def test_unusable_preset_names_refuse_the_whole_cache_and_leave_nothing(tmp_path, names):
    presets = {"machine": [{"name": name, "config": {}} for name in names]}
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.build_opc(presets))
    cache_root = tmp_path / "cache"

    with pytest.raises(PackedProfilesError, match="not supported yet"):
        resolve_profiles_dir(packed, cache_root=str(cache_root))

    assert os.listdir(cache_root) == []


def test_unreadable_cache_never_falls_back_to_the_folder_beside_it(tmp_path):
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.wrap(_BODY, format_version=3))
    cache_root = tmp_path / "cache"

    with pytest.raises(PackedProfilesError, match=NEWEST_SUPPORTED_ORCA):
        resolve_profiles_dir(packed, cache_root=str(cache_root))

    assert not cache_root.exists()


def test_a_cache_folder_that_cannot_be_written_says_how_to_move_it(tmp_path):
    packed = opc.build_packed_install(str(tmp_path / "install"))
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("in the way", encoding="utf-8")

    with pytest.raises(PackedProfilesError, match="PLATE_CACHE_DIR") as caught:
        resolve_profiles_dir(packed, cache_root=str(blocker / "orca-profiles"))

    assert "not supported yet" not in str(caught.value)
    assert caught.value.failed_step == "profiles"


# --- include layering in the flattener ----------------------------------------


def _loose(tmp_path, **files) -> str:
    folder = tmp_path / "machine"
    folder.mkdir()
    for stem, document in files.items():
        (folder / f"{stem}.json").write_text(json.dumps(document), encoding="utf-8")
    return str(folder)


def test_include_lays_over_the_parent_and_under_the_profile_itself(tmp_path):
    folder = _loose(
        tmp_path,
        parent={"name": "parent", "start": "generic", "a": "parent", "b": "parent", "c": "parent"},
        template={
            "name": "template",
            "type": "machine",
            "instantiation": "false",
            "start": "template",
            "b": "template",
        },
        child={"name": "child", "inherits": "parent", "include": ["template"], "c": "child"},
    )

    flat = _load_flattened_profile(os.path.join(folder, "child.json"))

    assert flat == {"name": "child", "start": "template", "a": "parent", "b": "template", "c": "child"}


def test_includes_apply_in_listed_order_and_a_shared_one_is_not_a_cycle(tmp_path):
    folder = _loose(
        tmp_path,
        first={"name": "first", "k": "first", "only_first": "1"},
        second={"name": "second", "k": "second"},
        parent={"name": "parent", "include": ["first"]},
        child={"name": "child", "inherits": "parent", "include": ["first", "second"]},
    )

    flat = _load_flattened_profile(os.path.join(folder, "child.json"))

    assert flat == {"name": "child", "k": "second", "only_first": "1"}


def test_an_included_preset_brings_its_own_parents(tmp_path):
    folder = _loose(
        tmp_path,
        base={"name": "base", "deep": "base"},
        template={"name": "template", "inherits": "base", "shallow": "template"},
        child={"name": "child", "include": ["template"]},
    )

    assert _load_flattened_profile(os.path.join(folder, "child.json")) == {
        "name": "child",
        "deep": "base",
        "shallow": "template",
    }


@pytest.mark.parametrize(
    ("include", "error"),
    [
        (["gone"], "not in the profiles directory"),
        (["../machine/child"], "invalid profile include reference"),
        ([""], "invalid profile include reference"),
        ([7], "invalid profile include reference"),
        ("template", "invalid profile include list"),
        (["child"], "cycle"),
    ],
    ids=["missing", "path", "empty", "not a string", "not a list", "includes itself"],
)
def test_an_include_that_cannot_be_resolved_fails_closed(tmp_path, include, error):
    folder = _loose(tmp_path, template={"name": "template"}, child={"name": "child", "include": include})

    with pytest.raises(ValueError, match=error):
        _load_flattened_profile(os.path.join(folder, "child.json"))


def test_machine_from_a_packed_install_gets_its_start_gcode_from_the_template(tmp_path):
    effective = resolve_profiles_dir(opc.build_packed_install(str(tmp_path)), cache_root=str(tmp_path / "cache"))

    tmp_machine = _create_temp_machine(machine_profile_path(effective, "Bambu Lab P1P", "0.4"), effective)
    try:
        machine = _read_json(tmp_machine.name)
    finally:
        os.unlink(tmp_machine.name)

    assert machine["machine_start_gcode"].startswith(";===== machine: P1P-0.4")
    assert machine["printable_height"] == "250"  # from the parent
    assert machine["name"] == "Bambu Lab P1P 0.4 nozzle"
    assert "include" not in machine and "inherits" not in machine


# --- plate slice ----------------------------------------------------------------


def _slice_args(model: str, outdir: str, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        file=model,
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
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


@pytest.fixture
def slice_env(tmp_path, monkeypatch):
    """A fake OrcaSlicer, a model, and a private unpack directory."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("ORCA_STUB_SCENARIO", "success")
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    dump = tmp_path / "profiles-dump.json"
    monkeypatch.setenv("ORCA_STUB_PROFILE_DUMP", str(dump))
    outdir = tmp_path / "out"
    outdir.mkdir()
    return argparse.Namespace(
        launcher=make_orca_launcher(str(tmp_path)),
        model=write_stl(str(tmp_path / "model.stl")),
        outdir=str(outdir),
        dump=dump,
        cache=tmp_path / "plate-cache",
    )


def test_slice_reads_machine_process_and_filament_from_a_packed_install(tmp_path, slice_env):
    packed = opc.build_packed_install(str(tmp_path / "install"))

    with settings_ctx(orca_slicer=slice_env.launcher, profiles_dir=packed):
        result = cmd_slice(_slice_args(slice_env.model, slice_env.outdir))

    assert os.path.isfile(result)
    used = json.loads(slice_env.dump.read_text(encoding="utf-8"))
    assert used["machine"]["machine_start_gcode"].startswith(";===== machine: P1P-0.4")
    assert used["machine"]["gcode_flavor"] == "marlin"
    assert used["process"]["layer_height"] == "0.2"
    assert used["process"]["wall_loops"] == "2"
    assert used["filament"]["filament_type"] == ["PLA"]
    assert used["filament"]["nozzle_temperature"] == ["220"]
    for kind in ("machine", "process", "filament"):
        assert "include" not in used[kind] and "inherits" not in used[kind]


def test_slice_refuses_an_unreadable_packed_install_before_running_the_slicer(tmp_path, slice_env, monkeypatch):
    marker = tmp_path / "slicer-ran"
    monkeypatch.setenv("ORCA_STUB_MARKER", str(marker))
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.wrap(_BODY, format_version=3))

    with settings_ctx(orca_slicer=slice_env.launcher, profiles_dir=packed), pytest.raises(BambuError) as caught:
        cmd_slice(_slice_args(slice_env.model, slice_env.outdir))

    assert "not supported yet" in str(caught.value)
    assert NEWEST_SUPPORTED_ORCA in str(caught.value)
    assert caught.value.failed_step == "profiles"
    assert caught.value.extra["profiles_dir"] == packed
    assert caught.value.extra["newest_supported_orca"] == NEWEST_SUPPORTED_ORCA
    assert not marker.exists()
    assert os.listdir(slice_env.outdir) == []


def test_slice_names_the_printer_when_a_packed_install_lacks_its_nozzle(tmp_path, slice_env):
    packed = opc.build_packed_install(str(tmp_path / "install"))

    with (
        settings_ctx(orca_slicer=slice_env.launcher, profiles_dir=packed, nozzle_size="0.8"),
        pytest.raises(BambuError) as caught,
    ):
        cmd_slice(_slice_args(slice_env.model, slice_env.outdir))

    assert "Missing machine profile for Bambu Lab P1P with a 0.8 mm nozzle" in str(caught.value)
    assert "unpacked from" in str(caught.value)
    assert caught.value.extra["profiles_dir"] == packed


def test_settings_catalog_and_override_checks_read_the_unpacked_profiles(tmp_path, slice_env):
    packed = opc.build_packed_install(str(tmp_path / "install"))

    catalog = setting_catalog(resolve_profiles_dir(packed))
    assert "layer_height" in catalog["process"]
    assert "filament_type" in catalog["filament"]
    assert "include" not in catalog["process"] and "include" not in catalog["filament"]

    # printer_model is a machine key only the unpacked machine profiles name.
    with settings_ctx(profiles_dir=packed):
        problem = _override_safety_problem(argparse.Namespace(set_process=["printer_model=X"], set_filament=None))
    assert problem is not None and "printer (machine) setting" in problem


def test_override_checks_keep_working_when_the_cache_is_unreadable(tmp_path, slice_env):
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.wrap(_BODY, format_version=3))

    with settings_ctx(profiles_dir=packed):
        refused = _override_safety_problem(argparse.Namespace(set_process=["printable_height=999"], set_filament=None))
        allowed = _override_safety_problem(argparse.Namespace(set_process=["wall_loops=3"], set_filament=None))

    assert refused is not None and "printer (machine) setting" in refused
    assert allowed is None


# --- plate preflight ----------------------------------------------------------


def test_preflight_fails_on_a_profiles_folder_with_no_printer_profiles(tmp_path):
    """The false pass: OrcaSlicer 2.5.x keeps profiles/BBL, with only filament/ in it."""
    hollow = tmp_path / "profiles" / "BBL"
    (hollow / "filament").mkdir(parents=True)

    check = _profiles_dir_check(str(hollow), "P1P", "0.4")

    assert check["status"] == "error"
    assert "no machine/ folder" in check["message"]


def test_preflight_passes_on_a_loose_tree_that_has_the_machine_profile(tmp_path):
    check = _profiles_dir_check(build_profiles_dir(str(tmp_path)), "P1P", "0.4")

    assert check["status"] == "ok"
    assert "Bambu Lab P1P" in check["message"] and "0.4 mm nozzle" in check["message"]


@pytest.mark.parametrize(("model", "nozzle", "named"), [("P1P", "0.6", "0.6 mm nozzle"), ("X1C", "0.4", "X1 Carbon")])
def test_preflight_fails_when_the_configured_printer_or_nozzle_has_no_profile(tmp_path, model, nozzle, named):
    check = _profiles_dir_check(build_profiles_dir(str(tmp_path)), model, nozzle)

    assert check["status"] == "error"
    assert "no machine profile" in check["message"] and named in check["message"]


def test_preflight_leaves_an_unknown_model_to_the_printer_model_check(tmp_path):
    assert _profiles_dir_check(build_profiles_dir(str(tmp_path)), "NOT-A-PRINTER", "0.4")["status"] == "ok"


def test_preflight_passes_on_a_readable_packed_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    packed = opc.build_packed_install(str(tmp_path / "install"))

    check = _profiles_dir_check(packed, "P1P", "0.4")

    assert check["status"] == "ok"
    assert "packed in BBL.opc" in check["message"]


def test_preflight_fails_on_a_packed_install_without_the_configured_nozzle(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    packed = opc.build_packed_install(str(tmp_path / "install"))

    check = _profiles_dir_check(packed, "P1P", "0.2")

    assert check["status"] == "error"
    assert "no machine profile for Bambu Lab P1P with a 0.2 mm nozzle" in check["message"]


def test_preflight_says_the_orcaslicer_version_is_not_supported_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.wrap(_BODY, format_version=3))

    check = _profiles_dir_check(packed, "P1P", "0.4")

    assert check["status"] == "error"
    assert "not supported yet" in check["message"]
    assert NEWEST_SUPPORTED_ORCA in check["message"]


def _profiles_dir_result(profiles_dir: str) -> dict:
    """The ``profiles-dir`` line of the list ``plate preflight`` prints.

    Folders are real ones under ``tmp_path``: ``os.path.isdir`` is not patched,
    because a patched one would also say ``machine/`` exists.
    """
    cfg = {"printer_ip": "1.2.3.4", "serial": "SN", "access_code": "x", "profiles_dir": profiles_dir}
    with (
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value=None),
        patch("bambu_cli.config.detect_profiles_dir", return_value=None),
        patch("shutil.which", return_value=None),
        settings_ctx(profiles_dir=profiles_dir, printer_model="P1P", nozzle_size="0.4"),
    ):
        checks = setup_cmd.collect_preflight_checks()
    (result,) = [check for check in checks if check["name"] == "profiles-dir"]
    return result


def test_plate_preflight_no_longer_passes_on_a_folder_it_cannot_slice_from(tmp_path, monkeypatch):
    """Through the whole check list: a 2.5-style BBL folder with no cache beside it."""
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    hollow = tmp_path / "profiles" / "BBL"
    (hollow / "filament").mkdir(parents=True)

    result = _profiles_dir_result(str(hollow))

    assert result["status"] == "error"
    assert "no machine/ folder" in result["message"]


def test_plate_preflight_passes_on_a_packed_install_and_refuses_an_unreadable_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    packed = opc.build_packed_install(str(tmp_path / "good"))
    unreadable = opc.build_packed_install(str(tmp_path / "newer"), opc.wrap(_BODY, format_version=3))

    good = _profiles_dir_result(packed)
    refused = _profiles_dir_result(unreadable)

    assert good["status"] == "ok"
    assert "Bambu Lab P1P" in good["message"] and "packed in BBL.opc" in good["message"]
    assert refused["status"] == "error"
    assert "not supported yet" in refused["message"] and NEWEST_SUPPORTED_ORCA in refused["message"]


# --- plate slice --list-settings ------------------------------------------------


def test_list_settings_reads_a_packed_install_and_names_the_configured_folder(tmp_path, slice_env, capsys):
    packed = opc.build_packed_install(str(tmp_path / "install"))

    with settings_ctx(orca_slicer=slice_env.launcher, profiles_dir=packed):
        cmd_slice(_slice_args(slice_env.model, slice_env.outdir, list_settings=True, json=True))

    listed = json.loads(capsys.readouterr().out)
    assert listed["action"] == "list_settings"
    # JSON output shortens the home folder to "~" and then uses "/" separators
    # (pytest's tmp_path is under the home folder on Windows).
    assert listed["profiles_dir"] == json_path(_display_path(packed))
    assert "layer_height" in listed["process"]["settings"]
    assert "filament_type" in listed["filament"]["settings"]
    assert "include" not in listed["process"]["settings"]
    assert os.listdir(slice_env.outdir) == []


def test_list_settings_refuses_an_unreadable_packed_install(tmp_path, slice_env):
    packed = opc.build_packed_install(str(tmp_path / "install"), opc.wrap(_BODY, format_version=3))

    with settings_ctx(orca_slicer=slice_env.launcher, profiles_dir=packed), pytest.raises(BambuError) as caught:
        cmd_slice(_slice_args(slice_env.model, slice_env.outdir, list_settings=True, json=True))

    assert "not supported yet" in str(caught.value)
    assert NEWEST_SUPPORTED_ORCA in str(caught.value)


# --- the TUI / wizard pre-check -------------------------------------------------


def test_tui_precheck_accepts_a_packed_install_and_reports_an_unreadable_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATE_CACHE_DIR", str(tmp_path / "plate-cache"))
    launcher = make_orca_launcher(str(tmp_path))
    good = opc.build_packed_install(str(tmp_path / "good"))
    bad = opc.build_packed_install(str(tmp_path / "bad"), opc.wrap(_BODY, format_version=3))

    assert slicer_preflight_problem(argparse.Namespace(orca_slicer=launcher, profiles_dir=good)) is None
    problem = slicer_preflight_problem(argparse.Namespace(orca_slicer=launcher, profiles_dir=bad))
    assert problem is not None and "not supported yet" in problem
