"""Remote file commands: listing and deletion."""

from tests.bambu_test_base import *  # noqa: F401,F403


class TestBambuCmdFiles(unittest.TestCase):
    def _printer_with_ftp(self, mock_get_printer, mock_get_ftp):
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        return printer

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_files_success(self, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_files

        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        # Mock the context manager behavior
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.return_value = ["file1.3mf", "file2.3mf"]
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)

        cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with("/model/")
        # __exit__ should be called when using context manager
        mock_get_ftp.return_value.__exit__.assert_called_once()
        mock_logger.info.assert_any_call("📁 Files on printer:")
        mock_logger.info.assert_any_call("   file1.3mf")
        mock_logger.info.assert_any_call("   file2.3mf")

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_files_empty(self, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_files

        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.return_value = []
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)

        cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with("/model/")
        mock_get_ftp.return_value.__exit__.assert_called_once()
        mock_logger.info.assert_called_with("No files on printer.")

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_files_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_files

        args = MagicMock()
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.nlst.side_effect = OSError("FTP Error")
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(BambuError) as cm:
            cmd_files(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.nlst.assert_called_once_with("/model/")
        self.assertIn("Failed to list files", str(cm.exception))

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_files_get_ftp_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_files

        args = MagicMock()
        args.json = False
        mock_get_ftp = MagicMock(side_effect=OSError("Connection Failed"))
        self._printer_with_ftp(mock_get_printer, mock_get_ftp)
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(BambuError) as cm:
            cmd_files(args)

        mock_get_ftp.assert_called_once()
        self.assertIn("Failed to list files", str(cm.exception))


class TestBambuCmdDelete(unittest.TestCase):
    @patch("bambu_cli.printer.BambuPrinter.get_ftp_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_delete_no_confirm(self, mock_exit, mock_logger, mock_get_ftp):
        from bambu_cli.commands import cmd_delete

        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = False

        mock_exit.side_effect = SystemExit(5)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_delete(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)
        mock_get_ftp.assert_not_called()
        mock_logger.warning.assert_called_once_with(
            "⚠️  This will DELETE 'test.3mf' from the printer. Add --confirm to proceed."
        )

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_delete_success(self, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_delete

        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = True
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer

        cmd_delete(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.delete.assert_called_once_with("/model/test.3mf")
        mock_logger.info.assert_called_once_with("🗑️  Deleted test.3mf from printer")

    @patch("bambu_cli.printer.get_printer")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("sys.exit")
    def test_cmd_delete_error(self, mock_exit, mock_logger, mock_get_printer):
        from bambu_cli.commands import cmd_delete

        args = MagicMock()
        args.file = "test.3mf"
        args.confirm = True
        args.json = False
        mock_ftp = MagicMock()
        mock_get_ftp = MagicMock()
        mock_get_ftp.return_value.__enter__.return_value = mock_ftp
        mock_ftp.delete.side_effect = OSError("Delete Error")
        printer = _test_printer()
        printer.get_ftp_client = mock_get_ftp
        mock_get_printer.return_value = printer
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises(BambuError) as cm:
            cmd_delete(args)

        mock_get_ftp.assert_called_once()
        mock_ftp.delete.assert_called_once_with("/model/test.3mf")
        self.assertIn("Delete", str(cm.exception))
