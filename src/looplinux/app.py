"""The daemon: event loop and the hold-to-snap state machine.

Idle, the process sleeps in the Qt event loop and wakes only for a D-Bus
signal (trigger pressed/released), a socket command or a Unix signal. While
the trigger is held it additionally reacts to pointer and key events from the
overlay.

How the pointer, clicks and keys select actions follows Loop's LoopManager,
MouseInteractionObserver and RadialMenuViewModel.
"""

import logging
import math
import os
import signal
import socket
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QSocketNotifier, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence

from looplinux.actions import (
    Action,
    ActionSpec,
    CycleAction,
    Direction,
    Leaf,
    Rect,
    action_name,
    cycle_step,
    direction_at,
    fills_radial_menu,
    is_repeatable,
    radial_angle,
    sector_angle,
)
from looplinux.backends import BackendError, Monitor, Window, WindowBackend, create_backend
from looplinux.config import Config
from looplinux.executor import (
    ActionError,
    apply_frame,
    compute_target,
    configure_backend,
    monitor_at,
    perform,
    recorded_action,
)
from looplinux.history import History, default_history_path
from looplinux.input.keys import evdev_name
from looplinux.input.shortcut import GlobalShortcut, ShortcutError
from looplinux.input.xkey import KeyListener, KeyListenerError
from looplinux.ipc import IpcError, IpcServer, send_command
from looplinux.overlay import Highlight, Overlay, OverlayError

log = logging.getLogger("looplinux")

# Pointer travel needed before the mouse takes over from a key selection.
_KEY_SELECTION_SLACK = 12
# Close a menu that was left open, e.g. because a release event got lost.
_WATCHDOG_MS = 60_000


class StartupError(RuntimeError):
    """The daemon cannot start; the message is meant for the user."""


@dataclass(slots=True)
class _Session:
    """State of one trigger hold."""

    window: Window
    monitor: Monitor
    area: Rect
    center: tuple[int, int]
    # The action the window is snapped to already; cycles continue after it.
    recorded: Leaf | None
    # Where incremental actions (larger, grow, move, ...) continue from.
    frame: Rect
    # What is bound to the selected key or sector, and the step of it in use.
    spec: ActionSpec = Action.NONE
    leaf: Leaf = Action.NONE
    target: Rect | None = None
    # The menu slot the selection was pointed at; None for keys.
    direction: Direction | None = None
    pointer: tuple[float, float] | None = None
    key_anchor: tuple[float, float] | None = None
    held: set[str] = field(default_factory=set)


