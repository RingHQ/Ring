"""Apply an action to a window through a backend."""

import contextlib
from collections.abc import Sequence

from ring.actions import (
    DESKTOP_ACTIONS,
    INCREMENTAL_ACTIONS,
    SCREEN_ACTIONS,
    STASH_ACTIONS,
    Action,
    ActionSpec,
    CycleAction,
    Leaf,
    PluginAction,
    Rect,
    action_name,
    cycle_monitor,
    cycle_step,
    directional_monitor,
    move_to_monitor_rect,
    stash_conflict,
    stash_frames,
    target_rect,
)
from ring.backends.base import (
    Animation,
    BackendError,
    Monitor,
    StashEntry,
    Window,
    WindowBackend,
)
from ring.config import ANIMATIONS, Config
from ring.history import History
from ring.plugins import REGISTRY, ActionContext


class ActionError(RuntimeError):
    """The action could not be applied; the message is meant for the user."""


def configure_backend(backend: WindowBackend, config: Config) -> None:
    """Tell the backend how windows should move, as set in the configuration."""
    curve, duration_ms, _ = ANIMATIONS[config.animation.style]
    animation = Animation(curve, duration_ms) if duration_ms > 0 else None
    backend.animation = animation if config.animation.windows else None
    backend.stash_animation = animation if config.stash.animate else None
    backend.stash_shift_focus = config.stash.shift_focus


def monitor_for_rect(rect: Rect, monitors: Sequence[Monitor]) -> Monitor:
    """Return the monitor showing the largest part of `rect`."""
    if not monitors:
        raise ActionError("No monitors found.")

    def overlap(monitor: Monitor) -> int:
        area = monitor.geometry
        width = min(rect.right, area.right) - max(rect.x, area.x)
        height = min(rect.bottom, area.bottom) - max(rect.y, area.y)
        return max(width, 0) * max(height, 0)

    return max(monitors, key=overlap)


def monitor_at(x: int, y: int, monitors: Sequence[Monitor]) -> Monitor:
    """Return the monitor under a point, falling back to the first one."""
    if not monitors:
        raise ActionError("No monitors found.")
    return next((m for m in monitors if m.geometry.contains(x, y)), monitors[0])


def recorded_action(config: Config, history: History, window: Window) -> Leaf | None:
    """Return the action the window is still snapped to, if any."""
    name = history.last_action(window.id, window.geometry)
    return None if name is None else config.leaf(name)


def compute_target(
    action: Leaf,
    backend: WindowBackend,
    config: Config,
    window: Window,
    monitor: Monitor,
    area: Rect,
    frame: Rect | None = None,
) -> Rect | None:
    """Return the frame `action` gives `window` on `monitor`, or None if it has none.

    `frame` is where incremental actions (larger, grow, move, ...) continue
    from; it defaults to the window's current frame.
    """
    others: list[Rect] = []
    if action is Action.FILL_AVAILABLE_SPACE:
        others = [other.geometry for other in backend.get_windows() if other.id != window.id]
    start = frame if frame is not None and action in INCREMENTAL_ACTIONS else window.geometry
    return target_rect(
        action,
        area,
        start,
        config.gaps_for(monitor.name),
        config.almost_maximize_margin_for(monitor.name),
        config.actions.size_increment,
        100 + config.preview.padding,
        others,
    )


def watch_stash(backend: WindowBackend, history: History, hide: Sequence[str] = ()) -> None:
    """Hand the stashed windows to the backend; forget those that were closed."""
    for window_id in backend.watch_stash(history.stashed(), hide=hide):
        history.unstash(window_id)


def _unmanage(backend: WindowBackend, history: History, window: Window) -> None:
    """Stop treating the window as stashed, because something else moved it."""
    if history.unstash(window.id) is not None:
        watch_stash(backend, history)


def _stash(
    action: Action,
    backend: WindowBackend,
    config: Config,
    history: History,
    window: Window,
    monitor: Monitor,
    area: Rect,
) -> None:
    existing = history.stash_entry(window.id)
    # A window that is stashed already is re-stashed from where it slides
    # out to, and still goes back to where it originally came from.
    frame = window.geometry if existing is None else existing.revealed
    restore = window.geometry if existing is None else existing.restore
    revealed, stashed = stash_frames(action, frame, area, config.stash.peek)

    # Two windows cannot hide behind the same stretch of an edge: the one
    # that was there first goes back to where it came from.
    for other in history.stashed():
        same_edge = other.action == action.value and other.monitor == monitor.name
        if other.window_id == window.id or not same_edge:
            continue
        if stash_conflict(revealed, other.revealed, vertical=action is not Action.STASH_BOTTOM):
            history.unstash(other.window_id)
            # The other window may have been closed in the meantime.
            with contextlib.suppress(BackendError):
                backend.set_geometry(Window(other.window_id, "", "", other.stashed), other.restore)

    history.record(window.id, window.geometry)
    history.set_stash(StashEntry(window.id, action.value, monitor.name, restore, revealed, stashed))
    watch_stash(backend, history, hide=[window.id])


def apply_frame(
    action: Leaf,
    rect: Rect,
    backend: WindowBackend,
    config: Config,
    history: History,
    window: Window,
    monitor: Monitor,
    area: Rect,
) -> None:
    """Give the window the frame `rect` and remember how it got there.

    For a stash action `rect` is where the window slides out to; the window
    itself ends up hidden behind the edge of `area`.
    """
    if action in STASH_ACTIONS:
        assert isinstance(action, Action)
        _stash(action, backend, config, history, window, monitor, area)
        return
    _unmanage(backend, history, window)
    history.record(window.id, window.geometry)
    backend.set_geometry(window, rect)
    history.set_last_action(window.id, action_name(action), rect)


