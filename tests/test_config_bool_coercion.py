"""Boolean config keys are read strictly, and an unreadable value fails safe.

``bool("false")`` is ``True``. ``camera_allow_streamer`` -- the opt-in to the
Docker streamer that does not honour ``cert_fingerprint`` -- was read with
``bool()``, so a hand-edited ``"camera_allow_streamer": "false"`` switched the
unpinned path ON. ``insecure_tls`` already had a strict reader; every
security-relevant boolean now uses it, falling to the safe side when the value
is not a recognisable true/false.
"""

from __future__ import annotations

import pytest

from bambu_cli.commands.snapshot import streamer_is_allowed
from bambu_cli.context import Settings


@pytest.mark.parametrize("value", [False, "false", "False", "no", "0", "off", "", None, "maybe", 1, 0])
def test_camera_allow_streamer_is_off_unless_clearly_true(value):
    settings = Settings.from_config({"camera_allow_streamer": value})
    assert settings.camera_allow_streamer is False
    assert streamer_is_allowed(settings) is False


@pytest.mark.parametrize("value", [True, "true", "yes", "1", "on", " TRUE "])
def test_camera_allow_streamer_true_spellings(value):
    assert Settings.from_config({"camera_allow_streamer": value}).camera_allow_streamer is True


@pytest.mark.parametrize("value", [False, "false", "no", "0", "off", None])
def test_camera_direct_only_false_spellings_mean_false(value):
    assert Settings.from_config({"camera_direct_only": value}).camera_direct_only is False


@pytest.mark.parametrize("value", [True, "yes", "true", "maybe", 1])
def test_camera_direct_only_unreadable_values_stay_locked_down(value):
    # direct-only is the safer mode, so anything that is not a clear "false"
    # (or null, meaning unset) keeps it on -- the old contract for "yes" included.
    assert Settings.from_config({"camera_direct_only": value}).camera_direct_only is True


def test_camera_direct_only_defaults_off():
    assert Settings.from_config({}).camera_direct_only is False
