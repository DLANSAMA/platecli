"""A1 and A1 mini machine profiles must slice from the command line.

OrcaSlicer refuses to slice with relative extrusion unless the layer-change
G-code resets the extruder (``G92 E0``). Bambu's X1/P1 profiles include it; the
A1 and A1 mini profiles do not, and the OrcaSlicer CLI does not apply the
exemption its GUI uses for Bambu printers. Every A1/A1 mini slice through
platecli therefore failed with "Relative extruder addressing requires resetting
the extruder position at each layer" (found 2026-09-22, reproduced on the base
commit). The temp machine profile now carries the reset when it is missing;
with relative extrusion it resets a counter and moves nothing.
"""

from __future__ import annotations

import json
import os

import pytest

from bambu_cli.slicer.profiles import _create_temp_machine


def _machine(tmp_path, **fields):
    path = tmp_path / "machine" / "Bambu Lab A1 mini 0.4 nozzle.json"
    os.makedirs(path.parent, exist_ok=True)
    path.write_text(json.dumps({"name": "Bambu Lab A1 mini 0.4 nozzle", **fields}), encoding="utf-8")
    return str(path)


def _written(tmp_path, path):
    handle = _create_temp_machine(path, str(tmp_path))
    with open(handle.name, encoding="utf-8") as fh:
        data = json.load(fh)
    os.unlink(handle.name)
    return data


def test_missing_extruder_reset_is_added(tmp_path):
    layer = (
        "; layer num/total_layer_count: {layer_num+1}/[total_layer_count]\nM73 L{layer_num+1}\nM991 S0 P{layer_num}\n"
    )
    data = _written(tmp_path, _machine(tmp_path, layer_change_gcode=layer))
    assert data["layer_change_gcode"].startswith(layer.rstrip("\n"))
    assert data["layer_change_gcode"].rstrip().endswith("G92 E0")


@pytest.mark.parametrize(
    "fields",
    [
        {"layer_change_gcode": "M73 L{layer_num+1}\nG92 E0 ; reset\n"},
        {"layer_change_gcode": "M73 L1\n", "before_layer_change_gcode": "  g92 e0.0\n"},
        {"layer_change_gcode": "M73 L1\n", "use_relative_e_distances": "0"},
    ],
)
def test_profiles_that_do_not_need_it_are_left_alone(tmp_path, fields):
    data = _written(tmp_path, _machine(tmp_path, **fields))
    assert data["layer_change_gcode"] == fields["layer_change_gcode"]
