from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestCameraPortIsValid(unittest.TestCase):
    def test_rejects_out_of_range_container_port(self):
        """A container port above 65535 must be rejected: \\d{1,5} alone lets
        '99999' match the regex even though it is not a valid port number."""
        from bambu_cli.camera import _camera_port_is_valid

        self.assertFalse(_camera_port_is_valid("1985:99999"))
        self.assertFalse(_camera_port_is_valid("0"))
        self.assertFalse(_camera_port_is_valid("70000-70005"))

    def test_accepts_valid_container_ports(self):
        from bambu_cli.camera import _camera_port_is_valid

        self.assertTrue(_camera_port_is_valid("127.0.0.1:1985:1984"))
        self.assertTrue(_camera_port_is_valid("1984"))
        self.assertTrue(_camera_port_is_valid("1984/tcp"))
        self.assertTrue(_camera_port_is_valid("1984-1989/udp"))


class TestGrabCameraFrameDirect(unittest.TestCase):
    def _mock_net(self):
        mock_sock = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls
        mock_tls.recv.side_effect = [
            # first recv: size header (16 bytes)
            (4).to_bytes(4, "little") + b"\x00" * 12,
            # second recv: 4 bytes data representing valid JPEG
            b"\xff\xd8\xff\xd9",
        ]
        create_connection = MagicMock(return_value=mock_sock)
        ssl_context_factory = MagicMock(return_value=mock_ctx)
        return create_connection, ssl_context_factory, mock_sock, mock_tls, mock_ctx

    def test_grab_camera_frame_direct_no_pin_fails_closed(self):
        """Without a pinned fingerprint (and insecure_tls unset) the camera
        connection must fail closed before the access code is sent."""
        import ssl as ssl_mod
        from bambu_cli.camera import _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code")

        with self.assertRaises(ssl_mod.SSLError):
            _grab_camera_frame_direct(
                printer,
                create_connection=create_connection,
                ssl_context_factory=ssl_factory,
            )
        mock_tls.sendall.assert_not_called()

    def test_grab_camera_frame_direct_insecure(self):
        from bambu_cli.camera import _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", insecure_tls=True)

        res = _grab_camera_frame_direct(
            printer,
            create_connection=create_connection,
            ssl_context_factory=ssl_factory,
        )
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        create_connection.assert_called_once_with(("192.168.1.100", 6000), timeout=12)
        mock_ctx.wrap_socket.assert_called_once_with(mock_sock, server_hostname="192.168.1.100")
        mock_tls.sendall.assert_called_once()
        mock_tls.getpeercert.assert_not_called()
        # wrap_socket detaches the fd into the SSLSocket, so the wrapped object
        # (not the bare socket) must be closed or the fd leaks.
        mock_tls.close.assert_called_once()

    @patch("bambu_cli.config.fingerprint_sha256")
    def test_grab_camera_frame_direct_with_pin(self, mock_fp):
        from bambu_cli.camera import _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "mock_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", cert_fingerprint="mock_fingerprint")

        res = _grab_camera_frame_direct(
            printer,
            create_connection=create_connection,
            ssl_context_factory=ssl_factory,
        )
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_tls.getpeercert.assert_called_once_with(binary_form=True)
        mock_fp.assert_called_once_with(b"der_cert")

    @patch("bambu_cli.config.fingerprint_sha256")
    def test_grab_camera_frame_direct_pin_mismatch(self, mock_fp):
        from bambu_cli.camera import _CameraPinMismatch, _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = b"der_cert"
        mock_fp.return_value = "wrong_fingerprint"
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", cert_fingerprint="mock_fingerprint")

        # A mismatching pin raises a dedicated security error (not a generic
        # SSLError) so the snapshot command can fail closed instead of falling
        # back to the Docker streamer, which would ignore the pin.
        with self.assertRaises(_CameraPinMismatch):
            _grab_camera_frame_direct(
                printer,
                create_connection=create_connection,
                ssl_context_factory=ssl_factory,
            )
        mock_tls.sendall.assert_not_called()

    def test_grab_camera_frame_direct_oversized_header_aborts(self):
        """An implausibly large frame length means the stream is desynced; the
        grab must give up (return None) instead of reading the skipped body as
        the next frame header for the rest of the loop."""
        from bambu_cli.camera import _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.recv.side_effect = [(99_000_000).to_bytes(4, "little") + b"\x00" * 12]
        printer = _test_printer(ip="192.168.1.100", access_code="c", insecure_tls=True)

        res = _grab_camera_frame_direct(
            printer,
            create_connection=create_connection,
            ssl_context_factory=ssl_factory,
        )
        self.assertIsNone(res)
        # Only the one bogus header was read — no attempt to drain/parse a body.
        self.assertEqual(mock_tls.recv.call_count, 1)


