"""Snapshot output handling: non-colliding filenames and the JSON metadata envelope."""

import hashlib

from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestSnapshotUniqueNaming(unittest.TestCase):
    """--unique flag produces timestamped filenames without wall-clock dependency."""

    def _snap_args(self, output=None, unique=False):
        args = MagicMock()
        args.output = output
        args.unique = unique
        args.json = False
        return args

    def test_unique_flag_no_output_uses_timestamp(self):
        """With --unique and no --output, filename is printer_snapshot_<stamp>.jpg."""
        import datetime
        from bambu_cli.protocols.camera import _utc_stamp

        fixed_dt = datetime.datetime(2026, 7, 24, 19, 15, 30, tzinfo=datetime.timezone.utc)
        stamp = _utc_stamp(fixed_dt)
        self.assertEqual(stamp, "20260724T191530Z")

        from bambu_cli.commands import cmd_snapshot

        saved_paths = []

        def _fake_write(path, data):
            saved_paths.append(path)

        args = self._snap_args(output=None, unique=True)

        with (
            patch("bambu_cli.protocols.camera._write_snapshot_atomic", side_effect=_fake_write),
            patch("bambu_cli.logging_utils._BACKEND", MagicMock()),
            patch("os.path.getsize", return_value=1024),
            patch("bambu_cli.protocols.camera._ensure_parent_dir"),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: b"\xff\xd8\xff\xd9",
                now=fixed_dt,
            )

        self.assertEqual(len(saved_paths), 1)
        self.assertIn("20260724T191530Z", saved_paths[0])
        self.assertTrue(saved_paths[0].endswith(".jpg"))
        self.assertIn("printer_snapshot_", saved_paths[0])

    def test_unique_flag_with_output_inserts_timestamp_before_ext(self):
        """With --unique and --output cam.jpg, result is cam_<stamp>.jpg."""
        import datetime
        from bambu_cli.commands import cmd_snapshot

        fixed_dt = datetime.datetime(2026, 7, 24, 19, 15, 30, tzinfo=datetime.timezone.utc)
        saved_paths = []

        def _fake_write(path, data):
            saved_paths.append(path)

        args = self._snap_args(output="cam.jpg", unique=True)

        with (
            patch("bambu_cli.protocols.camera._write_snapshot_atomic", side_effect=_fake_write),
            patch("bambu_cli.logging_utils._BACKEND", MagicMock()),
            patch("os.path.getsize", return_value=1024),
            patch("bambu_cli.protocols.camera._ensure_parent_dir"),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: b"\xff\xd8\xff\xd9",
                now=fixed_dt,
            )

        self.assertEqual(len(saved_paths), 1)
        self.assertTrue(saved_paths[0].endswith("20260724T191530Z.jpg"))
        self.assertTrue(saved_paths[0].startswith("cam_") or "cam_" in saved_paths[0])

    def test_no_unique_flag_uses_default_name(self):
        """Without --unique, saves to the given --output name unchanged."""
        from bambu_cli.commands import cmd_snapshot

        saved_paths = []

        def _fake_write(path, data):
            saved_paths.append(path)

        args = self._snap_args(output="myshot.jpg", unique=False)

        with (
            patch("bambu_cli.protocols.camera._write_snapshot_atomic", side_effect=_fake_write),
            patch("bambu_cli.logging_utils._BACKEND", MagicMock()),
            patch("os.path.getsize", return_value=1024),
            patch("bambu_cli.protocols.camera._ensure_parent_dir"),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: b"\xff\xd8\xff\xd9",
            )

        self.assertEqual(len(saved_paths), 1)
        self.assertTrue(saved_paths[0].endswith("myshot.jpg"))
        self.assertNotIn("Z.jpg", saved_paths[0])

class TestSnapshotJsonMetadata(unittest.TestCase):
    """captured_at and sha256 appear in --json output on every successful capture."""

    def _snap_args(self, output="snap.jpg", unique=False):
        args = MagicMock()
        args.output = output
        args.unique = unique
        args.json = True
        return args

    def test_direct_path_json_includes_captured_at_and_sha256(self, capsys=None):
        """Direct grab path: JSON output must include captured_at and sha256."""
        import io
        import contextlib
        import hashlib
        from bambu_cli.commands import cmd_snapshot

        frame_data = b"\xff\xd8\xff\xd9"
        expected_sha = hashlib.sha256(frame_data).hexdigest()
        args = self._snap_args()

        buf = io.StringIO()
        with (
            patch("bambu_cli.protocols.camera._write_snapshot_atomic"),
            patch("bambu_cli.logging_utils._BACKEND", MagicMock()),
            patch("os.path.getsize", return_value=len(frame_data)),
            patch("bambu_cli.protocols.camera._ensure_parent_dir"),
            patch("bambu_cli.protocols.camera.emit_json", side_effect=lambda d: buf.write(json.dumps(d))),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: frame_data,
            )

        payload = json.loads(buf.getvalue())
        self.assertIn("captured_at", payload)
        self.assertIn("sha256", payload)
        self.assertEqual(payload["sha256"], expected_sha)
        # captured_at should look like ISO-8601 UTC
        self.assertRegex(payload["captured_at"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_docker_path_json_includes_captured_at_and_sha256(self):
        """Docker streamer path: JSON output must include captured_at and sha256."""
        import io
        import hashlib
        from bambu_cli.commands import cmd_snapshot

        frame_data = b"\xff\xd8fake_image_data\xff\xd9"
        expected_sha = hashlib.sha256(frame_data).hexdigest()
        args = self._snap_args()

        mock_response = MagicMock()
        mock_response.read.return_value = frame_data
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="true"))

        buf = io.StringIO()
        with (
            patch("bambu_cli.protocols.camera._write_snapshot_atomic"),
            patch("bambu_cli.logging_utils._BACKEND", MagicMock()),
            patch("os.path.getsize", return_value=len(frame_data)),
            patch("bambu_cli.protocols.camera._ensure_parent_dir"),
            patch("bambu_cli.protocols.camera.emit_json", side_effect=lambda d: buf.write(json.dumps(d))),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,  # force Docker path
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
                sleep=MagicMock(),
            )

        payload = json.loads(buf.getvalue())
        self.assertIn("captured_at", payload)
        self.assertIn("sha256", payload)
        self.assertEqual(payload["sha256"], expected_sha)
        self.assertRegex(payload["captured_at"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


if __name__ == "__main__":
    unittest.main()