class Controller(QObject):
    """Turns trigger, pointer and key events into window actions."""

    def __init__(
        self,
        config: Config,
        backend: WindowBackend,
        overlay: Overlay,
        history: History,
        trigger_key: int,
        trigger_scan_code: int = -1,
    ) -> None:
        """Create the controller.

        `trigger_key` is the Qt key of the trigger and `trigger_scan_code`
        its X keycode; either may be -1 if unknown. They let the overlay
        recognise the trigger being released while it has the keyboard.
        """
        super().__init__()
        self._config = config
        self._backend = backend
        self._overlay = overlay
        self._history = history
        self._trigger_key = trigger_key
        self._trigger_scan_code = trigger_scan_code
        self._session: _Session | None = None
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(_WATCHDOG_MS)
        self._watchdog.timeout.connect(self.cancel)
        overlay.pointer_moved.connect(self._on_pointer)
        overlay.key_pressed.connect(self._on_key_pressed)
        overlay.key_released.connect(self._on_key_released)
        overlay.clicked.connect(self._on_click)

    @property
    def active(self) -> bool:
        return self._session is not None

    def press(self) -> None:
        """The trigger went down: show the menu for the active window."""
        if self._session is not None:
            return
        try:
            # Must come first: the overlay takes keyboard focus once shown.
            window = self._backend.get_active_window()
            if window is None:
                log.info("trigger ignored: no active window")
                return
            if self._config.is_excluded(window.app_id):
                log.info("trigger ignored: %s is excluded", window.app_id)
                return
            cursor = self._backend.get_cursor_position()
            monitor = monitor_at(*cursor, self._backend.get_monitors())
            area = self._backend.get_work_area(monitor)
        except (BackendError, ActionError) as error:
            log.error("cannot open the menu: %s", error)
            return

        origin = monitor.geometry
        center = (cursor[0] - origin.x, cursor[1] - origin.y)
        self._session = _Session(
            window=window,
            monitor=monitor,
            area=area,
            center=center,
            recorded=recorded_action(self._config, self._history, window),
            frame=window.geometry,
        )
        self._overlay.show(monitor, center)
        self._watchdog.start()

    def release(self) -> None:
        """The trigger went up: apply the selected action."""
        session = self._close()
        if session is None or session.leaf is Action.NONE:
            return
        try:
            if session.target is not None:
                apply_frame(
                    session.leaf,
                    session.target,
                    self._backend,
                    self._config,
                    self._history,
                    session.window,
                    session.monitor,
                    session.area,
                )
            else:
                perform(
                    session.leaf,
                    self._backend,
                    self._config,
                    self._history,
                    window=session.window,
                    monitor=session.monitor,
                )
        except (ActionError, BackendError) as error:
            log.warning("%s: %s", action_name(session.leaf), error)

    def cancel(self) -> None:
        """Close the menu without touching the window."""
        self._close()

    def toggle(self) -> None:
        if self._session is None:
            self.press()
        else:
            self.release()

    def _close(self) -> _Session | None:
        session, self._session = self._session, None
        if session is not None:
            self._watchdog.stop()
            self._overlay.hide()
        return session

    def _change(
        self,
        spec: ActionSpec,
        *,
        direction: Direction | None,
        advance: bool,
        backward: bool = False,
    ) -> None:
        """Select what is bound to a key or sector.

        `advance` is set for key presses and clicks, which step through a
        cycle and repeat incremental actions; merely pointing at something
        does neither.
        """
        session = self._session
        if session is None:
            return
        leaf: Leaf
        if isinstance(spec, CycleAction):
            leaf = cycle_step(
                spec,
                session.leaf,
                session.recorded,
                advance=advance,
                backward=backward,
                restart=self._config.actions.cycle_restart,
            )
        else:
            leaf = spec
        repeat = advance and is_repeatable(leaf)
        if leaf == session.leaf and spec == session.spec and not repeat:
            if session.direction != direction:
                # The same action, now reached through another slot.
                session.direction = direction
                self._show(session)
            return

        target: Rect | None = None
        if leaf is not Action.NONE:
            try:
                target = compute_target(
                    leaf,
                    self._backend,
                    self._config,
                    session.window,
                    session.monitor,
                    session.area,
                    session.frame,
                )
            except BackendError as error:
                log.warning("%s: %s", action_name(leaf), error)
        session.spec, session.leaf, session.direction = spec, leaf, direction
        session.target = target
        if target is not None:
            session.frame = target
        self._show(session)

    def _show(self, session: _Session) -> None:
        origin = session.monitor.geometry
        target = session.target
        local = None
        if target is not None:
            local = Rect(target.x - origin.x, target.y - origin.y, target.width, target.height)
        self._overlay.select(*self._highlight(session), local)

    def _highlight(self, session: _Session) -> tuple[Highlight, float]:
        """Decide how the ring shows the selection.

        Anything that sits in the menu lights up its own slot, wherever it
        was selected from. Other actions light up the side of the screen
        they snap to, or the whole ring if they have no side.
        """
        if session.leaf is Action.NONE:
            return Highlight.NONE, 0.0
        sectors = self._config.radial.sectors
        slot = session.direction
        if slot is None:
            slot = next((d for d in Direction if sectors.get(d) == session.spec), None)
        if slot is Direction.CENTER:
            return Highlight.FULL, 0.0
        if slot is not None:
            return Highlight.SEGMENT, sector_angle(slot)
        if fills_radial_menu(session.leaf):
            return Highlight.FULL, 0.0
        angle = radial_angle(session.leaf)
        if angle is None:
            return Highlight.NONE, 0.0
        return Highlight.SEGMENT, angle

    def _pin(self, session: _Session) -> None:
        """Keep a key selection until the pointer clearly moves again."""
        session.key_anchor = session.pointer or (
            float(session.center[0]),
            float(session.center[1]),
        )

    def _on_pointer(self, x: float, y: float) -> None:
        session = self._session
        if session is None:
            return
        session.pointer = (x, y)
        if session.key_anchor is not None:
            moved = math.hypot(x - session.key_anchor[0], y - session.key_anchor[1])
            if moved < _KEY_SELECTION_SLACK:
                return
            session.key_anchor = None
        direction = direction_at(
            x - session.center[0],
            y - session.center[1],
            self._config.radial.radius,
            self._config.radial.thickness,
        )
        spec: ActionSpec = Action.NONE
        if direction is not None:
            spec = self._config.radial.sectors.get(direction, Action.NONE)
        self._change(spec, direction=direction, advance=False)

    def _on_key_pressed(self, key: int, shift: bool) -> None:
        session = self._session
        if session is None or key == self._trigger_key:
            return
        name = evdev_name(key)
        if name is None:
            return
        if name == self._config.trigger.cancel_key:
            self.cancel()
            return
        session.held.add(name)
        spec = self._config.keybindings.get(frozenset(session.held), Action.NONE)
        if spec is Action.NONE:
            return
        self._pin(session)
        backward = shift and self._config.actions.cycle_backwards_on_shift
        self._change(spec, direction=None, advance=True, backward=backward)

    def _on_key_released(self, key: int, scan_code: int) -> None:
        # Normally the trigger source reports the release; this is the
        # fallback for when the overlay sees the key go up first or instead.
        if key == self._trigger_key or scan_code == self._trigger_scan_code:
            self.release()
            return
        name = evdev_name(key)
        if self._session is not None and name is not None:
            self._session.held.discard(name)

    def _on_click(self, secondary: bool) -> None:
        session = self._session
        if session is None:
            return
        if secondary:
            self.cancel()
        elif isinstance(session.spec, CycleAction) or is_repeatable(session.leaf):
            # A left click steps through the selected cycle, or repeats an
            # incremental action such as "larger".
            self._change(session.spec, direction=session.direction, advance=True)


