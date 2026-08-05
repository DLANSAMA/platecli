"""Printer-side file download (FTPS retrieve), distinct from the URL downloader."""

from tests.bambu_test_base import *  # noqa: F401,F403

class TestBambuDownloadFile(unittest.TestCase):
    """download_file streams to a temp sibling then atomically replaces, so a
    failed transfer never corrupts an existing file at local_path."""

    def _printer_with_ftp(self, mock_ftp):
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        return printer

    def test_download_file_success_writes_content_no_temp_left(self):
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        content = b"new content"
        mock_ftp = MagicMock()
        mock_ftp.retrbinary.side_effect = lambda cmd, cb, blocksize=None: cb(content)
        mock_ftp.size.return_value = len(content)

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertTrue(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), content)
        self.assertEqual([p for p in os.listdir(d) if p.endswith(".part")], [])

    def test_download_file_failure_preserves_existing_and_cleans_temp(self):
        import ftplib
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        with open(local, "wb") as f:
            f.write(b"original good file")

        mock_ftp = MagicMock()
        mock_ftp.retrbinary.side_effect = ftplib.error_temp("connection dropped")

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertFalse(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"original good file")  # untouched
        self.assertEqual([p for p in os.listdir(d) if p.endswith(".part")], [])

    def test_download_file_truncated_vs_remote_size_fails_without_replace(self):
        """A short RETR must not replace local_path when remote SIZE is larger.

        Bambu FTPS skips TLS close-notify on the data channel, so a dropped
        transfer can still return from retrbinary; size verification is required.
        """
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        with open(local, "wb") as f:
            f.write(b"original good file")

        mock_ftp = MagicMock()
        # RETR writes only 4 bytes, but SIZE claims 100.
        mock_ftp.retrbinary.side_effect = lambda cmd, cb, blocksize=None: cb(b"trunc")
        mock_ftp.size.return_value = 100

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertFalse(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"original good file")  # not replaced
        self.assertEqual([p for p in os.listdir(d) if p.endswith(".part")], [])

    def test_download_file_size_match_succeeds(self):
        import tempfile

        d = tempfile.mkdtemp()
        local = os.path.join(d, "out.gcode")
        content = b"full content here"

        mock_ftp = MagicMock()
        mock_ftp.retrbinary.side_effect = lambda cmd, cb, blocksize=None: cb(content)
        mock_ftp.size.return_value = len(content)

        ok = self._printer_with_ftp(mock_ftp).download_file("/model/out.gcode", local)

        self.assertTrue(ok)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), content)


if __name__ == "__main__":
    unittest.main()
