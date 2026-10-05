"""Hold-to-trigger through KDE's global shortcut service.

KGlobalAccel reports both the press and the release of a registered shortcut
over D-Bus, which is all a hold-to-trigger key needs. It works on Wayland and
X11 without any special permissions, but cannot bind a lone modifier key.
"""

import contextlib
from collections.abc import Callable
from typing import Any

from jeepney import DBusAddress, HeaderFields, MatchRule, MessageType, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

_SERVICE = "org.kde.kglobalaccel"
_DAEMON = DBusAddress("/kglobalaccel", bus_name=_SERVICE, interface="org.kde.KGlobalAccel")
_COMPONENT = "looplinux"
_COMPONENT_PATH = f"/component/{_COMPONENT}"
_COMPONENT_INTERFACE = "org.kde.kglobalaccel.Component"
_ACTION_ID = [_COMPONENT, "trigger", "loop-linux", "Hold to show the radial menu"]

# KGlobalAccel flags: mark the shortcut active and take the keys from us
# instead of from whatever was stored for this action earlier.
_SET_PRESENT = 2
_NO_AUTOLOADING = 4
_TIMEOUT = 3.0


class ShortcutError(RuntimeError):
    """The global shortcut could not be registered."""


class GlobalShortcut:
    """A global shortcut that calls back on press and on release.

    Nothing is read in the background: watch `fileno()` for readability and
    call `dispatch()` when it fires.
    """

    def __init__(
        self,
        key: int,
        description: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        """Register `key`, a Qt key combination as an int (QKeyCombination.toCombined)."""
        self._on_press = on_press
        self._on_release = on_release
        try:
            self._connection = open_dbus_connection(bus="SESSION")
        except Exception as error:
            raise ShortcutError(f"Cannot connect to the session D-Bus: {error}") from error
        try:
            self._call("doRegister", "as", _ACTION_ID)
            sequence = ([key, 0, 0, 0],)
            flags = _SET_PRESENT | _NO_AUTOLOADING
            granted = self._call("setShortcutKeys", "asa(ai)u", _ACTION_ID, [sequence], flags)
            if not granted or granted[0][0][0] != key:
                self._call("unRegister", "as", _ACTION_ID)
                raise ShortcutError(
                    f"The shortcut {description} is already used by something else. "
                    "Pick another one with trigger.shortcut in the config file, or free it "
                    "in System Settings > Keyboard > Shortcuts."
                )
            rule = MatchRule(
                type="signal",
                sender=_SERVICE,
                interface=_COMPONENT_INTERFACE,
                path=_COMPONENT_PATH,
            )
            self._connection.send_and_get_reply(message_bus.AddMatch(rule), timeout=_TIMEOUT)
        except ShortcutError:
            self._connection.close()
            raise
        except Exception as error:
            self._connection.close()
            raise ShortcutError(f"KDE's global shortcut service did not answer: {error}") from error

    def fileno(self) -> int:
        return int(self._connection.sock.fileno())

    def dispatch(self) -> bool:
        """Handle every pending message; return False once the bus is gone."""
        while True:
            try:
                message = self._connection.receive(timeout=0)
            except TimeoutError:
                return True
            except (OSError, EOFError):
                return False
            header = message.header
            if (
                header.message_type is not MessageType.signal
                or header.fields.get(HeaderFields.path) != _COMPONENT_PATH
                or header.fields.get(HeaderFields.interface) != _COMPONENT_INTERFACE
                or len(message.body) < 2
                or message.body[1] != _ACTION_ID[1]
            ):
                continue
            member = header.fields.get(HeaderFields.member)
            if member == "globalShortcutPressed":
                self._on_press()
            elif member == "globalShortcutReleased":
                self._on_release()

    def close(self) -> None:
        """Give the shortcut back so the keys reach applications again."""
        # Shutting down: if the service is gone there is nothing left to undo.
        with contextlib.suppress(Exception):
            self._call("unRegister", "as", _ACTION_ID)
        self._connection.close()

    def _call(self, method: str, signature: str, *body: object) -> Any:
        message = new_method_call(_DAEMON, method, signature, body)
        reply = self._connection.send_and_get_reply(message, timeout=_TIMEOUT)
        if reply.header.message_type is MessageType.error:
            detail = reply.body[0] if reply.body else "unknown error"
            raise ShortcutError(f"KDE's global shortcut service rejected {method}: {detail}")
        return reply.body[0] if reply.body else None
