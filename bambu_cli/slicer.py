import os
import sys
import json
import re
import tempfile

GMSH_MESH_SCALE = "0.5"

import subprocess
import platform
import shutil

from bambu_cli.logging_utils import logger

def _normalize_wall_type(wall_type):
    """Accept the old 'archaic' spelling as an alias for Orca's classic walls."""
    if wall_type == "archaic":
        return "classic"
    return wall_type

def _slicer_executable_problem(path):
    """Return a human-readable OrcaSlicer path problem, or None when usable."""
    from bambu_cli import bambu
    if path is None:
        return "OrcaSlicer path not specified in configuration."
    expanded = bambu._expand_path(path)
    display = bambu._display_path(expanded)
    if not os.path.exists(expanded):
        return f"OrcaSlicer not found at {display}"
    if sys.platform != "win32" and not os.access(expanded, os.X_OK):
        return f"OrcaSlicer is not executable at {display}; run `chmod +x {display}` or update orca_slicer in config.json."
    return None

def _sliced_output_path(filepath, output_dir=None, copies=1):
    from bambu_cli import bambu
    basename = os.path.splitext(bambu._portable_basename(filepath))[0]
    outdir = output_dir or os.path.dirname(os.path.abspath(filepath))
    if copies > 1:
        outfile = f"{basename}_x{copies}_sliced.3mf"
    else:
        outfile = f"{basename}_sliced.3mf"
    return os.path.join(outdir, outfile)

def _is_directory_input(path):
    """Return True for real directory inputs without trusting broad test mocks."""
    import stat
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except OSError:
        return False

def _directory_input_message(path):
    from bambu_cli import bambu
    return f"Path is a directory, not a file: {bambu._path_for_message(path)}"

def _validate_slice_options(args):
    from bambu_cli.cli import _namespace_get
    copies = getattr(args, 'copies', 1)
    if isinstance(copies, int) and copies < 1:
        return f"--copies must be a positive integer (got {copies})"
    infill = getattr(args, 'infill', 15)
    if isinstance(infill, int) and not (0 <= infill <= 100):
        return f"--infill must be between 0 and 100 (got {infill})"
    wall_type = _namespace_get(args, 'wall_type', None)
    if wall_type and wall_type not in ("normal", "classic", "archaic"):
        return "--wall-type must be one of: normal, classic"
    return None

def _safe_temp_prefix(value, fallback="tmp", max_length=48):
    """Return a filesystem-safe, bounded tempfile prefix ending in '_'."""
    prefix = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(value or "")).strip(" .")
    if not prefix:
        prefix = fallback
    return f"{prefix[:max_length]}_"

