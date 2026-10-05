"""Python side of the overlay window.

The overlay is one transparent layer-shell surface covering the monitor under
the cursor. It is drawn in QML (`Overlay.qml`, `RadialMenu.qml`,
`Preview.qml`); this class feeds it state and forwards its input events. A
second surface (`RemotePreview.qml`) shows the preview when the target is on
another monitor.

Sizes, colors and animation curves follow Loop's RadialMenuView, PreviewView
and AnimationConfiguration.
"""

import ctypes
import enum
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRectF, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainterPath, QRegion
from PySide6.QtQml import QQmlApplicationEngine

from ring.actions import Rect
from ring.backends.base import Monitor
from ring.config import ANIMATIONS, Config

log = logging.getLogger("ring")

_QML = Path(__file__).parent / "Overlay.qml"
_REMOTE_QML = Path(__file__).parent / "RemotePreview.qml"

_ANGLE_MS = 200
_APPEAR_MS = 100
# The ring shrinks to this scale while it is lit up as a whole.
_FILLED_SCALE = 0.85


class OverlayError(RuntimeError):
    """The overlay window could not be created."""


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


class Overlay(QObject):
    """The overlay window. Coordinates are local to the monitor it is shown on."""

    pointer_moved = Signal(float, float)
    key_pressed = Signal(int, bool)
    key_released = Signal(int, int)
    clicked = Signal(bool)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._radius = config.radial.radius
        self._thickness = config.radial.thickness
        self._preview = config.preview
        self._center = (0, 0)
        self._screen_size = (0, 0)
        # The monitor the ring is on, and the other one showing the preview.
        self._monitor: str | None = None
        self._remote_monitor: str | None = None
        self._angle = 0.0
        self._highlight = Highlight.NONE
        self._preview_shown = False
        self._preview_rect: QRectF | None = None
        wants_blur = config.theme.blur or config.preview.blur
        self._blur = _load_blur() if wants_blur else None
        self._menu_visible = config.radial.visible
        self._preview_visible = config.preview.visible
        self._blur_menu = config.theme.blur and self._blur is not None and self._menu_visible
        self._blur_preview = config.preview.blur and self._blur is not None
        if wants_blur and self._blur is None:
            log.info("background blur is not available on this system")

        self._engine = QQmlApplicationEngine()
        self._engine.rootContext().setContextProperty("bridge", self)
        self._engine.load(QUrl.fromLocalFile(str(_QML)))
        self._engine.load(QUrl.fromLocalFile(str(_REMOTE_QML)))
        roots = self._engine.rootObjects()
        if len(roots) < 2:
            raise OverlayError(
                "Could not create the overlay window. It needs the LayerShellQt QML module "
                "(Arch: layer-shell-qt) and the distribution's PySide6 (Arch: pyside6)."
            )
        self._window, self._remote = roots[0], roots[1]

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
        screens = QGuiApplication.screens()
        screen = next((s for s in screens if s.name() == monitor.name), None)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
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
        self._set("menuX", center[0])
        self._set("menuY", center[1])
        self._set("highlight", int(Highlight.NONE))
        self._window.show()
        self._apply_blur()
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
                self._set_preview_rect(self._window, self._starting_frame(preview))
                self._set("animate", True)
            self._set_preview_rect(self._window, preview)
            self._set("previewVisible", True)
        self._preview_shown = target is not None
        self._apply_blur()

    def hide(self) -> None:
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
            screen = next((s for s in QGuiApplication.screens() if s.name() == monitor.name), None)
            if screen is None:
                return
            size = (monitor.geometry.width, monitor.geometry.height)
            remote.setScreen(screen)
            remote.setGeometry(screen.geometry())
            self._set_preview_rect(remote, self._starting_frame(preview, size))
            remote.show()
            remote.setProperty("animate", True)
            self._remote_monitor = monitor.name
        self._set_preview_rect(remote, preview)
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
    def _set_preview_rect(window: Any, rect: Rect) -> None:
        window.setProperty("previewX", rect.x)
        window.setProperty("previewY", rect.y)
        window.setProperty("previewWidth", rect.width)
        window.setProperty("previewHeight", rect.height)

    def _apply_blur(self) -> None:
        if self._blur is None:
            return
        region = QRegion()
        if self._blur_menu:
            x, y = self._center
            scale = _FILLED_SCALE if self._highlight is Highlight.FULL else 1.0
            outer = round(self._radius * scale)
            inner = round((self._radius - self._thickness) * scale)
            ellipse = QRegion.RegionType.Ellipse
            region += QRegion(x - outer, y - outer, 2 * outer, 2 * outer, ellipse)
            region -= QRegion(x - inner, y - inner, 2 * inner, 2 * inner, ellipse)
        preview = self._preview_rect
        if self._blur_preview and preview is not None and preview.width() > 1:
            path = QPainterPath()
            radius = self._preview.corner_radius
            path.addRoundedRect(preview, radius, radius)
            region += QRegion(path.toFillPolygon().toPolygon())
        if region.isEmpty():
            # An empty region would mean "blur the whole surface".
            region = QRegion(-1, -1, 1, 1)
        self._blur(self._window, region)

    # Called from QML.

    @Slot(float, float)
    def pointerMoved(self, x: float, y: float) -> None:  # noqa: N802
        self.pointer_moved.emit(x, y)

    @Slot(int, bool)
    def keyPressed(self, key: int, shift: bool) -> None:  # noqa: N802
        self.key_pressed.emit(key, shift)

    @Slot(int, int)
    def keyReleased(self, key: int, scan_code: int) -> None:  # noqa: N802
        self.key_released.emit(key, scan_code)

    @Slot(bool)
    def mouseClicked(self, secondary: bool) -> None:  # noqa: N802
        self.clicked.emit(secondary)

    @Slot(float, float, float, float, bool)
    def previewMoved(self, x: float, y: float, width: float, height: float, shown: bool) -> None:  # noqa: N802
        """Keep the blurred region glued to the preview while it animates."""
        self._preview_rect = QRectF(x, y, width, height) if shown else None
        if self._blur_preview:
            self._apply_blur()
