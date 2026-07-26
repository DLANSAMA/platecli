"""OrcaSlicer onboarding: broadened auto-detection and actionable hints.

When the configured OrcaSlicer path is wrong, the tool should point the user at
a real binary/profile it can actually find, rather than a generic "edit config".
"""

from unittest.mock import patch

from bambu_cli import config, setup_cmd
from tests.bambu_test_base import settings_ctx


def test_detect_orca_returns_none_when_nothing_exists():
    with patch.object(config, "_orca_binary_candidates", return_value=["/nope/a", "/nope/b"]):
        assert config.detect_orca_slicer() is None


def test_detect_orca_returns_first_existing():
    with (
        patch.object(config, "_orca_binary_candidates", return_value=["/nope/a", "/found/orca", "/other"]),
        patch("bambu_cli.config.os.path.exists", side_effect=lambda p: p == "/found/orca"),
    ):
        assert config.detect_orca_slicer() == "/found/orca"


def test_detect_profiles_returns_first_existing_dir():
    with (
        patch.object(config, "_profiles_dir_candidates", return_value=["/nope", "/found/BBL"]),
        patch("bambu_cli.config.os.path.isdir", side_effect=lambda p: p == "/found/BBL"),
    ):
        assert config.detect_profiles_dir() == "/found/BBL"


def test_linux_orca_candidates_include_path_flatpak_and_appimage():
    # Force the Linux branch regardless of the CI runner's OS.
    with (
        patch("bambu_cli.config.sys.platform", "linux"),
        patch(
            "bambu_cli.config.shutil.which",
            side_effect=lambda n: "/usr/bin/orca-slicer" if n == "orca-slicer" else None,
        ),
    ):
        candidates = config._orca_binary_candidates()
    assert "/usr/bin/orca-slicer" in candidates  # PATH lookup
    assert any(c and "com.orcaslicer.OrcaSlicer" in c for c in candidates)  # current Flathub id
    assert any(c and "io.github.softfever.OrcaSlicer" in c for c in candidates)  # legacy id kept
    assert any(c and c.endswith("OrcaSlicer.AppImage") for c in candidates)  # AppImage


def test_windows_orca_candidates_include_hyphenated_installer_name():
    """The current Windows installer ships `orca-slicer.exe`, not `OrcaSlicer.exe`.

    Probing only the CamelCase name made auto-detection miss every stock
    Windows install, so both names must be candidates under each install root.
    """
    env = {
        "PROGRAMFILES": r"C:\Program Files",
        "LOCALAPPDATA": r"C:\Users\u\AppData\Local",
        "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
    }
    with (
        patch("bambu_cli.config.sys.platform", "win32"),
        patch.dict("bambu_cli.config.os.environ", env, clear=False),
        patch("bambu_cli.config.shutil.which", return_value=None),
    ):
        candidates = config._orca_binary_candidates()

    program_files_entries = [c for c in candidates if c and c.startswith(r"C:\Program Files\OrcaSlicer")]
    assert any(c.endswith("orca-slicer.exe") for c in program_files_entries)
    assert any(c.endswith("OrcaSlicer.exe") for c in program_files_entries)
    # Hyphenated name is probed first so a stock install wins over a stale one.
    assert program_files_entries[0].endswith("orca-slicer.exe")
    # Every install root gets both names.
    for root in (r"C:\Program Files", r"C:\Users\u\AppData\Local", r"C:\Program Files (x86)"):
        assert any(c and c.startswith(root) and c.endswith("orca-slicer.exe") for c in candidates)


def test_windows_orca_candidates_fall_back_to_path_lookup():
    """A custom install location is still findable when it is on PATH."""
    with (
        patch("bambu_cli.config.sys.platform", "win32"),
        patch(
            "bambu_cli.config.shutil.which",
            side_effect=lambda n: r"D:\tools\orca-slicer.exe" if n == "orca-slicer" else None,
        ),
    ):
        candidates = config._orca_binary_candidates()
    assert r"D:\tools\orca-slicer.exe" in candidates


def test_linux_profiles_dir_candidates_include_flatpak():
    # Force the Linux branch regardless of the CI runner's OS.
    with patch("bambu_cli.config.sys.platform", "linux"):
        candidates = config._profiles_dir_candidates()
    assert any(c and "com.orcaslicer.OrcaSlicer" in c for c in candidates)  # current Flathub id
    assert any(c and "io.github.softfever.OrcaSlicer" in c for c in candidates)  # legacy id kept


def test_orca_install_hint_is_platform_specific():
    expected = {
        "win32": "winget install --id SoftFever.OrcaSlicer",
        "darwin": "brew install --cask orcaslicer",
        "linux": "flatpak install -y flathub com.orcaslicer.OrcaSlicer",
    }
    for platform_name, command in expected.items():
        with patch("bambu_cli.config.sys.platform", platform_name):
            hint = config.orca_install_hint()
        assert command in hint
        assert config.ORCA_RELEASES_URL in hint
        assert "plate setup" in hint


