"""CLI --json envelopes on doctor, snapshot, upload dry-run, wizard, and interrupt."""

from tests.bambu_test_base import *  # noqa: F401,F403

import argparse
import io
import json
import ssl
import sys
from contextlib import redirect_stdout, redirect_stderr

from bambu_cli.errors import BambuError


# ---------------------------------------------------------------------------
# doctor: A1/A1M report camera_snapshot=true
# ---------------------------------------------------------------------------


class TestDoctorCameraCapability(unittest.TestCase):
    @patch("bambu_cli.printer.BambuPrinter.get_version", return_value=[])
    @patch("bambu_cli.protocols.mqtt.probe_cert_fingerprint", return_value=None)
    @patch("bambu_cli.protocols.mqtt.get_status")
    @patch("bambu_cli.printer.BambuPrinter.get_ftp_client")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("builtins.open")
    def _run_doctor_json(
        self, model, mock_file_open, mock_logger, mock_get_ftp, mock_get_status, mock_probe, mock_get_version
    ):
        from bambu_cli.commands import cmd_doctor

        original_open = io.open

        def custom_open(file, *a, **k):
            if "config.json" in str(file):
                return original_open(file, *a, **k)
            return MagicMock()

        mock_file_open.side_effect = custom_open
        mock_get_status.return_value = {"hw_ver": model, "sw_ver": "01.05.00.00", "ams": {}}
        mock_get_ftp.return_value.__enter__.return_value = MagicMock()

        args = MagicMock()
        args.output = None
        args.json = True
        args.verbose = False

        buf = io.StringIO()
        with settings_ctx(printer_model=model), redirect_stdout(buf):
            cmd_doctor(args)
        return json.loads(buf.getvalue())

    def test_a1_reports_camera_snapshot_true(self):
        payload = self._run_doctor_json("A1")
        self.assertTrue(payload["capabilities"]["capabilities"]["camera_snapshot"])
        self.assertIn("A1", payload["capabilities"]["capabilities"]["camera_snapshot_note"])

    def test_a1m_reports_camera_snapshot_true(self):
        payload = self._run_doctor_json("A1M")
        self.assertTrue(payload["capabilities"]["capabilities"]["camera_snapshot"])

    def test_x1_still_reports_camera_snapshot_false(self):
        payload = self._run_doctor_json("X1C")
        self.assertFalse(payload["capabilities"]["capabilities"]["camera_snapshot"])


# ---------------------------------------------------------------------------
# camera: BambuError from ctx.printer() must propagate (not be swallowed)
# ---------------------------------------------------------------------------


class TestCameraErrorPaths(unittest.TestCase):
    def _snapshot_args(self, tmpdir):
        args = argparse.Namespace(
            output=os.path.join(tmpdir, "snap.jpg"),
            unique=False,
            json=False,
            verbose=False,
        )
        return args

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_bambu_error_from_printer_propagates(self, _mock_logger):
        from bambu_cli.commands import snapshot as camera

        tmpdir = tempfile.mkdtemp()
        args = self._snapshot_args(tmpdir)

        # ctx.printer() raises a domain abort (as load_access_code does on a
        # malformed access code). It must propagate, NOT be demoted to a debug
        # log and fall through to the Docker path.
        fake_ctx = MagicMock()
        fake_ctx.printer.side_effect = BambuError("bad access code", exit_code=13)

        with self.assertRaises(BambuError) as cm:
            camera.cmd_snapshot(args, ctx=fake_ctx)
        self.assertEqual(cm.exception.exit_code, 13)

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_direct_write_oserror_becomes_file_error(self, _mock_logger):
        from bambu_cli.commands import snapshot as camera
        from bambu_cli.constants import EXIT_FILE_ERROR

        tmpdir = tempfile.mkdtemp()
        args = self._snapshot_args(tmpdir)

        fake_printer = MagicMock()
        fake_ctx = MagicMock()
        fake_ctx.printer.return_value = fake_printer

        with patch.object(camera, "_write_snapshot_atomic", side_effect=OSError("No space left on device")):
            with self.assertRaises(BambuError) as cm:
                camera.cmd_snapshot(args, ctx=fake_ctx, grab_frame=lambda printer: b"\xff\xd8jpegbytes")
        # A file write failure exits EXIT_FILE_ERROR, not the generic command
        # error the uncaught OSError would have produced.
        self.assertEqual(cm.exception.exit_code, EXIT_FILE_ERROR)


# ---------------------------------------------------------------------------
# upload --dry-run surfaces the real failure reason
# ---------------------------------------------------------------------------


