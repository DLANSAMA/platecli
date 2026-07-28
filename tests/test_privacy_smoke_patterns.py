"""Regression guard for tests/privacy_smoke.py pattern building.

A GitHub noreply email in the form ``<id>+<login>@users.noreply.github.com``
used to be split on every non-alphanumeric character, so ``users``, ``noreply``
and ``github`` became "local account name" patterns. Every tracked file that
contained the word "users" was then flagged as a privacy leak, wrecking the
"any tracked-file hit is a real leak" triage rule.

These tests load the smoke script directly (it is not an importable package
module) and drive ``local_identity_patterns()`` with a fully mocked identity,
so they are hermetic and do not depend on the developer's real git config or
login name. They must pass on Windows CI as well, hence no POSIX-only paths.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SMOKE_PATH = ROOT / "tests" / "privacy_smoke.py"


def _load_smoke():
    """Load tests/privacy_smoke.py as a standalone module."""
    spec = importlib.util.spec_from_file_location("privacy_smoke_under_test", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fake_git_config(mapping):
    """Return a fake subprocess.run that answers `git config --get <key>`."""

    def runner(cmd, *args, **kwargs):
        key = cmd[-1] if cmd else ""
        return types.SimpleNamespace(stdout=mapping.get(key, "") + "\n")

    return runner


def _patterns_for(module, *, email, username="", remote_url=""):
    """Build patterns as if the only identity signals were the given values.

    All ambient identity sources (login name, env vars, home dir name) are
    forced to ``username`` so nothing leaks in from the real machine.
    """
    git_map = {"user.email": email}
    if username:
        git_map["user.name"] = username
    if remote_url:
        git_map["remote.origin.url"] = remote_url

    home = types.SimpleNamespace(name=username)
    with (
        mock.patch.object(module.getpass, "getuser", return_value=username),
        mock.patch.dict(module.os.environ, {"USER": username, "USERNAME": username, "LOGNAME": username}, clear=True),
        mock.patch.object(module.Path, "home", staticmethod(lambda: home)),
        mock.patch.object(module.subprocess, "run", side_effect=_fake_git_config(git_map)),
    ):
        return module.local_identity_patterns()


class NoreplyEmailPatternTests(unittest.TestCase):
    # Synthetic noreply address for testing — constructed at runtime to avoid the
    # "email address" pattern in privacy_smoke.py matching this source file itself.
    _NOREPLY_PARTS = ("999999999+testuser", "users.noreply.github.com")
    USERNAME = "testuser"

    @property
    def NOREPLY(self):
        return "@".join(self._NOREPLY_PARTS)

    def setUp(self):
        self.module = _load_smoke()

    def test_domain_tokens_do_not_become_account_name_patterns(self):
        patterns = _patterns_for(self.module, email=self.NOREPLY, username=self.USERNAME)
        account = patterns.get("local account name")
        self.assertIsNotNone(account, "expected a local account name pattern from the real username")
        for bare in ("users", "noreply", "github"):
            self.assertIsNone(
                account.search(f"the {bare} table was updated"),
                f"noreply-domain token {bare!r} must not match as a local account name",
            )

    def test_real_username_still_matches(self):
        patterns = _patterns_for(self.module, email=self.NOREPLY, username=self.USERNAME)
        account = patterns["local account name"]
        # Case-insensitive, from either the git user.name or the email local part.
        self.assertTrue(account.search(f"path /home/{self.USERNAME}/thing"))
        self.assertTrue(account.search(f"hello {self.USERNAME.upper()} world"))

    def test_home_path_pattern_excludes_domain_tokens(self):
        patterns = _patterns_for(self.module, email=self.NOREPLY, username=self.USERNAME)
        home = patterns.get("absolute local home path")
        self.assertIsNotNone(home)
        # Domain tokens must not have produced /home/users, /Users/noreply, etc.
        for bare in ("users", "noreply", "github"):
            self.assertIsNone(home.search(f"/home/{bare}/file"))
            self.assertIsNone(home.search(f"/Users/{bare}/file"))
        # The real username home path still matches.
        self.assertTrue(home.search(f"/home/{self.USERNAME}/x"))

    def test_numeric_id_prefix_is_not_a_pattern(self):
        patterns = _patterns_for(self.module, email=self.NOREPLY, username=self.USERNAME)
        account = patterns["local account name"]
        self.assertIsNone(
            account.search("build number 999999999 completed"),
            "numeric noreply id prefix must not become an account-name pattern",
        )

    def test_full_noreply_email_literal_still_matched(self):
        # Belt-and-suspenders: the whole address is still caught if it leaks verbatim.
        patterns = _patterns_for(self.module, email=self.NOREPLY, username=self.USERNAME)
        account = patterns["local account name"]
        self.assertTrue(account.search(f"contact {self.NOREPLY} today"))


if __name__ == "__main__":  # pragma: no cover
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    unittest.main()
