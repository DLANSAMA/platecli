"""Upload command: path validation, size/name limits, and the resume/retry path.

Split out of the former 1333-line test_printer_commands.py (docs/test-backlog.md:
one module per command surface, so a failure names the command it broke)."""

from tests.bambu_test_base import *  # noqa: F401,F403

class TestBambuCmdUploadEdgeCases(unittest.TestCase):
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_upload_invalid_filepath(self, mock_exit, mock_logger):
        from bambu_cli.commands import cmd_upload

        args = MagicMock()
        args.file = "-invalid.gcode"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertIn("Invalid filepath", str(cm.exception))

    @patch("os.path.exists")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_upload_file_not_found(self, mock_exit, mock_logger, mock_exists):
        from bambu_cli.commands import cmd_upload

        mock_exists.return_value = False
        args = MagicMock()
        args.file = "missing.gcode"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertIn("File not found", str(cm.exception))

    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_upload_dry_run_success(self, mock_logger, mock_get_printer, mock_getsize, mock_exists):
        from bambu_cli.commands import cmd_upload

        mock_exists.return_value = True
        mock_getsize.return_value = 1024
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = True

        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)
        mock_logger.info.assert_any_call("   ✅ Printer reachable.")
        mock_logger.info.assert_any_call("   ✅ Local file test.gcode exists (1KB)")

    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_upload_dry_run_fail(self, mock_exit, mock_logger, mock_get_printer, mock_getsize, mock_exists):
        from bambu_cli.commands import cmd_upload

        mock_exists.return_value = True
        mock_getsize.return_value = 1024
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = True

        mock_get_ftp = MagicMock(side_effect=OSError("FTP Error"))
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        # The dry-run now surfaces the real cause instead of a fixed, misleading
        # "Could not reach printer." (a cert-pin mismatch must be distinguishable
        # from an off printer) — see fix/audit-cli-json-camera.
        self.assertIn("FTP Error", str(cm.exception))

    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("time.sleep")
    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.printer.logger")
    @patch("builtins.open", new_callable=mock_open)
    def test_cmd_upload_resume_offset(
        self, mock_file, mock_logger, mock_get_printer, mock_sleep, mock_getsize, mock_exists
    ):
        from bambu_cli.commands import cmd_upload

        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = False

        mock_ftp1 = MagicMock()
        mock_ftp1.storbinary.side_effect = OSError("Upload interrupted")
        mock_ftp1.size.return_value = 1024

        mock_ftp2 = MagicMock()
        mock_ftp2.size.return_value = 2048

        mock_get_ftp = MagicMock(
            side_effect=[
                MagicMock(__enter__=MagicMock(return_value=mock_ftp1)),
                MagicMock(__enter__=MagicMock(return_value=mock_ftp1)),
                MagicMock(__enter__=MagicMock(return_value=mock_ftp2)),
            ]
        )
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)

        mock_logger.info.assert_any_call("🔄 Resuming from 1KB...")
        mock_file().seek.assert_called_with(1024)
        mock_ftp2.storbinary.assert_called_with(
            "STOR /model/test.gcode", mock_file(), blocksize=1048576, rest=1024, callback=None
        )

    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("time.sleep")
    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    @patch("builtins.open", new_callable=mock_open)
    def test_cmd_upload_max_retries_exhausted(
        self, mock_file, mock_exit, mock_logger, mock_get_printer, mock_sleep, mock_getsize, mock_exists
    ):
        from bambu_cli.commands import cmd_upload

        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        args = MagicMock()
        args.file = "test.gcode"
        args.dry_run = False

        mock_ftp = MagicMock()
        mock_ftp.storbinary.side_effect = OSError("Upload always fails")
        mock_ftp.size.side_effect = OSError("Can't get size")

        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_upload(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        self.assertIn("Upload failed", str(cm.exception))

class TestBambuUploadRetry(unittest.TestCase):
    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.printer.logger")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("time.sleep")
    def test_cmd_upload_retry_success(
        self, mock_sleep, mock_logger, mock_file_open, mock_getsize, mock_exists, mock_printer_logger, mock_get_printer
    ):
        from bambu_cli.commands import cmd_upload

        args = MagicMock()
        args.file = "test.3mf"
        args.dry_run = False

        mock_exists.return_value = True
        mock_getsize.return_value = 2048

        mock_ftp = MagicMock()
        # Fail once, then succeed
        mock_ftp.storbinary.side_effect = [OSError("Timeout"), None]
        # First size() call is the mid-failure resume probe (mismatch keeps
        # uploaded_bytes at 0); second is the post-success verification.
        mock_ftp.size.side_effect = [0, 2048]
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_upload(args)

        self.assertEqual(mock_ftp.storbinary.call_count, 2)
        self.assertTrue(
            any("⚠️ Upload attempt 1 failed" in call[0][0] for call in mock_printer_logger.warning.call_args_list)
        )
        self.assertTrue(
            any("✅ Uploaded test.3mf to printer" in call[0][0] for call in mock_logger.info.call_args_list)
        )