class TestUploadDryRunReason(unittest.TestCase):
    @patch("bambu_cli.printer.get_printer")
    def test_dry_run_surfaces_ssl_pin_reason(self, mock_get_printer):
        from bambu_cli.commands import files
        from bambu_cli.constants import EXIT_NETWORK_ERROR

        # get_ftp_client raises an SSLError (what a cert-pin mismatch produces).
        printer = MagicMock()
        printer.get_ftp_client.side_effect = ssl.SSLError("certificate fingerprint mismatch")
        mock_get_printer.return_value = printer

        # A real, tiny print-ready model file so the earlier validation/size read
        # succeeds and we reach the dry-run FTPS probe.
        tmpdir = tempfile.mkdtemp()
        fpath = os.path.join(tmpdir, "x.gcode.3mf")
        with open(fpath, "wb") as fh:
            fh.write(b"PK\x03\x04dummy")

        args = argparse.Namespace(file=fpath, dry_run=True, json=True, verbose=False)

        with settings_ctx(simulation=False):
            with self.assertRaises(BambuError) as cm:
                files.cmd_upload(args)
        self.assertEqual(cm.exception.exit_code, EXIT_NETWORK_ERROR)
        # The real cause (SSL/fingerprint) must appear — not the old fixed
        # "Could not reach printer." string with no detail.
        self.assertIn("fingerprint", str(cm.exception).lower())


# ---------------------------------------------------------------------------
# wizard use_ams: true only when detected AMS material was kept
# ---------------------------------------------------------------------------


class TestWizardUseAms(unittest.TestCase):
    def _run(self, detected, chosen, slot=0):
        from bambu_cli.interactive import session

        captured = {}

        def _fake_job(ns):
            captured["use_ams"] = getattr(ns, "use_ams", None)
            captured["ams_mapping"] = getattr(ns, "ams_mapping", None)

        state = session.WizardState()
        state.printable_path = "/tmp/model.gcode.3mf"
        state.detected_ams_material = detected
        state.detected_ams_slot = slot if detected is not None else None
        state.material = chosen

        deps = MagicMock()
        deps.get_steps.return_value.get_job.return_value = _fake_job

        args = argparse.Namespace(sim=False, verbose=False)
        session._run_print(args, deps, state, confirm=False)
        return captured

    def test_use_ams_true_with_mapping_when_detected_material_kept(self):
        # use_ams=True requires an explicit ams_mapping (the job pipeline refuses
        # to let firmware pick a default tray); the detected active slot supplies it.
        cap = self._run(detected="PLA", chosen="PLA", slot=2)
        self.assertTrue(cap["use_ams"])
        self.assertEqual(cap["ams_mapping"], "2")

    def test_use_ams_false_when_material_changed(self):
        self.assertFalse(self._run(detected="PLA", chosen="PETG")["use_ams"])

    def test_use_ams_false_when_no_detection(self):
        self.assertFalse(self._run(detected=None, chosen="PLA")["use_ams"])

    def test_use_ams_false_when_no_active_slot(self):
        # Detected material but no firm active slot (fallback-only detection):
        # stay on the conservative external-spool default.
        cap = self._run(detected="PLA", chosen="PLA", slot=None)
        self.assertFalse(cap["use_ams"])


# ---------------------------------------------------------------------------
# CLI: bad global typed flag under --json emits the JSON envelope, exit 5
# ---------------------------------------------------------------------------


class TestCliJsonEnvelopeBypass(unittest.TestCase):
    def _run_main(self, argv):
        from bambu_cli import cli

        out, err = io.StringIO(), io.StringIO()
        code = None
        old = sys.argv
        sys.argv = ["plate"] + argv
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.main()
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.argv = old
        return code, out.getvalue(), err.getvalue()

    def test_bad_global_flag_json_emits_envelope(self):
        from bambu_cli.constants import EXIT_COMMAND_ERROR

        code, out, _err = self._run_main(["--json", "--network-timeout", "abc", "status"])
        self.assertEqual(code, EXIT_COMMAND_ERROR)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["exit_code"], EXIT_COMMAND_ERROR)
        self.assertEqual(payload["failed_step"], "parse")


# ---------------------------------------------------------------------------
# CLI: Ctrl-C / EOF during --json run emits an interrupted envelope on stdout
# ---------------------------------------------------------------------------


class TestCliInterruptEnvelope(unittest.TestCase):
    def test_keyboardinterrupt_json_emits_envelope_stdout_stderr(self):
        from bambu_cli import cli
        from bambu_cli.constants import EXIT_COMMAND_ERROR
        import bambu_cli.utils as utils

        utils._JSON_EMITTED = False

        def _boom(_args):
            raise KeyboardInterrupt()

        out, err = io.StringIO(), io.StringIO()
        code = None
        old = sys.argv
        sys.argv = ["plate", "--json", "status"]
        try:
            with patch.object(cli, "_resolve_command", return_value=_boom), redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.main()
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.argv = old
            utils._JSON_EMITTED = False

        self.assertEqual(code, EXIT_COMMAND_ERROR)
        # stdout must be a single valid JSON envelope (the machine channel stays
        # parseable — before the fix it received a bare "Operation cancelled…").
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["failed_step"], "interrupted")
        # The human line goes to stderr.
        self.assertIn("cancelled", err.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
