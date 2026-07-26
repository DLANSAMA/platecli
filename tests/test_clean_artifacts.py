"""Guards for scripts/clean_artifacts.py.

The script used to delete `.venv` whenever GITHUB_ACTIONS was unset, with
`ignore_errors=True`. On Windows that left a half-removed virtualenv (any
locked file, e.g. a running python.exe) and every later `uv run` failed with
"No pyvenv.cfg file". These tests pin the safe behaviour: opt-in only, and
loud when removal does not actually happen.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clean_artifacts.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import clean_artifacts  # noqa: E402


def _populate(root):
    """Create one of every artifact the script knows about, plus a .venv."""
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / "pkg" / "__pycache__" / "mod.cpython-39.pyc").write_text("x")
    (root / "pkg" / "stray.pyc").write_text("x")
    (root / "pkg" / "keep.py").write_text("x")
    for name in (".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "wheelhouse", "platecli.egg-info"):
        (root / name).mkdir()
        (root / name / "junk.txt").write_text("x")
    (root / ".bambu-download-123.zip").write_text("x")
    venv = root / ".venv"
    (venv / "Lib" / "site-packages" / "__pycache__").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr")


def _run(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


class TestCleanArtifactsVenvSafety(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _populate(self.root)

    def test_default_run_keeps_venv_and_clears_artifacts(self):
        result = _run(self.root)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".venv" / "pyvenv.cfg").is_file(), ".venv must survive a default run")
        self.assertFalse((self.root / "pkg" / "__pycache__").exists())
        self.assertFalse((self.root / "pkg" / "stray.pyc").exists())
        self.assertFalse((self.root / "build").exists())
        self.assertFalse((self.root / "platecli.egg-info").exists())
        self.assertFalse((self.root / ".bambu-download-123.zip").exists())
        self.assertTrue((self.root / "pkg" / "keep.py").is_file())

    def test_default_run_does_not_descend_into_venv(self):
        _run(self.root)

        self.assertTrue((self.root / ".venv" / "Lib" / "site-packages" / "__pycache__").is_dir())

    def test_venv_flag_removes_venv_non_interactively(self):
        result = _run(self.root, "--venv")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ".venv").exists())

    def test_declining_the_prompt_keeps_venv_but_still_cleans(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--venv"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            input="n\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".venv" / "pyvenv.cfg").is_file())
        self.assertIn("keeping .venv", result.stdout)
        self.assertFalse((self.root / "build").exists(), "other artifacts are still cleaned")

    def test_accepting_the_prompt_removes_venv(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--venv"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            input="y\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ".venv").exists())

    def test_all_flag_removes_venv(self):
        self.assertEqual(_run(self.root, "--all", "--yes").returncode, 0)
        self.assertFalse((self.root / ".venv").exists())

    def test_dry_run_removes_nothing_and_lists_targets(self):
        result = _run(self.root, "--dry-run", "--venv")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build", result.stdout)
        self.assertIn(".venv", result.stdout)
        self.assertTrue((self.root / "build").is_dir())
        self.assertTrue((self.root / ".venv").is_dir())
        self.assertTrue((self.root / "pkg" / "__pycache__").is_dir())

    def test_run_prints_what_it_removes(self):
        result = _run(self.root)

        self.assertIn("clean_artifacts: removing", result.stdout)
        self.assertIn("build", result.stdout)


class TestCleanArtifactsFailureReporting(unittest.TestCase):
    """A surviving directory must be reported, not silently ignored."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_remove_tree_reports_survivor(self):
        target = self.root / ".venv"
        target.mkdir()

        with patch.object(clean_artifacts.shutil, "rmtree", lambda *a, **kw: None):
            error = clean_artifacts.remove_tree(target)

        self.assertIsNotNone(error)
        self.assertIn("still present", error)

    def test_remove_tree_reports_oserror(self):
        target = self.root / "build"
        target.mkdir()

        def boom(*args, **kwargs):
            raise OSError(13, "locked")

        with patch.object(clean_artifacts.shutil, "rmtree", boom):
            error = clean_artifacts.remove_tree(target)

        self.assertIsNotNone(error)
        self.assertIn("locked", error)

    def test_main_exits_non_zero_and_explains_venv_recovery(self):
        import os
        from io import StringIO

        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "pyvenv.cfg").write_text("home = /usr")

        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

        with patch.object(clean_artifacts, "remove_tree", return_value="still present after removal"):
            with patch("sys.stderr", new=StringIO()) as err:
                code = clean_artifacts.main(["--venv", "--yes"])

        self.assertEqual(code, 1)
        self.assertIn(".venv may now be incomplete", err.getvalue())
        self.assertIn("uv sync", err.getvalue())

    def test_remove_tree_succeeds_normally(self):
        target = self.root / "dist"
        (target / "nested").mkdir(parents=True)
        (target / "nested" / "f.txt").write_text("x")

        self.assertIsNone(clean_artifacts.remove_tree(target))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
