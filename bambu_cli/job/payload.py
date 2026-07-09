"""Print payload generation and AMS/print-option parsing."""

from __future__ import annotations

import json
from urllib.parse import quote

from bambu_cli.cli import _namespace_get


def _print_next_command(args, basename):
    command = ["print", basename, "--confirm", "--json"]
    if _namespace_get(args, "use_ams", False):
        command.append("--use-ams")
    ams_mapping = _namespace_get(args, "ams_mapping")
    if ams_mapping:
        command.extend(["--ams-mapping", str(ams_mapping)])
    if _namespace_get(args, "timelapse", False):
        command.append("--timelapse")
    if _namespace_get(args, "skip_bed_leveling", False):
        command.append("--skip-bed-leveling")
    if _namespace_get(args, "skip_flow_cali", False):
        command.append("--skip-flow-cali")
    return command


def generate_print_payload(
    basename, use_ams=False, ams_mapping=None, timelapse=False, bed_leveling=True, flow_cali=True
):
    """Generate the JSON payload for the print command."""
    # Files are stored in /sdcard/model/ on the printer (referenced via the url field below).
    encoded_basename = quote(basename, safe="")
    print_cmd = {
        "sequence_id": "0",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "subtask_name": basename,
        "url": f"file:///sdcard/model/{encoded_basename}",
        "bed_type": "auto",
        "timelapse": timelapse,
        "bed_leveling": bed_leveling,
        "flow_cali": flow_cali,
        "vibration_cali": True,
        "layer_inspect": False,
        "use_ams": use_ams,
        "profile_id": "0",
        "project_id": "0",
        "subtask_id": "0",
        "task_id": "0",
    }

    if use_ams and ams_mapping is not None:
        print_cmd["ams_mapping"] = ams_mapping

    payload = json.dumps({"print": print_cmd})
    return payload


def _parse_print_options(args):
    """Validate print-only options and return the parsed AMS mapping."""
    from bambu_cli.constants import MAX_AMS_SLOT_INDEX

    raw_mapping = getattr(args, "ams_mapping", None)
    use_ams = getattr(args, "use_ams", False)
    if use_ams and not raw_mapping:
        # Do not silently omit ams_mapping and let firmware pick a default tray.
        return None, "--use-ams requires --ams-mapping (comma-separated slot indexes, e.g. '0' or '0,1,2')"
    if not raw_mapping:
        return None, None
    if not use_ams:
        return None, "--ams-mapping requires --use-ams"
    try:
        clean_mapping = raw_mapping.strip("[]")
        mapping = [int(x.strip()) for x in clean_mapping.split(",")]
    except ValueError:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if not mapping:
        return None, "Invalid AMS mapping format. Use comma-separated integers like '0' or '0,1,2'"
    if any(slot < 0 for slot in mapping):
        return None, "Invalid AMS mapping format. Slot indexes must be zero or positive integers like '0' or '0,1,2'"
    if any(slot > MAX_AMS_SLOT_INDEX for slot in mapping):
        return (
            None,
            f"Invalid AMS mapping: slot indexes must be between 0 and {MAX_AMS_SLOT_INDEX} "
            f"(4 slots per AMS unit, up to 4 units; got {mapping})",
        )
    return mapping, None
