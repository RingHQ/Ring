"""Python side of the overlay window.

The overlay is one transparent window covering the monitor under the cursor.
It is drawn in QML (`OverlayWindow.qml`, `RadialMenu.qml`, `Preview.qml`);
this class feeds it state and forwards its input events. A second window
(`PreviewWindow.qml`) shows the preview when the target is on another
monitor.

How the windows get above everything else depends on the session, see
`surface.py`: as layer-shell surfaces (`Overlay.qml` and `RemotePreview.qml`
wrap the two windows for that), or as X11 windows that the window manager
does not manage. The X11 kind has no keyboard focus, so it grabs the
keyboard while it is shown; and without a compositor nothing is transparent,
so it is then cut to the shape of the ring and the preview's border.
"""

import ctypes
import enum
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QPainterPath, QRegion, QScreen
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem

from ring.actions import Rect
from ring.backends.base import Monitor
from ring.config import ANIMATIONS, Config
from ring.overlay.surface import LAYER_SHELL, X11, OverlayError

log = logging.getLogger("ring")

_FOLDER = Path(__file__).parent
# The ring's window and the preview's, per kind of surface.
_QML = {
    LAYER_SHELL: (_FOLDER / "Overlay.qml", _FOLDER / "RemotePreview.qml"),
    X11: (_FOLDER / "OverlayWindow.qml", _FOLDER / "PreviewWindow.qml"),
}

_ANGLE_MS = 200
_APPEAR_MS = 100
# The ring shrinks to this scale while it is lit up as a whole.
_FILLED_SCALE = 0.85


class Highlight(enum.IntEnum):
    """How the ring shows the current selection (values shared with QML)."""

    NONE = 0
    SEGMENT = 1
    FULL = 2


def _load_blur() -> Callable[[Any, QRegion], None] | None:
    """Return a function that asks the compositor to blur behind a region.

    PySide6 has no bindings for KDE's KWindowEffects, so the C++ function is
    called directly. Returns None wherever that is not possible; the overlay
    then simply stays unblurred.
    """
    try:
        import shiboken6

        library = ctypes.CDLL("libKF6WindowSystem.so.6")
        # KWindowEffects::enableBlurBehind(QWindow *, bool, const QRegion &)
        enable = library._ZN14KWindowEffects16enableBlurBehindEP7QWindowbRK7QRegion
    except (ImportError, OSError, AttributeError):
        return None
    enable.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p]
    enable.restype = None

    def blur(window: Any, region: QRegion) -> None:
        enable(shiboken6.getCppPointer(window)[0], True, shiboken6.getCppPointer(region)[0])

    return blur


def _find_screen(monitor: Monitor) -> QScreen | None:
    """Return Qt's screen for a monitor of the backend."""
    screens = QGuiApplication.screens()
    for screen in screens:
        if screen.name() == monitor.name:
            return screen
    # The names can differ on X11; the place of the top left corner does not.
    for screen in screens:
        corner = screen.geometry().topLeft()
        if (corner.x(), corner.y()) == (monitor.geometry.x, monitor.geometry.y):
            return screen
    return None


def _scale(screen: QScreen, monitor: Monitor) -> float:
    """Return how many of Qt's units make one of the backend's on a monitor.

    They are the same on Wayland. An X11 window manager counts device pixels,
    while Qt divides those by the screen's scale factor.
    """
    width = monitor.geometry.width
    return screen.geometry().width() / width if width > 0 else 1.0


