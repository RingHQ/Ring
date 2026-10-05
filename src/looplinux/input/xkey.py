"""Hold-to-trigger on a single key, observed through the X server.

A lone key such as Right Ctrl cannot be a desktop shortcut. X11 clients can
still watch the keyboard with XInput2 raw events, and on Wayland compositors
that run Xwayland those keep arriving for modifier and other non-character
keys (in KWin: "Legacy X11 App Support", on by default for exactly these
keys). That is enough for a trigger key and needs no special permissions.

The key is only observed, not grabbed: the focused application still sees it.
"""

import struct
from collections.abc import Callable
from typing import Any

from looplinux.input.keys import EVDEV_CODES

# XInput2 event types.
_RAW_KEY_PRESS = 13
_RAW_KEY_RELEASE = 14
# X keycodes are evdev codes shifted by this much.
_X_KEYCODE_OFFSET = 8


class KeyListenerError(RuntimeError):
    """The key cannot be watched in this session."""


def x_keycode(key: str) -> int:
    """Return the X keycode of an evdev key name usable as a trigger."""
    try:
        return EVDEV_CODES[key] + _X_KEYCODE_OFFSET
    except KeyError:
        usable = ", ".join(sorted(EVDEV_CODES))
        raise KeyListenerError(
            f"trigger.key: {key} cannot be used as a trigger key. Usable keys: {usable}"
        ) from None


class KeyListener:
    """Calls back when one key goes down and when it comes back up.

    Nothing is read in the background: watch `fileno()` for readability and
    call `dispatch()` when it fires.
    """

    def __init__(
        self, key: str, on_press: Callable[[], None], on_release: Callable[[], None]
    ) -> None:
        """Watch `key`, an evdev key name such as KEY_RIGHTCTRL."""
        self.keycode = x_keycode(key)
        self._on_press = on_press
        self._on_release = on_release
        self._down = False
        try:
            from Xlib import display
            from Xlib.ext import xinput
        except ImportError as error:
            raise KeyListenerError(f"python-xlib is not installed ({error})") from error
        try:
            self._display: Any = display.Display()
            extension = self._display.query_extension("XInputExtension")
            if extension is None:
                raise KeyListenerError("the X server has no XInput extension")
            self._opcode = extension.major_opcode
            self._generic_event = self._display.extension_event.GenericEvent
            self._display.xinput_query_version()
            mask = xinput.RawKeyPressMask | xinput.RawKeyReleaseMask
            self._display.screen().root.xinput_select_events([(xinput.AllMasterDevices, mask)])
            self._display.sync()
        except KeyListenerError:
            raise
        except Exception as error:
            raise KeyListenerError(f"cannot watch keys through the X server: {error}") from error

    def fileno(self) -> int:
        return int(self._display.fileno())

    def dispatch(self) -> bool:
        """Handle every pending event; return False once the X server is gone."""
        try:
            while self._display.pending_events():
                event = self._display.next_event()
                if event.type != self._generic_event or event.extension != self._opcode:
                    continue
                if event.evtype not in (_RAW_KEY_PRESS, _RAW_KEY_RELEASE):
                    continue
                # Raw event body: device id (2 bytes), time (4), key code (4), ...
                _, _, keycode = struct.unpack_from("=HII", event.data)
                if keycode != self.keycode:
                    continue
                down = event.evtype == _RAW_KEY_PRESS
                if down == self._down:
                    continue  # key repeat
                self._down = down
                if down:
                    self._on_press()
                else:
                    self._on_release()
        except Exception:
            return False
        return True

    def close(self) -> None:
        self._display.close()
