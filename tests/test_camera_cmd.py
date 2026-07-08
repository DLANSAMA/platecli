from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestGrabCameraFrameDirect(unittest.TestCase):

    def _mock_net(self, mock_ssl, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls
        mock_ssl.return_value = mock_ctx
        mock_tls.recv.side_effect = [
            # first recv: size header (16 bytes)
            (4).to_bytes(4, "little") + b"\x00" * 12,
            # second recv: 4 bytes data representing valid JPEG
            b"\xff\xd8\xff\xd9",
        ]
        return mock_sock, mock_tls, mock_ctx

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_no_pin_fails_closed(self, mock_ssl, mock_create_conn):
        """Without a pinned fingerprint (and insecure_tls unset) the camera
        connection must fail closed before the access code is sent."""
        import ssl as ssl_mod
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code")

        with self.assertRaises(ssl_mod.SSLError):
            _grab_camera_frame_direct(printer)
        mock_tls.sendall.assert_not_called()

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_insecure(self, mock_ssl, mock_create_conn):
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", insecure_tls=True)

        res = _grab_camera_frame_direct(printer)
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_create_conn.assert_called_once_with(("192.168.1.100", 6000), timeout=12)
        mock_ctx.wrap_socket.assert_called_once_with(mock_sock, server_hostname="192.168.1.100")
        mock_tls.sendall.assert_called_once()
        mock_tls.getpeercert.assert_not_called()

    @patch('bambu_cli.config.fingerprint_sha256')
    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_with_pin(self, mock_ssl, mock_create_conn, mock_fp):
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "mock_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code",
                                cert_fingerprint="mock_fingerprint")

        res = _grab_camera_frame_direct(printer)
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_tls.getpeercert.assert_called_once_with(binary_form=True)
        mock_fp.assert_called_once_with(b"der_cert")

    @patch('bambu_cli.config.fingerprint_sha256')
    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_pin_mismatch(self, mock_ssl, mock_create_conn, mock_fp):
        import ssl as ssl_mod
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "wrong_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code",
                                cert_fingerprint="mock_fingerprint")

        with self.assertRaises(ssl_mod.SSLError):
            _grab_camera_frame_direct(printer)
        mock_tls.sendall.assert_not_called()

    @patch('bambu_cli.bambu.socket.create_connection')
    @patch('bambu_cli.bambu.ssl.create_default_context')
    def test_grab_camera_frame_direct_oversized_header_aborts(self, mock_ssl, mock_create_conn):
        """An implausibly large frame length means the stream is desynced; the
        grab must give up (return None) instead of reading the skipped body as
        the next frame header for the rest of the loop."""
        from bambu_cli.bambu import _grab_camera_frame_direct
        mock_sock, mock_tls, mock_ctx = self._mock_net(mock_ssl, mock_create_conn)
        mock_tls.recv.side_effect = [(99_000_000).to_bytes(4, "little") + b"\x00" * 12]
        printer = _test_printer(ip="192.168.1.100", access_code="c", insecure_tls=True)

        res = _grab_camera_frame_direct(printer)
        self.assertIsNone(res)
        # Only the one bogus header was read — no attempt to drain/parse a body.
        self.assertEqual(mock_tls.recv.call_count, 1)


class TestBambuCmdSnapshot(unittest.TestCase):

    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('bambu_cli.bambu.subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    def test_cmd_snapshot_non_localhost_url_blocked_before_any_request(
        self, mock_logger, mock_urlopen, mock_run, mock_grab, mock_which
    ):
        """A non-localhost camera_stream_url must be rejected before the
        readiness-polling loop issues any request (validate-then-use)."""
        from bambu_cli.bambu import cmd_snapshot

        args = MagicMock()
        args.output = "snap.jpg"
        with settings_ctx(camera_stream_url="http://evil.example.com:8080/frame.jpeg"):
            with self.assertRaises((SystemExit, BambuError)) as cm:
                cmd_snapshot(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)  # EXIT_CONFIG_ERROR
        mock_urlopen.assert_not_called()
        mock_run.assert_not_called()
        self.assertTrue(any(
            "must point to localhost" in c[0][0] for c in mock_logger.error.call_args_list
        ))

    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_invalid_output_path(self, mock_exit, mock_logger):
        from bambu_cli.bambu import cmd_snapshot
        args = MagicMock()
        args.output = "-invalid.jpg"
        mock_exit.side_effect = SystemExit(3)
        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_snapshot(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        mock_logger.error.assert_called_with("Invalid output path: -invalid.jpg")

    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('bambu_cli.bambu.subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_url_error(self, mock_exit, mock_logger, mock_urlopen, mock_run, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot
        import urllib.error

        args = MagicMock()
        args.output = "snap.jpg"

        # mock docker running
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen.side_effect = urllib.error.URLError("Network Error")
        mock_exit.side_effect = SystemExit(2)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_snapshot(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_called_with("Snapshot network error: <urlopen error Network Error>")

    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('bambu_cli.bambu.subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_snapshot_generic_error(self, mock_exit, mock_logger, mock_urlopen, mock_run, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot

        args = MagicMock()
        args.output = "snap.jpg"

        # mock docker running
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen.side_effect = Exception("Generic Error")
        mock_exit.side_effect = SystemExit(5)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_snapshot(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)
        mock_logger.error.assert_called_with("Snapshot failed: Generic Error")
    @patch('bambu_cli.bambu.shutil.which', return_value='/usr/bin/docker')
    @patch('bambu_cli.bambu._grab_camera_frame_direct', return_value=None)
    @patch('subprocess.run')
    @patch('urllib.request.urlopen')
    @patch('bambu_cli.bambu.logger')
    @patch('bambu_cli.bambu.load_access_code')
    def test_cmd_snapshot_start_container(self, mock_load_access, mock_logger, mock_urlopen, mock_subproc, mock_grab, mock_which):
        from bambu_cli.bambu import cmd_snapshot

        # 1st call: docker inspect (returns not running)
        # 2nd call: docker rm
        # 3rd call: docker run
        mock_subproc.side_effect = [
            MagicMock(returncode=1), # inspect fails
            MagicMock(returncode=0), # rm
            MagicMock(returncode=0)  # run
        ]

        mock_load_access.return_value = "MOCK_CODE"

        args = MagicMock()
        args.output = "snap.jpg"

        # Mock urlopen success
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"image data", b""]
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Need to mock os.path.exists for the temp file and the output file
        with patch('os.path.exists', return_value=True), \
             patch('os.fdopen', mock_open()), \
             patch('os.unlink'), \
             patch('time.sleep'), \
             patch('os.path.getsize', return_value=2048), \
             patch('bambu_cli.camera._write_snapshot_atomic'), \
             patch('builtins.open', new_callable=mock_open):
            cmd_snapshot(args)

        self.assertTrue(any("🔄 Starting camera streamer..." in call[0][0] for call in mock_logger.info.call_args_list))
        self.assertTrue(any("✅ Snapshot saved: snap.jpg (2KB)" in call[0][0] for call in mock_logger.info.call_args_list))

        # Verify docker run was called
        run_call = [call for call in mock_subproc.call_args_list if "run" in call[0][0]][0]
        self.assertIn("bambu_camera", run_call[0][0])
        self.assertIn("-e", run_call[0][0])
        self.assertIn("PRINTER_ACCESS_CODE", run_call[0][0])


if __name__ == '__main__':
    unittest.main()
