"""The streamer snapshot reports the container it actually used.

The JSON said ``"docker_container": "bambu_camera"`` whatever
``camera_container_name`` was configured to (2026-09-22 audit), so an agent
told to inspect or remove "the camera container" was pointed at the wrong one.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess

from tests.bambu_test_base import settings_ctx


def test_streamer_snapshot_reports_the_configured_container(tmp_path, capsys):
    from bambu_cli.commands.snapshot import cmd_snapshot

    jpeg = b"\xff\xd8" + b"\x00" * 64 + b"\xff\xd9"
    docker_calls = []

    def run(cmd, **kwargs):
        docker_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")

    class _Resp(io.BytesIO):
        status = 200

    args = argparse.Namespace(output=str(tmp_path / "snap.jpg"), json=True, unique=False, allow_camera_streamer=True)
    with settings_ctx(camera_container_name="my_cam", camera_stream_url="http://localhost:1985/api/frame.jpeg"):
        cmd_snapshot(
            args,
            grab_frame=lambda printer: None,  # no direct frame: take the streamer path
            which=lambda name: "/usr/bin/docker",
            subprocess_run=run,
            access_code_loader=lambda: "12345678",
            urlopen=lambda req, timeout=None: _Resp(jpeg),
            sleep=lambda s: None,
        )
    payload = json.loads(capsys.readouterr().out)
    assert payload["docker_container"] == "my_cam"
    assert any("my_cam" in cmd for cmd in docker_calls)
