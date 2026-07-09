"""The `slice` command: orchestrate convert → profiles → OrcaSlicer → finalize."""

from __future__ import annotations

import argparse
import os
import subprocess

from bambu_cli.cli import _display_path, _exception_for_message, _expand_path, _namespace_get, _path_for_message
from bambu_cli.config import MODEL_MAPPING, get_slicer_timeout
from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR, EXIT_FILE_ERROR, EXIT_TIMEOUT
from bambu_cli.context import current_settings
from bambu_cli.errors import BambuError, abort
from bambu_cli.logging_utils import logger
from bambu_cli.slicer.options import (
    _directory_input_message,
    _is_directory_input,
    _sliced_output_path,
    _validate_slice_options,
)
from bambu_cli.slicer.orca import _build_orcaslicer_cmd, _run_orcaslicer
from bambu_cli.slicer.output import _finalize_slice
from bambu_cli.slicer.profiles import (
    _create_temp_profiles,
    _discover_process_profile,
    _profiles_dir_diagnostic,
    _slicer_executable_problem,
)
from bambu_cli.slicer.step_convert import _convert_step_to_stl
from bambu_cli.utils import _ensure_output_dir, emit_json_error


def cmd_slice(
    args: argparse.Namespace,
) -> str:
    """Slice an STL/STEP file into a printable .3mf using OrcaSlicer."""
    settings = current_settings()
    filepath = _expand_path(args.file)
    source_filepath = filepath
    if filepath.startswith("-"):
        message = f"Invalid filepath: {_path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if not os.path.exists(filepath):
        message = f"File not found: {_path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)
    if _is_directory_input(filepath):
        message = _directory_input_message(filepath)
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_FILE_ERROR)

    slice_option_error = _validate_slice_options(args)
    if slice_option_error:
        logger.error(slice_option_error)
        emit_json_error(args, "slice", EXIT_COMMAND_ERROR, slice_option_error, failed_step="validate", file=filepath)
        abort("", exit_code=EXIT_COMMAND_ERROR)
    copies = getattr(args, "copies", 1)

    step_converted = False
    tmp_process = None
    tmp_filament = None
    result = None

    try:
        # Auto-convert STEP → STL (OrcaSlicer CLI doesn't support STEP)
        if filepath.lower().endswith((".step", ".stp")):
            new_filepath, success = _convert_step_to_stl(filepath)
            if not success:
                emit_json_error(
                    args,
                    "slice",
                    EXIT_COMMAND_ERROR,
                    "STEP/STP conversion failed.",
                    failed_step="convert",
                    file=filepath,
                )
                abort("", exit_code=EXIT_COMMAND_ERROR)
            filepath = new_filepath
            step_converted = True

        model_info = MODEL_MAPPING.get(settings.printer_model, MODEL_MAPPING["P1P"])
        model_code = model_info["token"]
        full_model_name = model_info["full_name"]

        quality_map = {
            "draft": f"0.28mm Extra Draft @BBL {model_code}",
            "standard": f"0.20mm Standard @BBL {model_code}",
            "high": f"0.12mm Fine @BBL {model_code}",
            "0.28": f"0.28mm Extra Draft @BBL {model_code}",
            "0.20": f"0.20mm Standard @BBL {model_code}",
            "0.12": f"0.12mm Fine @BBL {model_code}",
            "0.16": f"0.16mm Optimal @BBL {model_code}",
            "0.24": f"0.24mm Draft @BBL {model_code}",
        }
        layer = quality_map.get(args.quality, f"0.20mm Standard @BBL {model_code}")
        process_file = f"{layer}.json"

        outdir = _expand_path(args.output) if args.output else os.path.dirname(os.path.abspath(source_filepath))
        if outdir.startswith("-"):
            message = f"Invalid output directory: {_path_for_message(outdir)}"
            logger.error(message)
            emit_json_error(
                args, "slice", EXIT_COMMAND_ERROR, message, failed_step="validate", file=filepath, output=outdir
            )
            abort("", exit_code=EXIT_COMMAND_ERROR)
        try:
            _ensure_output_dir(outdir)
        except BambuError as exc:
            emit_json_error(
                args,
                "slice",
                (getattr(exc, "exit_code", None) or EXIT_FILE_ERROR),
                f"Could not prepare output directory: {_path_for_message(outdir)}",
                failed_step="validate",
                file=filepath,
                output=outdir,
            )
            raise
        outpath = _sliced_output_path(source_filepath, outdir, copies)
        outfile = os.path.basename(outpath)

        machine_file = f"{full_model_name} {settings.nozzle_size} nozzle.json"
        machine = os.path.join(settings.profiles_dir, "machine", machine_file)
        if not os.path.exists(machine):
            logger.warning(
                f"⚠️  Machine profile '{machine_file}' not found. Trying standard P1P with {settings.nozzle_size} nozzle..."
            )
            machine_file_fallback = f"Bambu Lab P1P {settings.nozzle_size} nozzle.json"
            machine_fallback = os.path.join(settings.profiles_dir, "machine", machine_file_fallback)
            if os.path.exists(machine_fallback):
                machine = machine_fallback
            else:
                logger.warning(
                    f"⚠️  Fallback machine profile '{machine_file_fallback}' not found. Using standard P1P 0.4 nozzle."
                )
                machine = os.path.join(settings.profiles_dir, "machine", "Bambu Lab P1P 0.4 nozzle.json")

        process = os.path.join(settings.profiles_dir, "process", process_file)

        filament_dir = os.path.join(settings.profiles_dir, "filament")
        filament = None
        requested_filament_name = _namespace_get(args, "filament", "PLA Basic") or "PLA Basic"
        requested_filament = str(requested_filament_name).lower()

        if os.path.isdir(filament_dir):
            files = os.listdir(filament_dir)
            for f in files:
                if requested_filament in f.lower() and "@base" in f:
                    filament = os.path.join(filament_dir, f)
                    break
            if not filament:
                for f in files:
                    if requested_filament in f.lower():
                        filament = os.path.join(filament_dir, f)
                        break

        if not filament or not os.path.exists(filament):
            logger.warning(
                f"⚠️  Filament matching '{requested_filament_name}' not found. Falling back to Bambu PLA Basic."
            )
            filament = os.path.join(filament_dir, "Bambu PLA Basic @base.json")

        slicer_problem = _slicer_executable_problem(settings.orca_slicer)
        if slicer_problem:
            from bambu_cli.config import detect_orca_slicer

            message = slicer_problem
            logger.error(message)
            detected_orca = detect_orca_slicer()
            if detected_orca and detected_orca != settings.orca_slicer:
                logger.info(
                    f'Detected OrcaSlicer at {_display_path(detected_orca)} — set "orca_slicer" to this in config.json.'
                )
            else:
                logger.info("Please update 'orca_slicer' in your config.json or place it in the tools/ directory.")
            emit_json_error(
                args,
                "slice",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                orca_slicer=settings.orca_slicer,
                detected_orca_slicer=detected_orca,
            )
            abort("", exit_code=EXIT_CONFIG_ERROR)

        if not os.path.exists(process):
            compatible_printer = f"{full_model_name} {settings.nozzle_size} nozzle"
            discovered_process = _discover_process_profile(
                args.quality,
                quality_map,
                model_code=model_code,
                compatible_printer=compatible_printer,
                profiles_dir=settings.profiles_dir,
            )
            if not discovered_process:
                hint, detected_profiles = _profiles_dir_diagnostic(settings.profiles_dir)
                if hint:
                    logger.info(hint)
                emit_json_error(
                    args,
                    "slice",
                    EXIT_CONFIG_ERROR,
                    "No slicer process profile found.",
                    failed_step="profiles",
                    file=filepath,
                    profiles_dir=settings.profiles_dir,
                    detected_profiles_dir=detected_profiles,
                )
                abort("", exit_code=EXIT_CONFIG_ERROR)
            process = discovered_process

        for path, name in [(machine, "machine"), (filament, "filament")]:
            if not os.path.exists(path):
                message = f"Missing {name} profile: {_path_for_message(path)}"
                logger.error(message)
                hint, detected_profiles = _profiles_dir_diagnostic(settings.profiles_dir)
                if hint:
                    logger.info(hint)
                emit_json_error(
                    args,
                    "slice",
                    EXIT_CONFIG_ERROR,
                    message,
                    failed_step="profiles",
                    file=filepath,
                    profile=name,
                    path=path,
                    profiles_dir=settings.profiles_dir,
                    detected_profiles_dir=detected_profiles,
                )
                abort("", exit_code=EXIT_CONFIG_ERROR)

        try:
            tmp_process, tmp_filament = _create_temp_profiles(process, filament, args)
        except Exception as exc:
            message = f"Failed to prepare OrcaSlicer profiles: {_exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="profiles",
                file=filepath,
            )
            abort("", exit_code=EXIT_CONFIG_ERROR)

        cmd = _build_orcaslicer_cmd(
            settings,
            args,
            machine,
            tmp_process.name,
            tmp_filament.name,
            outfile,
            outdir,
            copies,
            filepath,
        )

        layer_height = layer.split(" ")[0]
        infill = getattr(args, "infill", 15)
        pattern = getattr(args, "pattern", "3dhoneycomb")
        nozzle_temp = getattr(args, "nozzle_temp", 220)
        bed_temp = getattr(args, "bed_temp", 60)
        supports = getattr(args, "supports", False)

        filament_name = os.path.basename(filament).replace(".json", "").replace(" @base", "").strip()

        settings_summary = (
            f"{filament_name}, {layer_height} layer, {infill}% {pattern}, nozzle {nozzle_temp}°C, bed {bed_temp}°C"
        )
        if copies > 1:
            settings_summary += f", {copies} copies"
        if supports:
            settings_summary += ", supports ON"
            if getattr(args, "support_type", None):
                settings_summary += f" ({args.support_type})"
        if getattr(args, "walls", None):
            settings_summary += f", {args.walls} walls"
        logger.info(f"✂️  Slicing {os.path.basename(filepath)} ({settings_summary})...")

        # Dynamically get timeouts (A0530-NET-07)
        slicer_timeout = get_slicer_timeout(args)

        try:
            result = _run_orcaslicer(
                cmd,
                slicer_timeout,
                show_progress=not getattr(args, "json", False),
                filepath=filepath,
            )
        except subprocess.TimeoutExpired:
            message = f"Slicing timed out after {slicer_timeout} seconds"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_TIMEOUT,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
            )
            abort("", exit_code=EXIT_TIMEOUT)
        except OSError as exc:
            message = f"Failed to run OrcaSlicer: {_exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                orca_slicer=settings.orca_slicer,
                output=outpath,
            )
            abort("", exit_code=EXIT_CONFIG_ERROR)
    finally:
        for tmp_file in (tmp_process, tmp_filament):
            if tmp_file is not None and hasattr(tmp_file, "name"):
                try:
                    os.unlink(tmp_file.name)
                except OSError:
                    pass
        if step_converted and filepath and os.path.exists(filepath):
            try:
                os.unlink(filepath)
            except OSError:
                pass
            try:
                os.rmdir(os.path.dirname(filepath))
            except OSError:
                pass

    return _finalize_slice(result, outpath, args, filepath, step_converted)
