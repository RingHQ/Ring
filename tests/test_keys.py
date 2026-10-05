import pytest

from looplinux.input.keys import evdev_name
from looplinux.input.xkey import KeyListenerError, x_keycode


@pytest.mark.parametrize(
    ("qt_key", "expected"),
    [
        (0x41, "KEY_A"),
        (0x5A, "KEY_Z"),
        (0x30, "KEY_0"),
        (0x39, "KEY_9"),
        (0x20, "KEY_SPACE"),
        (0x01000000, "KEY_ESC"),
        (0x01000004, "KEY_ENTER"),
        (0x01000012, "KEY_LEFT"),
        (0x01000013, "KEY_UP"),
        (0x01000014, "KEY_RIGHT"),
        (0x01000015, "KEY_DOWN"),
        (0x01000030, "KEY_F1"),
        (0x0100003B, "KEY_F12"),
        (0x01000020, None),  # Shift
        (0x01000022, None),  # Meta
    ],
)
def test_evdev_name(qt_key: int, expected: str | None) -> None:
    assert evdev_name(qt_key) == expected


def test_trigger_keycodes() -> None:
    assert x_keycode("KEY_RIGHTCTRL") == 105
    assert x_keycode("KEY_RIGHTALT") == 108
    assert x_keycode("KEY_F13") == 191
    with pytest.raises(KeyListenerError, match="KEY_A cannot be used as a trigger key"):
        x_keycode("KEY_A")