def _parse_shortcut(text: str) -> tuple[int, int]:
    """Return (combined key code, plain key code) for a one-key sequence."""
    sequence = QKeySequence(text)
    if sequence.count() != 1 or sequence[0].key().value in (0, 0x01FFFFFF):
        raise StartupError(
            f"trigger.shortcut: '{text}' is not a valid shortcut. "
            'Use a single key combination such as "Meta+X" or "F13".'
        )
    return int(sequence[0].toCombined()), int(sequence[0].key().value)


def run(config: Config, backend_name: str | None = None) -> int:
    """Run the daemon until it is told to quit."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        raise StartupError(
            "The radial menu currently needs a Wayland session (it is drawn as a "
            "layer-shell surface). `looplinux snap` works without it."
        )
    try:
        send_command("ping", timeout=0.5)
    except IpcError:
        pass
    else:
        raise StartupError("looplinux is already running.")

    os.environ["QT_QPA_PLATFORM"] = "wayland"
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    app = QGuiApplication(["looplinux"])
    app.setQuitOnLastWindowClosed(False)

    backend = create_backend(backend_name)
    trigger: GlobalShortcut | KeyListener | None = None
    try:
        try:
            overlay = Overlay(config)
        except OverlayError as error:
            raise StartupError(str(error)) from error
        history = History(default_history_path())
        configure_backend(backend, config)
        try:
            # Pick the stashed windows up again after a restart.
            backend.watch_stash(history.stashed())
        except BackendError as error:
            log.warning("cannot watch stashed windows: %s", error)

        # The controller is created once the trigger is known, but the
        # trigger needs callbacks now.
        controllers: list[Controller] = []
        held = config.trigger.key
        if config.trigger.use == "key":
            try:
                trigger = KeyListener(
                    held, lambda: controllers[0].press(), lambda: controllers[0].release()
                )
                controllers.append(
                    Controller(config, backend, overlay, history, -1, trigger.keycode)
                )
            except KeyListenerError as error:
                log.warning("%s; falling back to the shortcut %s", error, config.trigger.shortcut)
        if trigger is None:
            held = config.trigger.shortcut
            combined, trigger_key = _parse_shortcut(held)
            controllers.append(Controller(config, backend, overlay, history, trigger_key))
            try:
                trigger = GlobalShortcut(
                    combined, held, controllers[0].press, controllers[0].release
                )
            except ShortcutError as error:
                raise StartupError(str(error)) from error
        controller = controllers[0]

        def on_trigger_activity() -> None:
            assert trigger is not None
            if not trigger.dispatch():
                log.error("lost the connection the trigger comes from; quitting")
                app.exit(1)

        trigger_notifier = QSocketNotifier(trigger.fileno(), QSocketNotifier.Type.Read)
        trigger_notifier.activated.connect(on_trigger_activity)

        def on_command(command: str) -> str:
            if command == "quit":
                # Deferred so the answer is sent before the loop stops.
                QTimer.singleShot(0, app.quit)
            elif command != "ping":
                getattr(controller, command)()
            return "ok"

        try:
            ipc = IpcServer(on_command)
        except IpcError as error:
            raise StartupError(str(error)) from error

        # Unix signals wake the event loop through a socket; no polling timer.
        wake_read, wake_write = socket.socketpair()
        wake_write.setblocking(False)
        signal.set_wakeup_fd(wake_write.fileno())
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, lambda *_: None)
        signal_notifier = QSocketNotifier(wake_read.fileno(), QSocketNotifier.Type.Read)
        signal_notifier.activated.connect(app.quit)

        log.info("ready: hold %s to open the radial menu", held)
        try:
            return int(app.exec())
        finally:
            controller.cancel()
            signal.set_wakeup_fd(-1)
            ipc.close()
    finally:
        if trigger is not None:
            trigger.close()
        backend.close()
