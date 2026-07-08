"""Residual A+ coverage for measured lines still missed after Phase C."""

from __future__ import annotations

import base64
import hashlib
import ssl
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from bambu_cli import netsafety
from bambu_cli.errors import BambuError
from bambu_cli.netsafety import (
    MAX_DOWNLOAD_REDIRECT_HOPS,
    SafeHTTPHandler,
    SafeHTTPRedirectHandler,
    SafeHTTPSHandler,
)
from bambu_cli.protocols import mqtt as mqtt_mod
from bambu_cli.setup_cmd import preflight as preflight_mod


def test_redirect_hop_success_tracks_count():
    handler = SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    new_req = urllib.request.Request("https://example.com/next")
    with patch.object(
        urllib.request.HTTPRedirectHandler,
        "redirect_request",
        return_value=new_req,
    ) as super_redirect:
        out = handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/next")
    super_redirect.assert_called_once()
    assert out is new_req
    assert out._bambu_redirect_hops == 1


def test_redirect_hop_none_from_parent():
    handler = SafeHTTPRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    with patch.object(
        urllib.request.HTTPRedirectHandler,
        "redirect_request",
        return_value=None,
    ):
        assert handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/next") is None


def test_safe_http_handler_open_delegates():
    handler = SafeHTTPHandler()
    req = urllib.request.Request("http://example.com/")
    with patch.object(handler, "do_open", return_value="resp") as do_open:
        assert handler.http_open(req) == "resp"
    do_open.assert_called_once()
    assert do_open.call_args[0][0] is netsafety.SafeHTTPConnection


def test_safe_https_handler_open_with_context_attrs():
    handler = SafeHTTPSHandler()
    handler._context = object()
    handler._check_hostname = False
    req = urllib.request.Request("https://example.com/")
    with patch.object(handler, "do_open", return_value="resp") as do_open:
        assert handler.https_open(req) == "resp"
    kwargs = do_open.call_args[1]
    assert kwargs["context"] is handler._context
    assert kwargs["check_hostname"] is False


def test_require_mqtt_import_error_aborts():
    prev = mqtt_mod.mqtt
    try:
        mqtt_mod.mqtt = None
        with patch.dict("sys.modules", {"paho": None, "paho.mqtt": None, "paho.mqtt.client": None}):
            # Force import failure by shadowing import
            import builtins

            real_import = builtins.__import__

            def _boom(name, *a, **k):
                if name.startswith("paho"):
                    raise ImportError("no paho")
                return real_import(name, *a, **k)

            with patch("builtins.__import__", side_effect=_boom), pytest.raises(BambuError) as ei:
                mqtt_mod._require_mqtt()
            assert ei.value.exit_code != 0
    finally:
        mqtt_mod.mqtt = prev


def test_get_and_verify_cert_pem_success():
    der = b"cert-bytes-for-pin"
    expected = hashlib.sha256(der).hexdigest()
    raw = MagicMock()
    tls = MagicMock()
    tls.getpeercert.return_value = der
    tls.__enter__ = lambda s: tls
    tls.__exit__ = lambda *a: False
    raw_cm = MagicMock()
    raw_cm.__enter__ = lambda s: raw
    raw_cm.__exit__ = lambda *a: False
    ctx = MagicMock()
    ctx.wrap_socket.return_value = tls
    with patch("bambu_cli.protocols.mqtt.socket.create_connection", return_value=raw_cm), patch(
        "ssl.SSLContext", return_value=ctx
    ):
        pem = mqtt_mod._get_and_verify_cert_pem("host", 990, expected, timeout=1)
    assert "BEGIN CERTIFICATE" in pem
    assert base64.b64encode(der).decode("ascii")[:20] in pem.replace("\n", "")


def test_preflight_permission_win32_skips(monkeypatch):
    monkeypatch.setattr(preflight_mod.sys, "platform", "win32")
    assert preflight_mod._file_permission_check("/tmp/x", "access_code") is None


def test_preflight_module_available_exception(monkeypatch):
    def boom(_name):
        raise ValueError("bad")

    monkeypatch.setattr(preflight_mod.importlib.util, "find_spec", boom)
    # Falls through to sys.modules check
    assert preflight_mod._module_available("nonexistent_module_xyz") is False