class Overlay(QObject):
    """The overlay window. Coordinates are local to the monitor it is shown on."""

    pointer_moved = Signal(float, float)
    key_pressed = Signal(int, bool)
    key_released = Signal(int, int)
    clicked = Signal(bool)

    def __init__(
        self,
        config: Config,
        surface: str = LAYER_SHELL,
        composited: Callable[[], bool] | None = None,
    ) -> None:
        """Create the windows, hidden.

        `composited` tells whether windows can be transparent right now. It
        is only asked for the X11 kind of surface, each time the ring opens.
        """
        super().__init__()
        self._x11 = surface == X11
        self._radius = config.radial.radius
        self._thickness = config.radial.thickness
        self._preview = config.preview
        self._center = (0, 0)
        self._screen_size = (0, 0)
        # Qt's units per unit of the backend, on the ring's monitor and on
        # the one with the remote preview.
        self._scale = 1.0
        self._remote_scale = 1.0
        # X11 only: whether the window is cut to shape instead of transparent.
        self._composited = composited if self._x11 else None
        self._masked = False
        self._grabbed = False
        self._preview_item: QQuickItem | None = None
        # The monitor the ring is on, and the other one showing the preview.
        self._monitor: str | None = None
        self._remote_monitor: str | None = None
        self._angle = 0.0
        self._highlight = Highlight.NONE
        self._preview_shown = False
        self._preview_rect: QRectF | None = None
        wants_blur = (
            config.theme.blur or config.preview.blur or config.preview.style == "liquid_glass"
        )
        self._blur = _load_blur() if wants_blur else None
        self._menu_visible = config.radial.visible
        self._preview_visible = config.preview.visible
        self._blur_menu = config.theme.blur and self._blur is not None and self._menu_visible
        # Glass without what is behind it out of focus is only a tinted sheet.
        self._glass = config.preview.style == "liquid_glass"
        self._blur_preview = (config.preview.blur or self._glass) and self._blur is not None
        if wants_blur and self._blur is None:
            log.info("background blur is not available on this system")

        self._engine = QQmlApplicationEngine()
        self._engine.rootContext().setContextProperty("bridge", self)
        for file in _QML[surface]:
            self._engine.load(QUrl.fromLocalFile(str(file)))
        roots = self._engine.rootObjects()
        if len(roots) < 2:
            needs = "the distribution's PySide6 with Qt Quick (Arch: pyside6)"
            if not self._x11:
                needs = f"the LayerShellQt QML module (Arch: layer-shell-qt) and {needs}"
            raise OverlayError(f"Could not create the overlay window. It needs {needs}.")
        self._window, self._remote = roots[0], roots[1]
        if self._x11:
            unmanaged = (
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.X11BypassWindowManagerHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self._window.setFlags(unmanaged)
            self._remote.setFlags(unmanaged | Qt.WindowType.WindowTransparentForInput)
            self._window.installEventFilter(self)
            self._set("pushesPreview", True)
        elif self._blur_preview:
            # The blurred region has to change in the very frame the preview
            # moves in. Asked from the GUI thread, the answer can end up a
            # frame early: that thread is already on to the next frame while
            # the last one is still being sent. So it is read here, on the
            # render thread, right before each frame is drawn; the GUI thread
            # stands still for that. Nobody needs it without that blur, and
            # it is Python run for every frame.
            self._preview_item = self._window.findChild(QQuickItem, "preview")
            self._window.beforeSynchronizing.connect(
                self._before_frame, Qt.ConnectionType.DirectConnection
            )

        if config.theme.accent_color == "system":
            accent = QGuiApplication.palette().highlight().color()
        else:
            accent = QColor(config.theme.accent_color)
        gradient = QColor(config.theme.gradient_color) if config.theme.gradient_color else accent
        curve, preview_ms, size_ms = ANIMATIONS[config.animation.style]
        instant = config.animation.style == "instant"
        self._set("menuRadius", self._radius)
        self._set("menuThickness", self._thickness)
        self._set("filledScale", _FILLED_SCALE)
        ring = QColor(config.theme.ring_color)
        opacity = config.theme.ring_opacity
        # Without blur behind it the ring needs more body to stay readable.
        self._ring_alpha = opacity if self._blur_menu else 1 - (1 - opacity) * 0.28
        ring.setAlphaF(self._ring_alpha)
        self._border_follows_accent = config.preview.border_color == "accent"
        border = accent
        if config.preview.border_color != "accent":
            border = QColor(config.preview.border_color)
        fill = QColor(config.preview.fill_color)
        fill.setAlphaF(config.preview.fill_opacity)
        self._set("accent", accent)
        self._set("accent2", gradient)
        self._set("ringColor", ring)
        self._set("menuShown", self._menu_visible)
        self._set("menuBlurred", self._blur_menu)
        self._set("previewBlurred", self._blur_preview)
        for window in (self._window, self._remote):
            window.setProperty("previewBorderColor", border)
            window.setProperty("previewFill", fill)
            window.setProperty("previewRadius", self._preview.corner_radius)
            window.setProperty("previewBorder", self._preview.border_thickness)
            window.setProperty("previewCurve", [*curve, 1.0, 1.0])
            window.setProperty("previewMs", preview_ms)
        self._set("sizeMs", size_ms)
        self._set("angleMs", 0 if instant else _ANGLE_MS)
        self._set("appearMs", 0 if instant else _APPEAR_MS)

    def show(self, monitor: Monitor, center: tuple[int, int]) -> None:
        """Show the ring at `center` with nothing selected."""
        screen = _find_screen(monitor) or QGuiApplication.primaryScreen()
        self._scale = _scale(screen, monitor)
        self._masked = self._composited is not None and not self._composited()
        # Cut to shape there is nothing to see through: the outline it is.
        for window in (self._window, self._remote):
            window.setProperty("previewGlass", self._glass and not self._masked)
        self._center = center
        self._screen_size = (monitor.geometry.width, monitor.geometry.height)
        self._monitor = monitor.name
        self._hide_remote()
        self._highlight = Highlight.NONE
        self._preview_shown = False
        self._preview_rect = None
        self._window.setScreen(screen)
        # The compositor stretches the surface over the output, but Qt will
        # not map a window it believes to be empty.
        self._window.setGeometry(screen.geometry())
        self._set("animate", False)
        self._set("previewVisible", False)
        self._set("menuX", center[0] * self._scale)
        self._set("menuY", center[1] * self._scale)
        self._set("highlight", int(Highlight.NONE))
        self._update_region()
        self._window.show()
        if self._x11:
            self._window.raise_()
        self._update_region()
        self._set("animate", True)

    def select(
        self,
        highlight: Highlight,
        angle: float,
        target: Rect | None,
        monitor: Monitor | None = None,
    ) -> None:
        """Update the ring and move the preview to `target`.

        `angle` is the compass bearing of the highlighted segment in degrees
        clockwise from north; it only matters for `Highlight.SEGMENT`.
        `target` is local to `monitor`, which defaults to the monitor the
        ring is on.
        """
        if highlight is Highlight.SEGMENT:
            # Turn the short way round; jump instead when the segment appears
            # from nothing or would have to cross the whole ring.
            turn = (angle - self._angle + 180) % 360 - 180
            glide = self._highlight is Highlight.SEGMENT and abs(turn) < 179
            self._angle += turn
            self._set("angleAnimated", glide)
            self._set("angle", self._angle)
        self._highlight = highlight
        self._set("highlight", int(highlight))

        if target is None or not self._preview_visible:
            target = None
            self._set("previewVisible", False)
            self._hide_remote()
        elif monitor is not None and monitor.name != self._monitor:
            self._show_remote(monitor, preview=target.inset(self._preview.padding))
            target = None
            self._set("previewVisible", False)
        else:
            self._hide_remote()
            preview = target.inset(self._preview.padding)
            if not self._preview_shown:
                self._set("animate", False)
                self._set_preview_rect(self._window, self._starting_frame(preview), self._scale)
                self._set("animate", True)
            self._set_preview_rect(self._window, preview, self._scale)
            self._set("previewVisible", True)
        self._preview_shown = target is not None
        self._update_region()

    def hide(self) -> None:
        self._release_input()
        self._window.hide()
        self._hide_remote()

    def set_colors(self, accent: str | None, gradient: str | None, ring: str | None) -> None:
        """Recolor the ring and the preview border; None leaves a color as it is.

        For plugins (`PluginApi.set_colors`). The colors last until the
        service restarts.
        """
        if accent is not None:
            self._set("accent", QColor(accent))
            self._set("accent2", QColor(gradient or accent))
            if self._border_follows_accent:
                for window in (self._window, self._remote):
                    window.setProperty("previewBorderColor", QColor(accent))
        elif gradient is not None:
            self._set("accent2", QColor(gradient))
        if ring is not None:
            color = QColor(ring)
            color.setAlphaF(self._ring_alpha)
            self._set("ringColor", color)

    def _show_remote(self, monitor: Monitor, preview: Rect) -> None:
        """Show the preview on a monitor other than the ring's. It is not blurred."""
        remote = self._remote
        if self._remote_monitor != monitor.name:
            self._hide_remote()
            screen = _find_screen(monitor)
            if screen is None:
                return
            self._remote_scale = _scale(screen, monitor)
            size = (monitor.geometry.width, monitor.geometry.height)
            remote.setScreen(screen)
            remote.setGeometry(screen.geometry())
            start = self._starting_frame(preview, size)
            self._set_preview_rect(remote, start, self._remote_scale)
            remote.show()
            remote.setProperty("animate", True)
            self._remote_monitor = monitor.name
        self._set_preview_rect(remote, preview, self._remote_scale)
        remote.setProperty("previewVisible", True)

    def _hide_remote(self) -> None:
        self._remote.setProperty("animate", False)
        self._remote.setProperty("previewVisible", False)
        self._remote.hide()
        self._remote_monitor = None

    def _starting_frame(self, preview: Rect, other_screen: tuple[int, int] | None = None) -> Rect:
        """Return the frame the preview grows out of when it first appears.

        `other_screen` is the size of the monitor it appears on, if that is
        not the one with the ring.
        """
        screen = other_screen or self._screen_size
        match self._preview.start:
            case "radial_menu" if other_screen is None:
                return Rect(self._center[0], self._center[1], 1, 1)
            case "screen_center":
                return Rect(screen[0] // 2, screen[1] // 2, 1, 1)
            case _:
                width, height = round(preview.width * 0.8), round(preview.height * 0.8)
                return Rect(
                    preview.x + (preview.width - width) // 2,
                    preview.y + (preview.height - height) // 2,
                    width,
                    height,
                )

    def _set(self, name: str, value: object) -> None:
        self._window.setProperty(name, value)

    @staticmethod
    def _set_preview_rect(window: Any, rect: Rect, scale: float) -> None:
        window.setProperty("previewX", rect.x * scale)
        window.setProperty("previewY", rect.y * scale)
        window.setProperty("previewWidth", rect.width * scale)
        window.setProperty("previewHeight", rect.height * scale)

    def _ring_region(self, outer: float, inner: float) -> QRegion:
        """Return the ring between two radii, in the window's coordinates."""
        x, y = round(self._center[0] * self._scale), round(self._center[1] * self._scale)
        ellipse = QRegion.RegionType.Ellipse
        out, hole = round(outer), max(round(inner), 0)
        region = QRegion(x - out, y - out, 2 * out, 2 * out, ellipse)
        return region - QRegion(x - hole, y - hole, 2 * hole, 2 * hole, ellipse)

    def _preview_region(self, preview: QRectF) -> QRegion:
        path = QPainterPath()
        radius = self._preview.corner_radius
        path.addRoundedRect(preview, radius, radius)
        return QRegion(path.toFillPolygon().toPolygon())

    def _update_region(self) -> None:
        """Tell the compositor where to blur, or cut the window to shape."""
        if self._masked:
            self._apply_mask()
        elif self._blur is not None:
            self._apply_blur()

    def _apply_blur(self) -> None:
        assert self._blur is not None
        region = QRegion()
        if self._blur_menu:
            scale = _FILLED_SCALE if self._highlight is Highlight.FULL else 1.0
            region += self._ring_region(
                self._radius * scale, (self._radius - self._thickness) * scale
            )
        preview = self._preview_rect
        if self._blur_preview and preview is not None and preview.width() > 1:
            region += self._preview_region(preview)
        # One pixel in each of two opposite corners, always. KWin's blur keeps
        # buffers the size of the region's bounding box and rebuilds them all
        # whenever that size changes, which for a preview gliding from one
        # size to another is every frame. That alone took a third more of
        # the GPU's time than the same glide without blur. Pinned like this
        # the box is the whole window and never changes while the ring is
        # open, and blur costs next to nothing. It also keeps the region from
        # being empty, which would mean "blur the whole surface".
        size = self._window.size()
        region += QRegion(0, 0, 1, 1)
        region += QRegion(size.width() - 1, size.height() - 1, 1, 1)
        self._blur(self._window, region)

    def _apply_mask(self) -> None:
        """Without a compositor: show only the ring and the preview's border."""
        region = QRegion()
        if self._menu_visible:
            # Room for the ring at both of its sizes, so it is never clipped
            # while it shrinks or grows.
            region += self._ring_region(
                self._radius + 1, (self._radius - self._thickness) * _FILLED_SCALE - 1
            )
        preview = self._preview_rect
        if preview is not None and preview.width() > 1:
            border = max(self._preview.border_thickness, 2)
            inside = preview.adjusted(border, border, -border, -border)
            frame = self._preview_region(preview)
            if inside.isValid():
                frame -= self._preview_region(inside)
            region += frame
        if region.isEmpty():
            # An empty mask would mean "no mask": the whole window, in black.
            region = QRegion(0, 0, 1, 1)
        self._window.setMask(region)

    # X11 only: an unmanaged window is never focused, so the keys are grabbed
    # and read from the window itself instead of from the item with focus.
    # The pointer only needs grabbing while the window is cut to shape, as
    # it would otherwise be seen over the ring alone.

    def _grab_input(self) -> None:
        if self._grabbed or not self._window.isVisible():
            return
        self._grabbed = bool(self._window.setKeyboardGrabEnabled(True))
        if not self._grabbed:
            log.info("the keyboard is in use elsewhere; keys will not select actions")
        if self._masked:
            self._window.setMouseGrabEnabled(True)

    def _release_input(self) -> None:
        if not self._x11:
            return
        self._window.setKeyboardGrabEnabled(False)
        self._window.setMouseGrabEnabled(False)
        self._grabbed = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Expose:
            # Only now is the window mapped, which a grab needs.
            self._grab_input()
        elif kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            assert isinstance(event, QKeyEvent)
            if not event.isAutoRepeat():
                if kind == QEvent.Type.KeyPress:
                    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self.key_pressed.emit(event.key(), shift)
                else:
                    self.key_released.emit(event.key(), event.nativeScanCode())
            return True
        return False

    # Called from QML.

    @Slot(float, float)
    def pointerMoved(self, x: float, y: float) -> None:  # noqa: N802
        # Sizes are the same in both units, places are not: report the
        # distance from the ring as it is, from the ring's place in the
        # backend's units.
        scale = self._scale
        self.pointer_moved.emit(
            self._center[0] + x - self._center[0] * scale,
            self._center[1] + y - self._center[1] * scale,
        )

    @Slot(int, bool)
    def keyPressed(self, key: int, shift: bool) -> None:  # noqa: N802
        self.key_pressed.emit(key, shift)

    @Slot(int, int)
    def keyReleased(self, key: int, scan_code: int) -> None:  # noqa: N802
        self.key_released.emit(key, scan_code)

    @Slot(bool)
    def mouseClicked(self, secondary: bool) -> None:  # noqa: N802
        self.clicked.emit(secondary)

    @Slot()
    def _before_frame(self) -> None:
        item = self._preview_item
        if item is not None:
            self.previewMoved(item.x(), item.y(), item.width(), item.height(), item.opacity() > 0.4)

    @Slot(float, float, float, float, bool)
    def previewMoved(self, x: float, y: float, width: float, height: float, shown: bool) -> None:  # noqa: N802
        """Keep the blurred region glued to the preview while it animates."""
        rect = QRectF(x, y, width, height) if shown else None
        if rect == self._preview_rect:
            # Asked every frame; most of them the preview has not moved.
            return
        self._preview_rect = rect
        if self._blur_preview or self._masked:
            self._update_region()