class TestBambuCmdSnapshot(unittest.TestCase):
    def _logger_patch(self):
        return patch("bambu_cli.logging_utils.logger", new=MagicMock())

    def test_cmd_snapshot_non_localhost_url_blocked_before_any_request(self):
        """A non-localhost camera_stream_url must be rejected before the
        readiness-polling loop issues any request (validate-then-use)."""
        from bambu_cli.commands import cmd_snapshot

        mock_run = MagicMock()
        mock_urlopen = MagicMock()
        mock_logger = MagicMock()
        args = MagicMock()
        args.output = "snap.jpg"
        with (
            patch("bambu_cli.camera.logger", mock_logger),
            settings_ctx(camera_stream_url="http://evil.example.com:8080/frame.jpeg"),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
            )
        self.assertEqual(
            getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1
        )  # EXIT_CONFIG_ERROR
        mock_urlopen.assert_not_called()
        mock_run.assert_not_called()
        self.assertTrue(any("must point to localhost" in c[0][0] for c in mock_logger.error.call_args_list))

    def test_cmd_snapshot_invalid_output_path(self):
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        args = MagicMock()
        args.output = "-invalid.jpg"
        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("sys.exit", side_effect=SystemExit(3)),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(args)

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        mock_logger.error.assert_called_with("Invalid output path: -invalid.jpg")

    def test_cmd_snapshot_url_error(self):
        from bambu_cli.commands import cmd_snapshot
        import urllib.error

        mock_logger = MagicMock()
        mock_run = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen = MagicMock(side_effect=urllib.error.URLError("Network Error"))
        args = MagicMock()
        args.output = "snap.jpg"

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("sys.exit", side_effect=SystemExit(2)),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
            )

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        mock_logger.error.assert_called_with("Snapshot network error: <urlopen error Network Error>")

    def test_cmd_snapshot_generic_error(self):
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_run = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "true"
        mock_run.return_value = mock_run_result

        mock_urlopen = MagicMock(side_effect=Exception("Generic Error"))
        args = MagicMock()
        args.output = "snap.jpg"

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("sys.exit", side_effect=SystemExit(5)),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
            )

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)
        mock_logger.error.assert_called_with("Snapshot failed: Generic Error")

    def test_cmd_snapshot_pin_mismatch_fails_closed(self):
        """A pinned-cert mismatch during the direct grab must abort (exit 2)
        without ever touching the Docker streamer fallback — otherwise the
        streamer would connect to the printer ignoring the pin (silent
        downgrade of an explicit security control)."""
        from bambu_cli.camera import _CameraPinMismatch
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_run = MagicMock()
        mock_urlopen = MagicMock()
        args = MagicMock()
        args.output = "snap.jpg"

        def _grab(printer):
            raise _CameraPinMismatch("Certificate fingerprint mismatch: expected aa, got bb")

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                grab_frame=_grab,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
            )

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        # Never fell back to the streamer.
        mock_run.assert_not_called()
        mock_urlopen.assert_not_called()
        self.assertTrue(any("does not match pinned fingerprint" in c[0][0] for c in mock_logger.error.call_args_list))

    def test_cmd_snapshot_ssl_error_with_pin_configured_fails_closed(self):
        """When a cert pin IS configured, an ssl.SSLError from the direct TLS
        grab (e.g. a handshake failure caused by an active MITM interfering
        with the port-6000 connection) must abort instead of silently falling
        back to the unpinned Docker streamer -- otherwise an attacker could
        defeat the pin simply by breaking the handshake rather than presenting
        a mismatched certificate."""
        import ssl as ssl_mod

        from bambu_cli.commands import cmd_snapshot
        from bambu_cli.context import RuntimeContext, Settings

        mock_logger = MagicMock()
        mock_run = MagicMock()
        mock_urlopen = MagicMock()
        args = MagicMock()
        args.output = "snap.jpg"

        def _grab(printer):
            raise ssl_mod.SSLError("handshake failure")

        ctx = RuntimeContext(settings=Settings(cert_fingerprint="aa" * 32, insecure_tls=False))

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                ctx=ctx,
                grab_frame=_grab,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
            )

        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        # Never fell back to the streamer.
        mock_run.assert_not_called()
        mock_urlopen.assert_not_called()
        self.assertTrue(
            any("TLS error with a cert pin configured" in c[0][0] for c in mock_logger.error.call_args_list)
        )

    def test_cmd_snapshot_ssl_error_without_pin_falls_back_to_docker(self):
        """The same ssl.SSLError, but with no pin configured, must still fall
        through to the Docker streamer -- this preserves the existing
        no-pin-configured fallback behavior."""
        import ssl as ssl_mod

        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_subproc = MagicMock(
            side_effect=[
                MagicMock(returncode=1),  # inspect fails
                MagicMock(returncode=0),  # rm
                MagicMock(returncode=0),  # run
            ]
        )
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"image data", b""]
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_sleep = MagicMock()
        mock_load_access = MagicMock(return_value="MOCK_CODE")

        def _grab(printer):
            raise ssl_mod.SSLError("no pin configured")

        args = MagicMock()
        args.output = "snap.jpg"

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            settings_ctx(cert_fingerprint=None, insecure_tls=False),
            patch("os.path.exists", return_value=True),
            patch("os.fdopen", mock_open()),
            patch("os.unlink"),
            patch("os.path.getsize", return_value=2048),
            patch("bambu_cli.camera._write_snapshot_atomic"),
            patch("builtins.open", new_callable=mock_open),
        ):
            cmd_snapshot(
                args,
                grab_frame=_grab,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_subproc,
                access_code_loader=mock_load_access,
                urlopen=mock_urlopen,
                sleep=mock_sleep,
            )

        mock_subproc.assert_called()

    def test_cmd_snapshot_start_container(self):
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        # 1st call: docker inspect (returns not running)
        # 2nd call: docker rm
        # 3rd call: docker run
        mock_subproc = MagicMock(
            side_effect=[
                MagicMock(returncode=1),  # inspect fails
                MagicMock(returncode=0),  # rm
                MagicMock(returncode=0),  # run
            ]
        )
        mock_load_access = MagicMock(return_value="MOCK_CODE")

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"image data", b""]
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_sleep = MagicMock()

        args = MagicMock()
        args.output = "snap.jpg"

        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("os.path.exists", return_value=True),
            patch("os.fdopen", mock_open()),
            patch("os.unlink"),
            patch("os.path.getsize", return_value=2048),
            patch("bambu_cli.camera._write_snapshot_atomic"),
            patch("builtins.open", new_callable=mock_open),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_subproc,
                access_code_loader=mock_load_access,
                urlopen=mock_urlopen,
                sleep=mock_sleep,
            )

        self.assertTrue(any("🔄 Starting camera streamer..." in call[0][0] for call in mock_logger.info.call_args_list))
        self.assertTrue(
            any("✅ Snapshot saved: snap.jpg (2KB)" in call[0][0] for call in mock_logger.info.call_args_list)
        )

        # Verify docker run was called
        run_call = [call for call in mock_subproc.call_args_list if "run" in call[0][0]][0]
        self.assertIn("bambu_camera", run_call[0][0])
        self.assertIn("-e", run_call[0][0])
        self.assertIn("PRINTER_ACCESS_CODE", run_call[0][0])

    def test_cmd_snapshot_invalid_camera_port_aborts(self):
        """A malformed camera_port must be rejected with a clear config error
        before any docker command runs."""
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_run = MagicMock()
        args = MagicMock()
        args.output = "snap.jpg"
        with (
            patch("bambu_cli.camera.logger", mock_logger),
            settings_ctx(camera_port="not-a-port"),
            self.assertRaises((SystemExit, BambuError)) as cm,
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=MagicMock(),
            )
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)
        mock_run.assert_not_called()
        self.assertTrue(any("Invalid camera_port" in c[0][0] for c in mock_logger.error.call_args_list))

    def test_cmd_snapshot_non_loopback_bind_warns(self):
        """A camera_port that publishes on a non-loopback interface warns the
        user that the printer camera is exposed to the network."""
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="true"))  # already running
        mock_response = MagicMock()
        mock_response.read.return_value = b"img"
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        args = MagicMock()
        args.output = "snap.jpg"
        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("bambu_cli.camera._write_snapshot_atomic"),
            patch("os.path.getsize", return_value=1024),
            settings_ctx(camera_port="0.0.0.0:1985:1984"),
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
                sleep=MagicMock(),
            )
        self.assertTrue(any("non-loopback" in c[0][0] for c in mock_logger.warning.call_args_list))

    def test_cmd_snapshot_running_container_exposed_warns(self):
        """When the configured port is loopback but a *pre-existing* container is
        still bound to a non-loopback interface, warn to recreate it."""
        from bambu_cli.commands import cmd_snapshot

        mock_logger = MagicMock()
        mock_run = MagicMock(
            side_effect=[
                MagicMock(returncode=0, stdout="true"),  # running check
                MagicMock(  # NetworkSettings.Ports inspect
                    returncode=0,
                    stdout='{"1984/tcp":[{"HostIp":"0.0.0.0","HostPort":"1985"}]}',
                ),
            ]
        )
        mock_response = MagicMock()
        mock_response.read.return_value = b"img"
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        args = MagicMock()
        args.output = "snap.jpg"
        with (
            patch("bambu_cli.camera.logger", mock_logger),
            patch("bambu_cli.camera._write_snapshot_atomic"),
            patch("os.path.getsize", return_value=1024),
            settings_ctx(camera_port="127.0.0.1:1985:1984"),  # config is safe
        ):
            cmd_snapshot(
                args,
                grab_frame=lambda printer: None,
                which=lambda name: "/usr/bin/docker",
                subprocess_run=mock_run,
                urlopen=mock_urlopen,
                sleep=MagicMock(),
            )
        self.assertTrue(any("docker rm -f" in c[0][0] for c in mock_logger.warning.call_args_list))


if __name__ == "__main__":
    unittest.main()