def test_orca_install_hint_falls_back_for_unknown_platform():
    with patch("bambu_cli.config.sys.platform", "freebsd14"):
        hint = config.orca_install_hint()
    assert config.ORCA_RELEASES_URL in hint


def test_missing_orca_message_tells_you_how_to_install_it():
    """With no OrcaSlicer anywhere, "edit your config" is not an actionable fix."""
    from bambu_cli import slicer

    with (
        patch("bambu_cli.config.detect_orca_slicer", return_value=None),
        patch("bambu_cli.config.sys.platform", "win32"),
    ):
        msg = slicer._slicer_executable_problem(r"C:\nope\orca-slicer.exe")
    assert msg is not None
    assert "winget install --id SoftFever.OrcaSlicer" in msg


def test_missing_orca_message_prefers_detected_path_over_install_hint():
    """When a real binary exists, point at it rather than telling them to install."""
    from bambu_cli import slicer

    with patch("bambu_cli.config.detect_orca_slicer", return_value="/found/orca-slicer"):
        msg = slicer._slicer_executable_problem("/bad/orca")
    assert msg is not None
    assert "winget" not in msg
    assert "flatpak" not in msg


def test_preflight_suggests_detected_orca_when_configured_path_bad():
    cfg = {"printer_ip": "1.2.3.4", "serial": "SN", "access_code": "x", "orca_slicer": "/bad/orca"}
    with (
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value="OrcaSlicer not found at /bad/orca"),
        patch("bambu_cli.config.detect_orca_slicer", return_value="/found/orca"),
        patch("os.path.isdir", return_value=True),
        patch("shutil.which", return_value=None),
    ):
        checks = setup_cmd.collect_preflight_checks()

    orca = [c for c in checks if c["name"] == "orca-slicer"][0]
    assert orca["status"] == "error"
    assert "/found/orca" in orca["message"]
    assert "orca_slicer" in orca["message"]


def test_preflight_suggests_detected_profiles_when_configured_dir_bad():
    cfg = {"printer_ip": "1.2.3.4", "serial": "SN", "access_code": "x", "profiles_dir": "/bad/profiles"}
    with (
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.slicer.cmd._slicer_executable_problem", return_value=None),
        patch("bambu_cli.config.detect_profiles_dir", return_value="/found/BBL"),
        patch("os.path.isdir", return_value=False),
        patch("shutil.which", return_value=None),
    ):
        checks = setup_cmd.collect_preflight_checks()

    profiles = [c for c in checks if c["name"] == "profiles-dir"][0]
    assert profiles["status"] == "error"
    assert "/found/BBL" in profiles["message"]
    assert "profiles_dir" in profiles["message"]


def test_preflight_unset_orca_path_is_actionable():
    cfg = {"printer_ip": "1.2.3.4", "serial": "SN", "access_code": "x"}
    with (
        settings_ctx(orca_slicer="", profiles_dir=""),
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.config.detect_orca_slicer", return_value=None),
        patch("bambu_cli.config.detect_profiles_dir", return_value=None),
        patch("shutil.which", return_value=None),
    ):
        checks = setup_cmd.collect_preflight_checks()
    orca = [c for c in checks if c["name"] == "orca-slicer"][0]
    assert orca["status"] == "error"
    assert "plate setup" in orca["message"]
    assert "orca_slicer" in orca["message"]
    assert not orca["message"].rstrip().endswith("at")
    assert "not found at" not in orca["message"]


def test_preflight_unset_profiles_dir_is_actionable():
    cfg = {"printer_ip": "1.2.3.4", "serial": "SN", "access_code": "x"}
    with (
        settings_ctx(orca_slicer="", profiles_dir=""),
        patch("bambu_cli.setup_cmd.preflight.load_config", return_value=cfg),
        patch("bambu_cli.setup_cmd.preflight._config_path", return_value="/tmp/config.json"),
        patch("bambu_cli.setup_cmd.preflight._display_path", side_effect=lambda p: p),
        patch("bambu_cli.config.detect_orca_slicer", return_value=None),
        patch("bambu_cli.config.detect_profiles_dir", return_value=None),
        patch("shutil.which", return_value=None),
    ):
        checks = setup_cmd.collect_preflight_checks()
    profiles = [c for c in checks if c["name"] == "profiles-dir"][0]
    assert profiles["status"] == "error"
    assert "plate setup" in profiles["message"]
    assert "profiles_dir" in profiles["message"]
    assert "not found at ." not in profiles["message"]
