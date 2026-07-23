"""Tests for lazy, cached VERSION resolution in bambu_cli.constants.

VERSION used to be resolved eagerly at module-import time via
importlib.metadata, which costs real work on every CLI invocation even
though only the --version path ever touches it. It's now resolved lazily
via a module-level __getattr__ (PEP 562) and cached in the module
namespace so repeated access doesn't re-resolve.
"""

import importlib
import subprocess

from bambu_cli import constants


def test_version_is_nonempty_string():
    assert isinstance(constants.VERSION, str)
    assert constants.VERSION


def test_version_resolution_is_cached_after_first_access():
    """Accessing VERSION twice must only call _resolve_version once."""
    calls = []
    original = constants._resolve_version

    def counting_resolve():
        calls.append(1)
        return original()

    # Reset any cached VERSION on the live module object so __getattr__
    # runs again, then patch the resolver to count invocations.
    had_cached = "VERSION" in constants.__dict__
    cached_value = constants.__dict__.pop("VERSION", None)
    original_resolve = constants._resolve_version
    try:
        constants._resolve_version = counting_resolve  # type: ignore[attr-defined]

        first = constants.VERSION
        second = constants.VERSION

        assert first == second
        assert len(calls) == 1, f"expected _resolve_version to run once, ran {len(calls)} times"
    finally:
        constants._resolve_version = original_resolve  # type: ignore[attr-defined]
        # Restore prior cache state so other tests see the original value.
        constants.__dict__.pop("VERSION", None)
        if had_cached:
            constants.__dict__["VERSION"] = cached_value
        else:
            # Re-resolve normally so later imports/tests still work.
            _ = constants.VERSION


def test_missing_attribute_still_raises_attribute_error():
    try:
        constants.__getattr__("NOT_A_REAL_ATTRIBUTE")
    except AttributeError as exc:
        assert "NOT_A_REAL_ATTRIBUTE" in str(exc)
    else:
        raise AssertionError("expected AttributeError for unknown attribute")


def test_cli_version_flag_prints_expected_version():
    result = subprocess.run(
        ["uv", "run", "plate", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"plate {constants.VERSION}"


def test_module_reload_reresolves_version_once():
    """Sanity check that a fresh module import still resolves VERSION lazily
    and consistently, independent of the caching test's internal state
    manipulation above."""
    mod = importlib.import_module("bambu_cli.constants")
    assert mod.VERSION == constants.VERSION
