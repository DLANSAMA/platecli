"""OrcaSlicer onboarding: broadened auto-detection and actionable hints.

When the configured OrcaSlicer path is wrong, the tool should point the user at
a real binary/profile it can actually find, rather than a generic "edit config".
"""

from unittest.mock import patch

from bambu_cli import config, setup_cmd


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
    assert any(c and "flatpak" in c for c in candidates)  # Flatpak export
    assert any(c and c.endswith("OrcaSlicer.AppImage") for c in candidates)  # AppImage


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
