"""--sim must never reach the Docker streamer or read the real access code."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bambu_cli.errors import BambuError
from tests.bambu_test_base import settings_ctx


def test_sim_refuses_streamer_without_touching_real_code(tmp_path):
    from bambu_cli.commands import cmd_snapshot
    from bambu_cli.context import get_current

    loader, run, urlopen = MagicMock(), MagicMock(), MagicMock()
    args = SimpleNamespace(output=str(tmp_path / "s.jpg"), json=False, allow_camera_streamer=True, unique=False)
    with settings_ctx(simulation=True, camera_allow_streamer=True):
        ctx = get_current()
        with pytest.raises(BambuError) as cm:
            cmd_snapshot(
                args,
                ctx,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=run,
                access_code_loader=loader,
                urlopen=urlopen,
            )
    assert cm.value.exit_code == 2
    loader.assert_not_called()
    run.assert_not_called()
    urlopen.assert_not_called()
