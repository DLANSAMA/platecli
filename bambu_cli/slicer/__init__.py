"""OrcaSlicer integration: option validation, profiles, STEP conversion,
subprocess run, and the `slice` command.

Re-exports are for convenient imports, not for mock targets. Tests inject
collaborators or patch the focused submodule where the name is defined/used
(e.g. ``bambu_cli.slicer.step_convert.subprocess``).
"""

from bambu_cli.slicer.cmd import cmd_slice  # noqa: F401
from bambu_cli.slicer.options import (  # noqa: F401
    _directory_input_message,
    _is_directory_input,
    _normalize_wall_type,
    _safe_temp_prefix,
    _sliced_output_path,
    _validate_slice_options,
)
from bambu_cli.slicer.orca import (  # noqa: F401
    _build_orcaslicer_cmd,
    _run_orcaslicer,
)
from bambu_cli.slicer.output import (  # noqa: F401
    _finalize_slice,
    _is_valid_sliced_3mf,
)
from bambu_cli.slicer.profiles import (  # noqa: F401
    _create_temp_profiles,
    _discover_process_profile,
    _process_profile_compatible,
    _profiles_dir_diagnostic,
    _slicer_executable_problem,
)
from bambu_cli.slicer.step_convert import (  # noqa: F401
    GMSH_MESH_SCALE,
    _convert_step_to_stl,
)
