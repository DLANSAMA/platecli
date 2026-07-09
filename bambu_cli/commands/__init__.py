"""Printer subcommand handlers (status, upload, print, doctor, ...).

Re-exports are for convenient imports and CLI dispatch
(``bambu_cli.cli._resolve_command``), not for mock targets. Tests inject
collaborators or patch the focused submodule where the name is defined/used.
"""

from bambu_cli.commands.device import (  # noqa: F401
    cmd_light,
    cmd_pause,
    cmd_resume,
    cmd_stop,
)
from bambu_cli.commands.doctor import (  # noqa: F401
    _offer_pin_fingerprint,
    cmd_doctor,
)
from bambu_cli.commands.files import (  # noqa: F401
    cmd_delete,
    cmd_files,
    cmd_upload,
)
from bambu_cli.commands.gcode import cmd_gcode  # noqa: F401
from bambu_cli.commands.print_cmd import cmd_print  # noqa: F401
from bambu_cli.commands.setup_wrappers import (  # noqa: F401
    cmd_config,
    cmd_download,
    cmd_job,
    cmd_preflight,
    cmd_setup,
    cmd_slice,
    cmd_snapshot,
)
from bambu_cli.commands.status import cmd_status  # noqa: F401