def _convert_step_to_stl(filepath):
    """Convert STEP to STL using gmsh."""
    from bambu_cli import bambu
    filepath = os.path.abspath(bambu._expand_path(filepath))
    stem = _safe_temp_prefix(os.path.splitext(os.path.basename(filepath))[0], fallback="converted")
    
    # Create a secure, owner-restricted temporary directory
    tmpdir = tempfile.mkdtemp(prefix="bambu_step_")
    stl_path = os.path.join(tmpdir, f"{stem}.stl")
    
    logger.info("🔄 Converting STEP → STL (OrcaSlicer CLI requires STL)...")
    try:
        cmd_args = ["gmsh", filepath, "-3", "-format", "stl", "-o", stl_path, "-clscale", GMSH_MESH_SCALE]
        if sys.platform != "win32" and shutil.which("nice"):
            cmd_args = ["nice", "-n", "10"] + cmd_args
        conv = subprocess.run(
            cmd_args,
            capture_output=True, text=True, timeout=60
        )
        if conv.returncode != 0 or not os.path.exists(stl_path):
            if conv.stdout or conv.stderr:
                logger.error(f"STEP conversion failed (RC={conv.returncode}).")
                if conv.stdout:
                    logger.error(f"Stdout:\n{conv.stdout}")
                if conv.stderr:
                    logger.error(f"Stderr:\n{conv.stderr}")
            logger.error("STEP conversion failed.")
            try:
                os.unlink(stl_path)
            except OSError:
                pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
            return None, False

    except FileNotFoundError:
        logger.error("STEP conversion failed. Please install gmsh for your platform.")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    except subprocess.TimeoutExpired:
        logger.error("STEP conversion timed out.")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    except OSError as exc:
        logger.error(f"STEP conversion failed: {bambu._exception_for_message(exc)}")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    try:
        size = os.path.getsize(stl_path) // 1024
    except OSError as exc:
        logger.error(f"STEP conversion failed: {bambu._exception_for_message(exc)}")
        try:
            os.unlink(stl_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
        return None, False
    logger.info(f"   Converted: {os.path.basename(stl_path)} ({size}KB)")
    return stl_path, True

def _process_profile_compatible(path, compatible_printer):
    if not compatible_printer:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    compatible = data.get("compatible_printers")
    if not isinstance(compatible, list):
        return False
    return compatible_printer in compatible

def _discover_process_profile(quality_arg, quality_map, model_code="P1P", compatible_printer=None):
    """Discover a matching process profile."""
    from bambu_cli import bambu
    layer_height = quality_arg if quality_arg.startswith("0.") else quality_map.get(quality_arg, f"0.20mm Standard @BBL {model_code}").split(" ")[0]
    proc_dir = os.path.join(bambu.PROFILES_DIR, "process")
    if os.path.isdir(proc_dir):
        files = os.listdir(proc_dir)
        process_file = next((f for f in files if f.startswith(layer_height) and model_code in f and "nozzle" not in f), None)
        if not process_file and compatible_printer:
            process_file = next(
                (
                    f for f in files
                    if f.startswith(layer_height)
                    and "nozzle" not in f
                    and _process_profile_compatible(os.path.join(proc_dir, f), compatible_printer)
                ),
                None,
            )
        if process_file:
            logger.debug(f"Profile auto-discovered: {process_file}")
            return os.path.join(proc_dir, process_file)
        else:
            # Fall back to standard 0.20mm for this model
            process_file = next((f for f in files if f.startswith("0.20mm") and model_code in f and "nozzle" not in f), None)
            if not process_file and compatible_printer:
                process_file = next(
                    (
                        f for f in files
                        if f.startswith("0.20mm")
                        and "nozzle" not in f
                        and _process_profile_compatible(os.path.join(proc_dir, f), compatible_printer)
                    ),
                    None,
                )
            if process_file:
                logger.warning(f"⚠️  Requested quality not found, using: {process_file}")
                return os.path.join(proc_dir, process_file)
            else:
                # If still not found, try falling back to P1P standard
                process_file = next((f for f in files if f.startswith("0.20mm") and "P1P" in f and "nozzle" not in f), None)
                if process_file:
                    logger.warning(f"⚠️  Requested quality/model profile not found, falling back to: {process_file}")
                    return os.path.join(proc_dir, process_file)
                else:
                    logger.error(f"No slicer profiles found in {proc_dir}")
                    return None
    return None

def _create_temp_profiles(process, filament, args):
    """Create temporary process and filament profiles with overrides."""
    infill = getattr(args, 'infill', 15)
    pattern = getattr(args, 'pattern', '3dhoneycomb')
    supports = getattr(args, 'supports', False)
    nozzle_temp = getattr(args, 'nozzle_temp', 220)
    bed_temp = getattr(args, 'bed_temp', 60)
    support_type = getattr(args, 'support_type', None)
    support_interface_density = getattr(args, 'support_interface_density', None)
    walls = getattr(args, 'walls', None)
    wall_type = getattr(args, 'wall_type', None)
    top_layers = getattr(args, 'top_layers', None)
    bottom_layers = getattr(args, 'bottom_layers', None)
    support_interface_pattern = getattr(args, 'support_interface_pattern', None)
    accel_wall = getattr(args, 'accel_wall', None)
    accel_wall_outer = getattr(args, 'accel_wall_outer', None)
    accel_infill = getattr(args, 'accel_infill', None)
    accel_travel = getattr(args, 'accel_travel', None)
    accel_first_layer = getattr(args, 'accel_first_layer', None)

    created = []
    try:
        tmp_process = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, prefix='proc_', encoding='utf-8')
        created.append(tmp_process)
        import sys
        print(f"[debug] open={open} process={process}", file=sys.stderr)
        with open(process, encoding='utf-8') as f:
            proc_data = json.load(f)
        proc_data['sparse_infill_density'] = f"{infill}%"
        proc_data['sparse_infill_pattern'] = pattern
        proc_data['enable_support'] = '1' if supports else '0'

        if support_type:
            proc_data['support_style'] = support_type
        if support_interface_density is not None:
            proc_data['support_interface_density'] = f"{support_interface_density}%"
        if support_interface_pattern:
            proc_data['support_interface_pattern'] = support_interface_pattern
        if walls is not None:
            proc_data['wall_loops'] = str(walls)
        if wall_type:
            wall_type = _normalize_wall_type(wall_type)
            proc_data['wall_generator'] = 'arachne' if wall_type == 'normal' else 'classic'
        if top_layers is not None:
            proc_data['top_shell_layers'] = str(top_layers)
        if bottom_layers is not None:
            proc_data['bottom_shell_layers'] = str(bottom_layers)

        # Acceleration settings
        if accel_wall is not None:
            proc_data['inner_wall_acceleration'] = str(accel_wall)
        if accel_wall_outer is not None:
            proc_data['outer_wall_acceleration'] = str(accel_wall_outer)
        if accel_infill is not None:
            proc_data['sparse_infill_acceleration'] = str(accel_infill)
        if accel_travel is not None:
            proc_data['travel_acceleration'] = str(accel_travel)
        if accel_first_layer is not None:
            proc_data['initial_layer_acceleration'] = str(accel_first_layer)

        json.dump(proc_data, tmp_process)
        tmp_process.close()

        # Copy filament profile and merge overrides
        tmp_filament = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, prefix='fil_', encoding='utf-8')
        created.append(tmp_filament)
        with open(filament, encoding='utf-8') as f:
            fil_data = json.load(f)

        nozzle_temp_str_list = [str(nozzle_temp)]
        fil_data['nozzle_temperature'] = nozzle_temp_str_list
        fil_data['nozzle_temperature_initial_layer'] = nozzle_temp_str_list

        bed_temp_str_list = [str(bed_temp)]
        from bambu_cli.constants import BED_PLATE_TYPES
        for plate in BED_PLATE_TYPES:
            fil_data[plate] = bed_temp_str_list
            fil_data[f'{plate}_initial_layer'] = bed_temp_str_list
        json.dump(fil_data, tmp_filament)
        tmp_filament.close()
    except Exception:
        for tmp in created:
            try:
                tmp.close()
            except Exception:
                pass
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        raise

    return tmp_process, tmp_filament

