import unittest
import os
import tempfile
from unittest.mock import MagicMock, patch

from bambu_cli.camera import _require_localhost_streamer_url, _write_snapshot_atomic


class TestCameraBase(unittest.TestCase):
    def test_require_localhost_streamer_url_valid(self):
        args = MagicMock()
        _require_localhost_streamer_url(args, "http://localhost:8080/stream", "out.jpg")
        _require_localhost_streamer_url(args, "http://127.0.0.1:8080/stream", "out.jpg")
        _require_localhost_streamer_url(args, "https://localhost:8080/stream", "out.jpg")
        _require_localhost_streamer_url(args, "http://[::1]:8080/stream", "out.jpg")
        # Should not raise any error

    @patch("bambu_cli.camera.abort")
    def test_require_localhost_streamer_url_invalid(self, mock_abort):
        mock_abort.side_effect = SystemExit(3)
        args = MagicMock()

        # Test non-localhost
        with self.assertRaises(SystemExit):
            _require_localhost_streamer_url(args, "http://remote-server:8080/stream", "out.jpg")

        # Test invalid scheme
        with self.assertRaises(SystemExit):
            _require_localhost_streamer_url(args, "ftp://localhost:8080/stream", "out.jpg")

    def test_write_snapshot_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            outpath = os.path.join(td, "snapshot.jpg")
            data = b"fake-image-data"
            _write_snapshot_atomic(outpath, data)
            self.assertTrue(os.path.exists(outpath))
            with open(outpath, "rb") as f:
                self.assertEqual(f.read(), data)

    @patch("os.replace")
    def test_write_snapshot_atomic_failure(self, mock_replace):
        # Trigger an exception during os.replace to test cleanup
        mock_replace.side_effect = PermissionError("Permission denied")
        with tempfile.TemporaryDirectory() as td:
            outpath = os.path.join(td, "snapshot.jpg")
            data = b"fake-image-data"

            with self.assertRaises(PermissionError):
                _write_snapshot_atomic(outpath, data)

            # The temp file should have been unlinked
            # Let's verify by checking if there's any file in the temp directory
            # (since we passed the dir as output's directory)
            self.assertEqual(os.listdir(td), [])


if __name__ == "__main__":
    unittest.main()
