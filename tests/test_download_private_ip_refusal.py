"""A download blocked by the private-address guard is a refusal, not a network blip.

Before the 2026-09-22 fix, ``plate download http://localhost/...`` failed with
exit 2 ("Network error during download: <urlopen error <urlopen error Could
not connect ...>>"), which reads as a transient error worth retrying, and never
mentioned ``--allow-private-ips``. The branch meant to report it (exit 5,
``failed_step: validate``) matched on the text "Security Error", which only
ever appeared in a log line; the two unit tests covering it injected a
URLError carrying that text, so they passed while production never did. These
tests drive the real opener.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import threading
from unittest import mock

import pytest

from bambu_cli.constants import EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR
from bambu_cli.errors import BambuError
from tests.bambu_test_base import settings_ctx


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"solid cube\nendsolid cube\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _download(url, outdir, capsys=None):
    from bambu_cli.commands import cmd_download

    args = argparse.Namespace(url=url, output=str(outdir), name=None, max_download_mb=100, json=True)
    with pytest.raises(BambuError) as caught:
        cmd_download(args)
    return caught.value


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_private_target_is_refused_with_a_hint(local_server, tmp_path, host):
    with settings_ctx(allow_private_ips=False):
        exc = _download(f"http://{host}:{local_server}/model.stl", tmp_path)
    assert exc.exit_code == EXIT_COMMAND_ERROR
    assert exc.failed_step == "validate"
    assert "--allow-private-ips" in str(exc)
    assert "urlopen error" not in str(exc)
    assert list(tmp_path.iterdir()) == []


def test_same_target_downloads_with_the_override(local_server, tmp_path):
    from bambu_cli.commands import cmd_download

    args = argparse.Namespace(
        url=f"http://127.0.0.1:{local_server}/model.stl", output=str(tmp_path), name=None, max_download_mb=100
    )
    with settings_ctx(allow_private_ips=True):
        cmd_download(args)
    assert [p.name for p in tmp_path.iterdir()] == ["model.stl"]


def test_unreachable_public_host_is_still_a_network_error(tmp_path):
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
    with (
        settings_ctx(allow_private_ips=False),
        mock.patch("bambu_cli.netsafety.socket.getaddrinfo", return_value=public),
        mock.patch("bambu_cli.netsafety.socket.create_connection", side_effect=OSError("unreachable")),
    ):
        exc = _download("http://files.example.org/model.stl", tmp_path)
    assert exc.exit_code == EXIT_NETWORK_ERROR
    assert "--allow-private-ips" not in str(exc)


def test_json_envelope_names_the_refused_addresses(local_server, tmp_path, capsys):
    from bambu_cli.utils import write_error_envelope

    with settings_ctx(allow_private_ips=False):
        exc = _download(f"http://127.0.0.1:{local_server}/model.stl", tmp_path)
    write_error_envelope(
        argparse.Namespace(json=True), "download", exc.exit_code, str(exc), exc.failed_step, **exc.extra
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == EXIT_COMMAND_ERROR
    assert payload["blocked_addresses"] == ["127.0.0.1"]


def test_multicast_refusal_does_not_suggest_the_override(tmp_path):
    multicast = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("239.255.255.250", 80))]
    with (
        settings_ctx(allow_private_ips=True),
        mock.patch("bambu_cli.netsafety.socket.getaddrinfo", return_value=multicast),
    ):
        exc = _download("http://group.example.org/model.stl", tmp_path)
    assert exc.exit_code == EXIT_COMMAND_ERROR
    assert "--allow-private-ips" not in str(exc)
