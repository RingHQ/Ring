"""Translate Qt key codes to the evdev key names used in the config file."""

# Values of Qt::Key, spelled out so this module works without importing Qt.
_QT_KEY_F1 = 0x01000030
_QT_KEY_F24 = 0x01000047

_NAMED = {
    0x20: "KEY_SPACE",
    0x2C: "KEY_COMMA",
    0x2D: "KEY_MINUS",
    0x2E: "KEY_DOT",
    0x2F: "KEY_SLASH",
    0x3B: "KEY_SEMICOLON",
    0x3D: "KEY_EQUAL",
    0x01000000: "KEY_ESC",
    0x01000001: "KEY_TAB",
    0x01000003: "KEY_BACKSPACE",
    0x01000004: "KEY_ENTER",
    0x01000005: "KEY_KPENTER",
    0x01000006: "KEY_INSERT",
    0x01000007: "KEY_DELETE",
    0x01000010: "KEY_HOME",
    0x01000011: "KEY_END",
    0x01000012: "KEY_LEFT",
    0x01000013: "KEY_UP",
    0x01000014: "KEY_RIGHT",
    0x01000015: "KEY_DOWN",
    0x01000016: "KEY_PAGEUP",
    0x01000017: "KEY_PAGEDOWN",
}


# evdev codes of the keys that can serve as a single-key trigger: modifiers
# and other keys that type nothing.
EVDEV_CODES = {
    "KEY_LEFTCTRL": 29,
    "KEY_LEFTSHIFT": 42,
    "KEY_RIGHTSHIFT": 54,
    "KEY_LEFTALT": 56,
    "KEY_CAPSLOCK": 58,
    "KEY_SCROLLLOCK": 70,
    "KEY_RIGHTCTRL": 97,
    "KEY_RIGHTALT": 100,
    "KEY_INSERT": 110,
    "KEY_PAUSE": 119,
    "KEY_LEFTMETA": 125,
    "KEY_RIGHTMETA": 126,
    "KEY_COMPOSE": 127,
    **{f"KEY_F{number}": 170 + number for number in range(13, 25)},
}


def evdev_name(qt_key: int) -> str | None:
    """Return the evdev name for a Qt key code, or None if it has no mapping."""
    if ord("A") <= qt_key <= ord("Z") or ord("0") <= qt_key <= ord("9"):
        return f"KEY_{chr(qt_key)}"
    if _QT_KEY_F1 <= qt_key <= _QT_KEY_F24:
        return f"KEY_F{qt_key - _QT_KEY_F1 + 1}"
    return _NAMED.get(qt_key)
