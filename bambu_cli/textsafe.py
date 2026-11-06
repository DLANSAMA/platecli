"""Make untrusted text safe to show in a terminal.

Names that come from outside the process (the printer's FTP listing, MQTT
report fields such as ``gcode_file`` or an AMS ``tray_type``, mDNS instance
names, Printables model names) can carry terminal control sequences. Rich and
Textual stop *markup* injection when text is wrapped in ``Text()``, but they
pass ESC through, so ``\\x1b]52;c;...`` can write the user's clipboard and
``\\x1b[2J`` can clear the screen. Bidi overrides can make ``evil.gcode`` read
as something else on a confirmation line.

Rank 10: stdlib only, no knowledge of config, transport or commands.
"""

__all__ = ["display_safe", "has_unsafe_display_chars"]

# Bidi embedding/override/isolate controls, the implicit marks, and the Unicode
# line and paragraph separators (which some terminals treat as newlines).
_UNSAFE_FORMAT_CHARS = frozenset(
    chr(code) for code in (0x200E, 0x200F, 0x2028, 0x2029, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)


def _is_unsafe(char: str) -> bool:
    code = ord(char)
    # C0 controls (including ESC, CR, LF, TAB), DEL, and C1 controls (0x9b is
    # an 8-bit CSI that some terminals honour).
    return code < 0x20 or 0x7F <= code <= 0x9F or char in _UNSAFE_FORMAT_CHARS


def has_unsafe_display_chars(value) -> bool:
    """True if *value* holds a control or bidi-format character."""
    return any(_is_unsafe(c) for c in str(value if value is not None else ""))


def display_safe(value, replacement: str = "?") -> str:
    """Return *value* as text with every control or bidi-format character replaced.

    ``None`` becomes the empty string. Printable text, including non-ASCII
    letters, is returned unchanged.
    """
    text = str(value if value is not None else "")
    if not has_unsafe_display_chars(text):
        return text
    return "".join(replacement if _is_unsafe(c) else c for c in text)
