"""Direct port-6000 TLS camera grab: port validation, pin enforcement, fail-closed paths.

Split out of the former 927-line test_camera_cmd.py."""

import hashlib

from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError

class TestCameraPortIsValid(unittest.TestCase):
    def test_rejects_out_of_range_container_port(self):
        """A container port above 65535 must be rejected: \\d{1,5} alone lets
        '99999' match the regex even though it is not a valid port number."""
        from bambu_cli.protocols.camera import _camera_port_is_valid

        self.assertFalse(_camera_port_is_valid("1985:99999"))
        self.assertFalse(_camera_port_is_valid("0"))
        self.assertFalse(_camera_port_is_valid("70000-70005"))

    def test_accepts_valid_container_ports(self):
        from bambu_cli.protocols.camera import _camera_port_is_valid

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
        from bambu_cli.protocols.camera import _grab_camera_frame_direct

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
        from bambu_cli.protocols.camera import _grab_camera_frame_direct

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

    def test_grab_camera_frame_direct_with_pin(self):
        from bambu_cli.protocols.camera import _grab_camera_frame_direct

        der = b"der_cert"
        good_fp = hashlib.sha256(der).hexdigest()
        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = der
        printer = _test_printer(ip="192.168.1.100", access_code="my_secret_code", cert_fingerprint=good_fp)

        res = _grab_camera_frame_direct(
            printer,
            create_connection=create_connection,
            ssl_context_factory=ssl_factory,
        )
        self.assertEqual(res, b"\xff\xd8\xff\xd9")

        mock_tls.getpeercert.assert_called_once_with(binary_form=True)

    def test_grab_camera_frame_direct_pin_mismatch(self):
        from bambu_cli.protocols.camera import _CameraPinMismatch, _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = b"der_cert"
        # Pin the SHA-256 of a *different* cert so the real compare fails.
        printer = _test_printer(
            ip="192.168.1.100",
            access_code="my_secret_code",
            cert_fingerprint=hashlib.sha256(b"a_different_cert").hexdigest(),
        )

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

    def test_grab_camera_frame_direct_pin_no_peer_cert(self):
        """A pin configured but no peer cert must fail closed (missing cert is not
        a Docker-fallback signal). Regression: the old code called .lower() on a
        None fingerprint and crashed with AttributeError instead of a clean pin
        failure that the snapshot command recognizes as fail-closed."""
        from bambu_cli.protocols.camera import _CameraPinMismatch, _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = None
        printer = _test_printer(
            ip="192.168.1.100",
            access_code="my_secret_code",
            cert_fingerprint=hashlib.sha256(b"der_cert").hexdigest(),
        )

        with self.assertRaises(_CameraPinMismatch):
            _grab_camera_frame_direct(
                printer,
                create_connection=create_connection,
                ssl_context_factory=ssl_factory,
            )
        mock_tls.sendall.assert_not_called()

    def test_grab_camera_frame_direct_malformed_nonascii_pin(self):
        """A malformed/non-ASCII pin must raise _CameraPinMismatch (fail closed),
        NOT a raw TypeError from hmac.compare_digest that would escape into the
        broad except-Exception fallback and silently use the unpinned Docker
        streamer."""
        from bambu_cli.protocols.camera import _CameraPinMismatch, _grab_camera_frame_direct

        create_connection, ssl_factory, mock_sock, mock_tls, mock_ctx = self._mock_net()
        mock_tls.getpeercert.return_value = b"der_cert"
        # 64 chars but with a Cyrillic 'а' — survives normalize, non-ASCII.
        printer = _test_printer(
            ip="192.168.1.100",
            access_code="my_secret_code",
            cert_fingerprint="а" + "b" * 63,
        )

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
        from bambu_cli.protocols.camera import _grab_camera_frame_direct

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
