"""Tests for honest, non-impersonating outbound HTTP identity.

platecli must identify itself to Printables as a first-party API client
(``platecli/<version> (+<project url>)``) and must not forge browser-only
``Origin``/``Referer`` headers. Arbitrary user-supplied download URLs keep a
browser-compatible User-Agent for CDN compatibility, with an honest,
attributable token appended.

Ground rules (docs/test-backlog.md): never touch the network.
"""

import json
import sys
from unittest.mock import MagicMock, patch

_mock_mqtt = MagicMock()
sys.modules.setdefault("paho", _mock_mqtt)
sys.modules.setdefault("paho.mqtt", _mock_mqtt)

from bambu_cli import constants, netsafety  # noqa: E402
from bambu_cli.printables import resolve_printables_url  # noqa: E402


def _clear_ua_caches():
    netsafety.platecli_user_agent.cache_clear()
    netsafety._default_user_agent.cache_clear()


def _gql_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    return resp


@patch("bambu_cli.printables.build_safe_opener")
@patch("bambu_cli.logging_utils._BACKEND")
def test_printables_gql_headers_are_honest_and_unforged(mock_logger, mock_safe_opener):
    mock_open = mock_safe_opener.return_value.open
    mock_open.return_value.__enter__.side_effect = [
        _gql_response(
            {
                "data": {
                    "print": {
                        "name": "3D BENCHY",
                        "stls": [{"name": "3dbenchy.stl", "fileSize": 1024, "id": "49068"}],
                        "gcodes": [],
                    }
                }
            }
        ),
        _gql_response(
            {
                "data": {
                    "getDownloadLink": {
                        "ok": True,
                        "output": {"link": "https://files.printables.com/media/x/3dbenchy.stl"},
                        "errors": None,
                    }
                }
            }
        ),
    ]

    url, name = resolve_printables_url("https://www.printables.com/model/3161-3d-benchy")
    assert url == "https://files.printables.com/media/x/3dbenchy.stl"
    assert name == "3dbenchy.stl"

    requests = [call.args[0] for call in mock_open.call_args_list]
    assert len(requests) == 2
    for req in requests:
        # urllib capitalizes header keys, so the lookup key must be "User-agent".
        ua = req.get_header("User-agent")
        assert ua == netsafety.platecli_user_agent()
        assert ua.startswith("platecli/")
        assert "github.com/DLANSAMA/platecli" in ua
        assert "Mozilla" not in ua
        assert "Chrome" not in ua
        # No forged browser-only headers.
        assert req.get_header("Origin") is None
        assert req.get_header("Referer") is None


def test_user_agent_for_url_policy():
    try:
        _clear_ua_caches()
        honest = netsafety.platecli_user_agent()

        for url in (
            "https://api.printables.com/graphql/",
            "https://www.printables.com/model/3161",
            "https://printables.com/model/3161",
            "https://files.printables.com/media/x/a.stl",
        ):
            assert netsafety.user_agent_for_url(url) == honest, url

        # Arbitrary hosts keep the browser-compatible UA.
        for url in (
            "https://cdn.example.com/a.stl",
            # Lookalikes must NOT inherit the first-party policy: exact-host
            # matching, not suffix matching.
            "https://printables.com.evil.example/x",
            "https://evil.printables.com.attacker.net/x",
            "not a url at all",
        ):
            ua = netsafety.user_agent_for_url(url)
            assert ua.startswith("Mozilla/5.0"), url
            assert "platecli/" in ua

        # Non-string input must not raise.
        assert netsafety._host_of(MagicMock()) == ""
    finally:
        _clear_ua_caches()


def test_platecli_user_agent_uses_version_source_of_truth():
    had = "VERSION" in constants.__dict__
    prev = constants.__dict__.get("VERSION")
    try:
        constants.VERSION = "9.9.9-test"
        _clear_ua_caches()
        assert netsafety.platecli_user_agent() == "platecli/9.9.9-test (+https://github.com/DLANSAMA/platecli)"
    finally:
        if had:
            constants.VERSION = prev
        else:
            constants.__dict__.pop("VERSION", None)
        _clear_ua_caches()
