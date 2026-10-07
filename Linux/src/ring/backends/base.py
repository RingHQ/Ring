"""The interface every window-management backend implements."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from ring.actions import Rect


class BackendError(RuntimeError):
    """A backend could not talk to the compositor or carry out a request."""


class UnsupportedSessionError(BackendError):
    """No backend exists for the running session; the message explains why."""


@dataclass(frozen=True, slots=True)
class Window:
    """A snapshot of a top-level window."""

    id: str
    app_id: str
    title: str
    geometry: Rect
    fullscreen: bool = False


@dataclass(frozen=True, slots=True)
class Monitor:
    """An output and its full geometry in the global coordinate space."""

    name: str
    geometry: Rect


@dataclass(frozen=True, slots=True)
class Animation:
    """How a window glides to a new frame: a cubic bezier curve over a duration."""

    curve: tuple[float, float, float, float]
    duration_ms: int


@dataclass(frozen=True, slots=True)
class StashEntry:
    """A window tucked away behind a screen edge."""

    window_id: str
    action: str
    monitor: str
    # Where the window was before it was stashed.
    restore: Rect
    # Slid out against the edge, and pushed out past it.
    revealed: Rect
    stashed: Rect


@dataclass(frozen=True, slots=True)
class Scene:
    """What the menu needs to know to open, taken at one moment."""

    window: Window | None
    cursor: tuple[int, int]
    monitors: list[Monitor]
    # The monitor under the cursor and its work area.
    monitor: Monitor
    area: Rect


class WindowBackend(ABC):
    """Query and manipulate windows on one compositor or window manager.

    All rectangles use the compositor's global coordinate space. Methods raise
    `BackendError` when the compositor cannot be reached or rejects a request,
    for example because the window was closed in the meantime.
    """

    name: ClassVar[str]

    # Set by the caller from the configuration; None means "jump".
    animation: Animation | None = None
    stash_animation: Animation | None = None
    stash_shift_focus: bool = True

    @abstractmethod
    def get_active_window(self) -> Window | None:
        """Return the focused window, or None if there is nothing to snap.

        Desktops, panels and other shell surfaces count as "nothing".
        """

    @abstractmethod
    def get_windows(self) -> list[Window]:
        """Return the normal, visible windows of the current virtual desktop."""

    @abstractmethod
    def get_monitors(self) -> list[Monitor]:
        """Return all connected monitors in a stable order."""

    @abstractmethod
    def get_work_area(self, monitor: Monitor) -> Rect:
        """Return the monitor area not reserved by panels or struts."""

    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int]:
        """Return the pointer position in global coordinates."""

    def get_scene(self) -> Scene:
        """Return the active window, the cursor and the monitor it is on.

        Backends that can answer all of it in one request override this.
        """
        window = self.get_active_window()
        cursor = self.get_cursor_position()
        monitors = self.get_monitors()
        if not monitors:
            raise BackendError("No monitors found.")
        monitor = next((m for m in monitors if m.geometry.contains(*cursor)), monitors[0])
        return Scene(window, cursor, monitors, monitor, self.get_work_area(monitor))

    @abstractmethod
    def set_geometry(self, window: Window, rect: Rect) -> None:
        """Move and resize the window, leaving fullscreen or maximized state first.

        Glides there if `animation` is set; the call does not wait for that.
        """

    @abstractmethod
    def set_fullscreen(self, window: Window, fullscreen: bool) -> None:
        """Enter or leave fullscreen."""

    @abstractmethod
    def minimize(self, window: Window) -> None:
        """Minimize the window."""

    @abstractmethod
    def move_to_desktop(self, window: Window, offset: int) -> None:
        """Send the window `offset` virtual desktops along, wrapping, and follow it."""

    @abstractmethod
    def watch_stash(self, entries: Sequence[StashEntry], hide: Sequence[str] = ()) -> Sequence[str]:
        """Take over the stashed windows.

        From now on each window in `entries` slides out while the pointer is
        over it, or when it is activated, and hides again when the pointer
        leaves. Windows whose id is in `hide` are hidden right away. An empty
        `entries` stops the watching.

        Returns the ids of the windows in `entries` that no longer exist.
        """

    def is_composited(self) -> bool:
        """Tell whether windows can be transparent right now.

        Always true on Wayland. An X11 window manager can have compositing
        switched off, and then a transparent window is drawn on black.
        """
        return True

    def close(self) -> None:  # noqa: B027 - optional hook, not every backend holds resources
        """Release any connection held by the backend."""