def _other_monitor(action: Action, home: Monitor, monitors: Sequence[Monitor]) -> Monitor:
    if len(monitors) < 2:
        raise ActionError("There is only one monitor.")
    index = monitors.index(home)
    if action is Action.NEXT_SCREEN:
        return monitors[cycle_monitor(index, len(monitors), 1)]
    if action is Action.PREVIOUS_SCREEN:
        return monitors[cycle_monitor(index, len(monitors), -1)]
    target = directional_monitor(index, [monitor.geometry for monitor in monitors], action)
    if target is None:
        raise ActionError("There is no monitor in that direction.")
    return monitors[target]


def screen_target(
    action: Action,
    backend: WindowBackend,
    window: Window,
    monitors: Sequence[Monitor] | None = None,
) -> tuple[Monitor, Rect, Rect]:
    """Return where a screen action sends `window`: monitor, work area, frame."""
    monitors = backend.get_monitors() if monitors is None else monitors
    home = monitor_for_rect(window.geometry, monitors)
    monitor = _other_monitor(action, home, monitors)
    area = backend.get_work_area(monitor)
    return monitor, area, move_to_monitor_rect(window.geometry, backend.get_work_area(home), area)


def _perform_plugin_action(
    action: PluginAction,
    backend: WindowBackend,
    config: Config,
    history: History,
    window: Window,
    monitor: Monitor | None,
) -> Rect | None:
    registered = REGISTRY.actions.get(action.name)
    if registered is None:
        plugin = action.name.split(".")[0]
        reason = REGISTRY.errors.get(plugin, "the plugin is not enabled or not installed")
        raise ActionError(f"The action '{action.name}' is not available: {reason}.")
    monitor = monitor or monitor_for_rect(window.geometry, backend.get_monitors())
    area = backend.get_work_area(monitor)
    try:
        gaps = config.gaps_for(monitor.name)
        target = registered.handler(ActionContext(window, monitor, area, backend, gaps))
    except (ActionError, BackendError):
        raise
    except Exception as error:
        raise ActionError(f"The plugin action '{action.name}' failed: {error}") from error
    if target is None:
        return None
    if not isinstance(target, Rect) or target.width <= 0 or target.height <= 0:
        raise ActionError(f"The plugin action '{action.name}' returned an invalid frame.")
    apply_frame(action, target, backend, config, history, window, monitor, area)
    return target


def perform(
    action: ActionSpec,
    backend: WindowBackend,
    config: Config,
    history: History,
    *,
    window: Window | None = None,
    monitor: Monitor | None = None,
) -> Rect | None:
    """Apply `action` and return the window's new geometry, if it got one.

    `window` defaults to the active window and `monitor` to the monitor that
    window is on; the overlay passes the monitor under the cursor instead.
    A cycle advances from the step the window is currently snapped to.
    Raises `ActionError` when there is nothing sensible to do.
    """
    if action is Action.NONE:
        return None
    if window is None:
        window = backend.get_active_window()
    if window is None:
        raise ActionError("There is no active window to act on.")
    if config.is_excluded(window.app_id):
        raise ActionError(f"'{window.app_id}' is listed in [exclusions] and was left alone.")
    if isinstance(action, CycleAction):
        action = cycle_step(
            action,
            Action.NONE,
            recorded_action(config, history, window),
            advance=True,
            restart=config.actions.cycle_restart,
        )

    if isinstance(action, PluginAction):
        return _perform_plugin_action(action, backend, config, history, window, monitor)

    match action:
        case Action.FULLSCREEN:
            backend.set_fullscreen(window, not window.fullscreen)
            return None
        case Action.MINIMIZE:
            backend.minimize(window)
            return None
        case Action.MINIMIZE_OTHERS:
            for other in backend.get_windows():
                if other.id != window.id:
                    backend.minimize(other)
            return None
        case Action.UNDO:
            previous = history.pop_undo(window.id)
            if previous is None:
                raise ActionError("Nothing to undo for this window.")
            _unmanage(backend, history, window)
            backend.set_geometry(window, previous)
            return previous
        case Action.INITIAL_FRAME:
            original = history.original(window.id)
            if original is None:
                raise ActionError("This window has not been snapped, so there is no initial frame.")
            _unmanage(backend, history, window)
            backend.set_geometry(window, original)
            history.forget(window.id)
            return original
    if action is Action.UNSTASH:
        entry = history.unstash(window.id)
        if entry is None:
            raise ActionError("This window is not stashed.")
        watch_stash(backend, history)
        backend.set_geometry(window, entry.restore)
        return entry.restore
    if action in DESKTOP_ACTIONS:
        backend.move_to_desktop(window, 1 if action is Action.NEXT_DESKTOP else -1)
        return None

    target: Rect | None
    if action in SCREEN_ACTIONS:
        assert isinstance(action, Action)
        monitor, area, target = screen_target(action, backend, window)
    else:
        monitor = monitor or monitor_for_rect(window.geometry, backend.get_monitors())
        area = backend.get_work_area(monitor)
        target = compute_target(action, backend, config, window, monitor, area)
    if target is None:
        raise ActionError(f"The action '{action_name(action)}' cannot be applied.")

    apply_frame(action, target, backend, config, history, window, monitor, area)
    return target