def cmd_slice(args):
    """Slice an STL/STEP file into a printable .3mf using OrcaSlicer."""
    from bambu_cli import bambu
    from bambu_cli.cli import _namespace_get, _exit_code_from_system_exit
    from bambu_cli.utils import emit_json, emit_json_error
    from bambu_cli.constants import EXIT_FILE_ERROR, EXIT_COMMAND_ERROR, EXIT_CONFIG_ERROR, EXIT_TIMEOUT

    filepath = bambu._expand_path(args.file)
    source_filepath = filepath
    if filepath.startswith('-'):
        message = f"Invalid filepath: {bambu._path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        sys.exit(EXIT_FILE_ERROR)
    if not os.path.exists(filepath):
        message = f"File not found: {bambu._path_for_message(filepath)}"
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        sys.exit(EXIT_FILE_ERROR)
    if _is_directory_input(filepath):
        message = _directory_input_message(filepath)
        logger.error(message)
        emit_json_error(args, "slice", EXIT_FILE_ERROR, message, failed_step="validate", file=filepath)
        sys.exit(EXIT_FILE_ERROR)

    slice_option_error = _validate_slice_options(args)
    if slice_option_error:
        logger.error(slice_option_error)
        emit_json_error(args, "slice", EXIT_COMMAND_ERROR, slice_option_error, failed_step="validate", file=filepath)
        sys.exit(EXIT_COMMAND_ERROR)
    copies = getattr(args, 'copies', 1)

    step_converted = False
    tmp_process = None
    tmp_filament = None
    result = None

    try:
        # Auto-convert STEP → STL (OrcaSlicer CLI doesn't support STEP)
        if filepath.lower().endswith(('.step', '.stp')):
            convert_func = getattr(bambu, "_convert_step_to_stl", _convert_step_to_stl)
            new_filepath, success = convert_func(filepath)
            if not success:
                emit_json_error(args, "slice", EXIT_COMMAND_ERROR, "STEP/STP conversion failed.", failed_step="convert", file=filepath)
                sys.exit(EXIT_COMMAND_ERROR)
            filepath = new_filepath
            step_converted = True

        model_info = bambu.MODEL_MAPPING.get(bambu.PRINTER_MODEL, bambu.MODEL_MAPPING["P1P"])
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

        outdir = bambu._expand_path(args.output) if args.output else os.path.dirname(os.path.abspath(source_filepath))
        if outdir.startswith('-'):
            message = f"Invalid output directory: {bambu._path_for_message(outdir)}"
            logger.error(message)
            emit_json_error(args, "slice", EXIT_COMMAND_ERROR, message, failed_step="validate", file=filepath, output=outdir)
            sys.exit(EXIT_COMMAND_ERROR)
        try:
            bambu._ensure_output_dir(outdir)
        except SystemExit as exc:
            emit_json_error(
                args,
                "slice",
                _exit_code_from_system_exit(exc, EXIT_FILE_ERROR),
                f"Could not prepare output directory: {bambu._path_for_message(outdir)}",
                failed_step="validate",
                file=filepath,
                output=outdir,
            )
            raise
        outpath = _sliced_output_path(source_filepath, outdir, copies)
        outfile = os.path.basename(outpath)

        machine_file = f"{full_model_name} {bambu.NOZZLE_SIZE} nozzle.json"
        machine = os.path.join(bambu.PROFILES_DIR, "machine", machine_file)
        if not os.path.exists(machine):
            logger.warning(f"⚠️  Machine profile '{machine_file}' not found. Trying standard P1P with {bambu.NOZZLE_SIZE} nozzle...")
            machine_file_fallback = f"Bambu Lab P1P {bambu.NOZZLE_SIZE} nozzle.json"
            machine_fallback = os.path.join(bambu.PROFILES_DIR, "machine", machine_file_fallback)
            if os.path.exists(machine_fallback):
                machine = machine_fallback
            else:
                logger.warning(f"⚠️  Fallback machine profile '{machine_file_fallback}' not found. Using standard P1P 0.4 nozzle.")
                machine = os.path.join(bambu.PROFILES_DIR, "machine", "Bambu Lab P1P 0.4 nozzle.json")

        process = os.path.join(bambu.PROFILES_DIR, "process", process_file)

        filament_dir = os.path.join(bambu.PROFILES_DIR, "filament")
        filament = None
        requested_filament_name = _namespace_get(args, 'filament', 'PLA Basic') or 'PLA Basic'
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
            logger.warning(f"⚠️  Filament matching '{requested_filament_name}' not found. Falling back to Bambu PLA Basic.")
            filament = os.path.join(filament_dir, "Bambu PLA Basic @base.json")

        slicer_problem = _slicer_executable_problem(bambu.ORCA_SLICER)
        if slicer_problem:
            message = slicer_problem
            logger.error(message)
            logger.info("Please update 'orca_slicer' in your config.json or place it in the tools/ directory.")
            emit_json_error(args, "slice", EXIT_CONFIG_ERROR, message, failed_step="slicer", file=filepath, orca_slicer=bambu.ORCA_SLICER)
            sys.exit(EXIT_CONFIG_ERROR)

        if not os.path.exists(process):
            compatible_printer = f"{full_model_name} {bambu.NOZZLE_SIZE} nozzle"
            process = _discover_process_profile(
                args.quality,
                quality_map,
                model_code=model_code,
                compatible_printer=compatible_printer,
            )
            if not process:
                emit_json_error(args, "slice", EXIT_CONFIG_ERROR, "No slicer process profile found.", failed_step="profiles", file=filepath)
                sys.exit(EXIT_CONFIG_ERROR)

        for path, name in [(machine, "machine"), (filament, "filament")]:
            if not os.path.exists(path):
                message = f"Missing {name} profile: {bambu._path_for_message(path)}"
                logger.error(message)
                emit_json_error(args, "slice", EXIT_CONFIG_ERROR, message, failed_step="profiles", file=filepath, profile=name, path=path)
                sys.exit(EXIT_CONFIG_ERROR)

        try:
            tmp_process, tmp_filament = _create_temp_profiles(process, filament, args)
        except Exception as exc:
            message = f"Failed to prepare OrcaSlicer profiles: {bambu._exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="profiles",
                file=filepath,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        cmd = []
        if platform.system() == "Linux":
            # Prefer a real display (gives working GPU GL + thumbnails). Only fall back
            # to a virtual framebuffer when no display is present (e.g. headless/agent runs).
            has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            if not has_display:
                if shutil.which("xvfb-run"):
                    cmd.extend(["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24 +extension GLX +render"])
                else:
                    logger.warning("⚠️  No DISPLAY and xvfb-run not found; OrcaSlicer may fail headless. "
                                   "Install it: `sudo pacman -S xorg-server-xvfb` (Arch/CachyOS) or `sudo apt install xvfb` (Debian/Ubuntu).")

        cmd.extend([
            bambu.ORCA_SLICER,
            "--load-settings", f"{machine};{tmp_process.name}",
            "--load-filaments", tmp_filament.name,
            "--slice", "0",
            "--export-3mf", outfile,
            "--outputdir", outdir,
        ])
        if copies > 1:
            cmd.extend(["--arrange", "1"])
        if getattr(args, 'threads', None) is not None:
            cmd.extend(["--threads", str(args.threads)])
        if sys.platform != "win32" and shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd
        cmd.extend([filepath] * copies)

        layer_height = layer.split(" ")[0]
        infill = getattr(args, 'infill', 15)
        pattern = getattr(args, 'pattern', '3dhoneycomb')
        nozzle_temp = getattr(args, 'nozzle_temp', 220)
        bed_temp = getattr(args, 'bed_temp', 60)
        supports = getattr(args, 'supports', False)

        filament_name = os.path.basename(filament).replace('.json', '').replace(' @base', '').strip()

        settings_summary = f"{filament_name}, {layer_height} layer, {infill}% {pattern}, nozzle {nozzle_temp}°C, bed {bed_temp}°C"
        if copies > 1:
            settings_summary += f", {copies} copies"
        if supports:
            settings_summary += ", supports ON"
            if getattr(args, 'support_type', None):
                settings_summary += f" ({args.support_type})"
        if getattr(args, 'walls', None):
            settings_summary += f", {args.walls} walls"
        logger.info(f"✂️  Slicing {os.path.basename(filepath)} ({settings_summary})...")

        # Dynamically get timeouts (A0530-NET-07)
        slicer_timeout = bambu.get_slicer_timeout(args)

        try:
            # Interactive visual feedback logging (A0530-UI-05)
            logger.info("   Running OrcaSlicer background worker...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            stdout_lines = []
            stderr_lines = []

            progress = None
            task_id = None
            try:
                if not getattr(args, "json", False):
                    from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn
                    progress = Progress(
                        TextColumn("[bold blue]{task.description}", justify="right"),
                        BarColumn(bar_width=None),
                        "[progress.percentage]{task.percentage:>3.1f}%",
                        "•",
                        TimeElapsedColumn(),
                        transient=True
                    )
                    progress.start()
                    task_id = progress.add_task(f"Slicing {os.path.basename(filepath)}", total=100)
            except ImportError:
                pass

            import queue
            import re
            import threading
            import time

            chunk_queue = queue.Queue()

            def _pump(stream, name):
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        break
                    chunk_queue.put((name, chunk.decode("utf-8", errors="replace")))

            readers = [
                threading.Thread(target=_pump, args=(process.stdout, "stdout"), daemon=True),
                threading.Thread(target=_pump, args=(process.stderr, "stderr"), daemon=True),
            ]
            for t in readers:
                t.start()

            def _handle_stdout_line(line_str):
                pct_match = re.search(r'(\d+)%', line_str)
                if progress and task_id is not None and pct_match:
                    progress.update(task_id, completed=int(pct_match.group(1)))
                if any(pat in line_str.lower() for pat in ("progress", "%", "slicing", "exporting")):
                    logger.info(f"   [OrcaSlicer] {line_str}")
                elif line_str:
                    logger.debug(f"   [OrcaSlicer] {line_str}")

            stdout_carry = ""

            def _consume(name, text):
                nonlocal stdout_carry
                if name == "stderr":
                    stderr_lines.append(text)
                    return
                stdout_lines.append(text)
                # OrcaSlicer emits progress lines terminated by \r as well as \n
                parts = re.split(r'[\r\n]', stdout_carry + text)
                stdout_carry = parts.pop()
                for part in parts:
                    line_str = part.strip()
                    if line_str:
                        _handle_stdout_line(line_str)

            try:
                start_time = time.monotonic()
                while True:
                    try:
                        name, text = chunk_queue.get(timeout=0.5)
                        _consume(name, text)
                    except queue.Empty:
                        pass
                    if time.monotonic() - start_time > slicer_timeout:
                        raise subprocess.TimeoutExpired(cmd, slicer_timeout)
                    if process.poll() is not None:
                        alive = False
                        for t in readers:
                            t.join(timeout=2)
                            alive = alive or t.is_alive()
                        if not alive:
                            break
                # Final drain after both readers exit
                while True:
                    try:
                        name, text = chunk_queue.get_nowait()
                    except queue.Empty:
                        break
                    _consume(name, text)
                if stdout_carry.strip():
                    _handle_stdout_line(stdout_carry.strip())
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            finally:
                if progress:
                    progress.stop()
                for stream in (process.stdout, process.stderr):
                    try:
                        stream.close()
                    except Exception:
                        pass

            process.wait()
            result = subprocess.CompletedProcess(
                cmd,
                returncode=process.returncode,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines),
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
            sys.exit(EXIT_TIMEOUT)
        except OSError as exc:
            message = f"Failed to run OrcaSlicer: {bambu._exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_CONFIG_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                orca_slicer=bambu.ORCA_SLICER,
                output=outpath,
            )
            sys.exit(EXIT_CONFIG_ERROR)
    finally:
        for tmp_file in (tmp_process, tmp_filament):
            if tmp_file is not None and hasattr(tmp_file, 'name'):
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

    # OrcaSlicer can exit non-zero on a headless GL/thumbnail step even when the slice
    # itself succeeded and a valid .3mf was written. Treat that specific case as success.
    _benign_rc = False
    if result is not None and result.returncode != 0 and os.path.exists(outpath):
        try:
            _ok_size = os.path.getsize(outpath) > 0
        except OSError:
            _ok_size = False
        _blob = ((result.stdout or "") + (result.stderr or "")).lower()
        _gl_noise = any(k in _blob for k in ("glfw", "glew", "init opengl failed", "skip thumbnail"))
        _real_err = ("nothing to be sliced" in _blob) or ("slicing error" in _blob)
        _benign_rc = _ok_size and _gl_noise and not _real_err
        if _benign_rc:
            logger.warning("   OrcaSlicer exited non-zero on a headless GL/thumbnail step, but a valid .3mf was produced — continuing.")
    if result is not None and os.path.exists(outpath) and (result.returncode == 0 or _benign_rc):
        try:
            size = os.path.getsize(outpath)
        except OSError as exc:
            message = f"Could not read sliced output file: {bambu._exception_for_message(exc)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
            )
            sys.exit(EXIT_FILE_ERROR)
        if size <= 0:
            bambu._remove_partial_file(outpath)
            message = f"Slicing produced an empty output file: {bambu._path_for_message(outpath)}"
            logger.error(message)
            emit_json_error(
                args,
                "slice",
                EXIT_FILE_ERROR,
                message,
                failed_step="slicer",
                file=filepath,
                output=outpath,
                bytes=size,
            )
            sys.exit(EXIT_FILE_ERROR)
        logger.info(f"✅ Sliced: {bambu._path_for_message(outpath)} ({size // 1024}KB)")
        if bool(_namespace_get(args, "json", False)):
            emit_json({
                "status": "sliced",
                "command": "slice",
                "file": bambu._expand_path(args.file),
                "path": outpath,
                "filename": os.path.basename(outpath),
                "bytes": size,
                "step_converted": step_converted,
            })
        return outpath
    else:
        rc = result.returncode if result is not None else -1
        message = f"Slicing failed (RC={rc})"
        logger.error(message)
        all_output = ""
        if result is not None:
            all_output = (result.stdout or "") + (result.stderr or "")
        error_found = False
        for line in all_output.split('\n'):
            lower_line = line.lower()
            if '[error]' in lower_line or 'nothing to be sliced' in lower_line or 'error:' in lower_line:
                msg = line.split('] ')[-1].strip() if '] ' in line else line.strip()
                if msg:
                    logger.error(f"   {msg}")
                    error_found = True

        if not error_found:
            logger.info("   Check OrcaSlicer profiles or syntax.")
        emit_json_error(
            args,
            "slice",
            EXIT_COMMAND_ERROR,
            message,
            failed_step="slicer",
            file=filepath,
            output=outpath,
            returncode=rc,
        )
        sys.exit(EXIT_COMMAND_ERROR)
