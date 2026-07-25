"""Printables request-header policy: honest UA, no forged browser headers."""

import json
import unittest
from unittest.mock import MagicMock, patch

from bambu_cli import netsafety


def _gql_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    return resp


class TestPrintablesHonestHeaders(unittest.TestCase):
    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_printables_gql_headers_are_honest_and_unforged(self, _mock_logger, mock_safe_opener):
        from bambu_cli.printables import resolve_printables_url

        mock_open = mock_safe_opener.return_value.open
        mock_open.return_value.__enter__.side_effect = [
            _gql_response(
                {
                    "data": {
                        "print": {
                            "name": "3D BENCHY",
                            "stls": [{"name": "3dbenchy.stl", "fileSize": 11285384, "id": "49068"}],
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

        resolve_printables_url("https://www.printables.com/model/3161-3d-benchy")

        self.assertGreaterEqual(mock_open.call_count, 2)
        for call in mock_open.call_args_list:
            req = call.args[0]
            # NOTE: urllib stores header keys via str.capitalize(), so the lookup
            # key must be "User-agent" — "User-Agent" returns None and would make
            # this assertion vacuously pass.
            ua = req.get_header("User-agent")
            self.assertEqual(ua, netsafety.platecli_user_agent())
            self.assertTrue(ua.startswith("platecli/"))
            self.assertIn("github.com/DLANSAMA/platecli", ua)
            self.assertNotIn("Mozilla", ua)
            self.assertNotIn("Chrome", ua)
            # Forged browser-only headers must be gone entirely.
            self.assertIsNone(req.get_header("Origin"))
            self.assertIsNone(req.get_header("Referer"))


class TestUserAgentPolicy(unittest.TestCase):
    def tearDown(self):
        netsafety.platecli_user_agent.cache_clear()
        netsafety._default_user_agent.cache_clear()

    def test_user_agent_for_url_policy(self):
        for host in ("api.printables.com", "www.printables.com", "printables.com", "files.printables.com"):
            with self.subTest(host=host):
                self.assertEqual(
                    netsafety.user_agent_for_url(f"https://{host}/x"),
                    netsafety.platecli_user_agent(),
                )

        # Arbitrary CDNs keep the browser-compatible UA.
        self.assertTrue(netsafety.user_agent_for_url("https://cdn.example.com/x").startswith("Mozilla/5.0"))

        # Exact-host matching, not suffix matching: lookalikes must NOT inherit
        # the first-party policy.
        for lookalike in (
            "https://printables.com.evil.example/x",
            "https://evil.printables.com.attacker.net/x",
        ):
            with self.subTest(lookalike=lookalike):
                self.assertTrue(netsafety.user_agent_for_url(lookalike).startswith("Mozilla/5.0"))

        # Garbage in, generic UA out — never an exception.
        self.assertTrue(netsafety.user_agent_for_url("not a url at all").startswith("Mozilla/5.0"))
        self.assertEqual(netsafety._host_of(MagicMock()), "")

    def test_platecli_user_agent_uses_version_source_of_truth(self):
        from bambu_cli import constants

        had = "VERSION" in constants.__dict__
        prev = constants.__dict__.get("VERSION")
        try:
            constants.VERSION = "9.9.9-test"
            netsafety.platecli_user_agent.cache_clear()
            netsafety._default_user_agent.cache_clear()
            self.assertEqual(
                netsafety.platecli_user_agent(),
                "platecli/9.9.9-test (+https://github.com/DLANSAMA/platecli)",
            )
        finally:
            if had:
                constants.VERSION = prev
            else:
                constants.__dict__.pop("VERSION", None)
            netsafety.platecli_user_agent.cache_clear()
            netsafety._default_user_agent.cache_clear()


if __name__ == "__main__":
    unittest.main()
