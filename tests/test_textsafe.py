"""display_safe strips terminal control and bidi characters from untrusted names."""

import pytest

from bambu_cli.textsafe import display_safe, has_unsafe_display_chars


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("\x1b]52;c;ZWNobw==\x07x.gcode", "?]52;c;ZWNobw==?x.gcode"),
        ("\x1b[2Jbenchy.3mf", "?[2Jbenchy.3mf"),
        ("a\nb\rc\td", "a?b?c?d"),
        ("csi\x9b31m", "csi?31m"),
        ("del\x7f", "del?"),
        ("abc\u202egcode.exe", "abc?gcode.exe"),
        ("line\u2028sep", "line?sep"),
    ],
)
def test_control_and_bidi_characters_are_replaced(raw, shown):
    assert has_unsafe_display_chars(raw)
    assert display_safe(raw) == shown


@pytest.mark.parametrize("text", ["Benchy [remix].stl", "модель.stl", "部件 v2.3mf", "", "plain"])
def test_printable_text_is_unchanged(text):
    assert not has_unsafe_display_chars(text)
    assert display_safe(text) == text


def test_none_and_non_strings():
    assert display_safe(None) == ""
    assert display_safe(42) == "42"
