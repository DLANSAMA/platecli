from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError

import contextlib
import pathlib
import subprocess
import tempfile

from bambu_cli.constants import EXIT_FILE_ERROR


class TestMain(unittest.TestCase):
    def tearDown(self):
        # main() installs a process-wide RuntimeContext; reset it to the shared
        # baseline so it can't leak into later tests (the pytest suite does this
        # via a conftest fixture, but the CI `unittest` line needs it here).
        install_baseline_context()

    @patch("sys.argv", ["bambu.py", "status"])
    @patch("bambu_cli.commands.cmd_status")
    @patch("bambu_cli.cli.setup_logging")
    @patch("socket.getaddrinfo")
    def test_main_argparse_subcommand(self, mock_getaddrinfo, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu

        mock_getaddrinfo.return_value = []
        bambu_cli.cli.main()
        mock_cmd_status.assert_called_once()
        mock_setup_logging.assert_called_once_with(False)

    def test_global_json_before_subcommand_survives_override(self):
        # Regression: subcommands that re-declare --json (status/light/pause/resume)
        # must not clobber the global `--json` placed before the subcommand back to
        # False — the re-declared flag must share the global's argparse.SUPPRESS default.
        import bambu_cli.cli

        parser = bambu_cli.cli.build_parser()
        for argv in (
            ["--json", "status"],
            ["--json", "light", "on"],
            ["--json", "pause"],
            ["--json", "resume"],
            ["--json", "files"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(getattr(args, "json", False), argv)
        # …and absent when not requested (SUPPRESS default, no clobbering to False).
        self.assertFalse(hasattr(parser.parse_args(["status"]), "json"))

    @patch("sys.argv", ["bambu.py", "--sim", "status"])
    @patch("bambu_cli.commands.cmd_status")
    @patch("bambu_cli.cli.setup_logging")
    def test_main_sim_flag(self, mock_setup_logging, mock_cmd_status):
        import bambu_cli.bambu
        from bambu_cli import context

        bambu_cli.cli.main()
        self.assertTrue(context.get_current().simulation)

    @patch("sys.argv", ["bambu.py", "status"])
    @patch("bambu_cli.cli.logger")
    @patch("sys.exit")
    @patch("socket.getaddrinfo", side_effect=socket.gaierror)
    def test_main_invalid_printer_ip(self, mock_getaddrinfo, mock_exit, mock_logger):
        import bambu_cli.bambu
        from bambu_cli import context
        from bambu_cli.context import RuntimeContext, Settings

        mock_exit.side_effect = SystemExit(1)
        # Install a context with an unresolvable IP; mock load_config so main()
        # doesn't overwrite it from the on-disk config.
        context.set_current(RuntimeContext(settings=Settings(printer_ip="invalid_ip")))
        with patch("bambu_cli.config.load_config", return_value=None):
            with self.assertRaises((SystemExit, BambuError)) as cm:
                bambu_cli.cli.main()

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        mock_logger.error.assert_called_with("Invalid printer_ip or hostname in config: invalid_ip")

    @patch("sys.argv", ["bambu.py", "--json", "--sim", "upload", "x.stl"])
    @patch("bambu_cli.cli.setup_logging")
    def test_json_envelope_survives_logger_failure(self, _mock_setup_logging):
        """Verify the try/except fallback in logging_utils.safe_log_error is exercised.

        The critical patch target is bambu_cli.logging_utils.logger (the name binding
        actually used by safe_log_error), NOT bambu_cli.cli.logger (a different object
        that is never called on this path). Patching the wrong target made the mock
        never fire, leaving the fallback path at logging_utils.py:69-76 uncovered.
        """
        import bambu_cli.cli as cli
        import bambu_cli.utils as utils

        utils._JSON_EMITTED = False
        buf = io.StringIO()
        stderr_buf = io.StringIO()
        boom = BambuError("boom", exit_code=EXIT_FILE_ERROR, failed_step="validate")
        with patch("bambu_cli.commands.cmd_upload", side_effect=boom):
            with patch("bambu_cli.logging_utils.logger") as mock_logger:
                mock_logger.error.side_effect = RuntimeError("handler exploded")
                with contextlib.redirect_stdout(buf):
                    with contextlib.redirect_stderr(stderr_buf):
                        with self.assertRaises(SystemExit) as cm:
                            cli.main()
        # The mock must actually have been called — if it wasn't, the test is vacuous.
        mock_logger.error.assert_called()
        # Exit code preserved despite logging failure.
        self.assertEqual(cm.exception.code, EXIT_FILE_ERROR)
        # JSON envelope still emitted to stdout before the logging attempt.
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "boom")
        # Bare-stderr fallback path fired (logging_utils.py:73-74).
        self.assertIn("ERROR", stderr_buf.getvalue())


class TestBambuCmdSetup(unittest.TestCase):
    def setUp(self):
        self.isatty_patcher = patch("sys.stdin.isatty", return_value=True)
        self.isatty_patcher.start()
        self.input_patcher = patch("builtins.input", return_value="")
        self.input_mock_obj = self.input_patcher.start()

    def tearDown(self):
        self.isatty_patcher.stop()
        self.input_patcher.stop()

    @patch("getpass.getpass")
    @patch("bambu_cli.setup_cmd.wizard.logger")
    @patch("bambu_cli.setup_cmd.common.logger")
    @patch("bambu_cli.setup_cmd.wizard.socket.inet_ntoa")
    def test_cmd_setup_zeroconf_success(self, mock_ntoa, mock_common_logger, mock_logger, mock_getpass):
        # Asserts the observable outcome (config content, 0600, no temp litter) rather than
        # the os.open/mock_open write idiom, which is an implementation detail of the
        # secure writer and changed when writes became atomic.
        import sys
        import json
        import tempfile
        import os as _os
        from unittest.mock import patch as _patch

        mock_zc_module = MagicMock()
        mock_zc_class = MagicMock()
        mock_zc_instance = MagicMock()
        mock_zc_class.return_value = mock_zc_instance
        mock_browser_class = MagicMock()

        def fake_browser(zc, type_, listener):
            mock_info = MagicMock()
            mock_info.addresses = [b"\xc0\xa8\x01\x01"]
            mock_info.parsed_addresses.return_value = ["192.168.1.1"]
            mock_zc_instance.get_service_info.return_value = mock_info
            mock_ntoa.return_value = "192.168.1.1"
            listener.add_service(zc, type_, "BBLP-00112233._bblp._tcp.local.")
            return mock_browser_class

        mock_browser_class.side_effect = fake_browser

        mock_zc_module.Zeroconf = mock_zc_class
        mock_zc_module.ServiceBrowser = fake_browser

        sys.modules["zeroconf"] = mock_zc_module

        from bambu_cli.commands import cmd_setup

        mock_getpass.return_value = "12345678"
        self.input_mock_obj.side_effect = ["", "", "n"]

        tmpdir = tempfile.mkdtemp()
        cfg_path = _os.path.join(tmpdir, "config.json")
        try:
            with _patch("bambu_cli.setup_cmd.common._config_path", return_value=cfg_path):
                cmd_setup(MagicMock(json=False))

            from bambu_cli.utils import _display_path

            mock_common_logger.info.assert_any_call(f"\n✅ Config saved to {_display_path(cfg_path)}")
            with open(cfg_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["printer_ip"], "192.168.1.1")
            self.assertEqual(data["serial"], "00112233")
            self.assertEqual(data["access_code"], "12345678")
            if sys.platform != "win32":
                self.assertEqual(_os.stat(cfg_path).st_mode & 0o777, 0o600)
            self.assertEqual([n for n in _os.listdir(tmpdir) if n.endswith(".tmp")], [])
        finally:
            import shutil as _shutil

            _shutil.rmtree(tmpdir, ignore_errors=True)
            del sys.modules["zeroconf"]

    @patch("bambu_cli.setup_cmd.wizard.logger")
    @patch("sys.exit")
    def test_cmd_setup_zeroconf_no_devices(self, mock_exit, mock_logger):
        import sys

        mock_zc_module = MagicMock()
        mock_zc_class = MagicMock()
        mock_zc_instance = MagicMock()
        mock_zc_class.return_value = mock_zc_instance

        mock_zc_module.Zeroconf = mock_zc_class
        mock_zc_module.ServiceBrowser = MagicMock()

        sys.modules["zeroconf"] = mock_zc_module

        from bambu_cli.commands import cmd_setup

        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises((SystemExit, BambuError)):
            cmd_setup(MagicMock())

        mock_logger.error.assert_called_with("No printers found. Ensure printer is on the same network.")
        del sys.modules["zeroconf"]

    @patch("bambu_cli.setup_cmd.wizard.logger")
    @patch("sys.exit")
    def test_cmd_setup_zeroconf_not_installed(self, mock_exit, mock_logger):
        import sys

        if "zeroconf" in sys.modules:
            del sys.modules["zeroconf"]

        original_import = __import__

        def mocked_import(name, *args, **kwargs):
            if name == "zeroconf":
                raise ImportError("No module named 'zeroconf'")
            return original_import(name, *args, **kwargs)

        import builtins

        builtins.__import__ = mocked_import

        try:
            from bambu_cli.commands import cmd_setup

            mock_exit.side_effect = SystemExit(1)

            with self.assertRaises((SystemExit, BambuError)):
                cmd_setup(MagicMock())

            mock_logger.warning.assert_called_with(
                "⚠️  'zeroconf' package is not installed; network printer auto-discovery is disabled."
            )
        finally:
            builtins.__import__ = original_import


class TestBracketFilenames(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def _run(self, argv, tmpdir):
        env = {**os.environ, "COLUMNS": "200", "NO_COLOR": "1"}
        env["XDG_CONFIG_HOME"] = tmpdir
        env.pop("BAMBU_CLI", None)
        return subprocess.run(
            [sys.executable, "-m", "bambu_cli.bambu"] + argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            cwd=str(self.ROOT),
            env=env,
        )

    def test_plain_mode_bracket_filename_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("a[/b]c.stl", "part[v2].stl"):
                with self.subTest(name=name):
                    r = self._run(["--sim", "upload", name], td)
                    self.assertNotIn("MarkupError", r.stderr)
                    self.assertIn(name, r.stderr)
                    self.assertEqual(r.returncode, EXIT_FILE_ERROR, r.stderr)

    def test_json_mode_bracket_filename_emits_valid_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(["--json", "--sim", "upload", "a[/b]c.stl"], td)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["command"], "upload")
            self.assertEqual(payload["exit_code"], EXIT_FILE_ERROR)
            self.assertEqual(payload["error"], "File not found: a[/b]c.stl")
            self.assertEqual(payload["file"], "a[/b]c.stl")
            self.assertEqual(payload["failed_step"], "validate")
            self.assertEqual(r.returncode, EXIT_FILE_ERROR)


if __name__ == "__main__":
    unittest.main()
