"""The system tray icon: pause Ring, open the settings, see the used counter."""

import os
import subprocess
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ring.config import Config
from ring.stats import Stats


def ring_icon(accent: QColor, *, paused: bool = False) -> QIcon:
    """Draw Ring's icon: a ring with one lit segment, all grey while paused."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    circle = QRectF(11, 11, 42, 42)
    pen = QPen(QColor(140, 140, 140, 110 if paused else 200), 13)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    painter.drawEllipse(circle)
    if not paused:
        pen.setColor(accent)
        painter.setPen(pen)
        # Sixteenths of a degree, counter-clockwise from east: the upper right.
        painter.drawArc(circle, 10 * 16, 70 * 16)
    painter.end()
    return QIcon(pixmap)


def open_settings() -> None:
    """Start the settings window as its own program."""
    environment = dict(os.environ)
    # The service turns its own windows into overlay surfaces; a normal
    # application window must not inherit that.
    environment.pop("QT_WAYLAND_SHELL_INTEGRATION", None)
    environment.pop("QT_QPA_PLATFORM", None)
    subprocess.Popen(
        [sys.executable, "-m", "ring", "settings"],
        env=environment,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )


class Tray(QObject):
    """The tray icon and its menu."""

    def __init__(
        self,
        config: Config,
        stats: Stats,
        set_paused: Callable[[bool], None],
        quit_ring: Callable[[], None],
    ) -> None:
        super().__init__()
        self._stats = stats
        self._set_paused = set_paused
        self.paused = False
        self._accent = QGuiApplication.palette().highlight().color()
        if config.theme.accent_color != "system":
            self._accent = QColor(config.theme.accent_color)

        self.menu = QMenu()
        self.counter = QAction(self.menu)
        self.counter.setEnabled(False)
        self.pause = QAction("Pause Ring", self.menu)
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self._toggled)
        settings = QAction("Settings…", self.menu)
        settings.triggered.connect(lambda: open_settings())
        leave = QAction("Quit Ring", self.menu)
        leave.triggered.connect(lambda: quit_ring())
        self.menu.addAction(self.counter)
        self.menu.addSeparator()
        self.menu.addAction(self.pause)
        self.menu.addAction(settings)
        self.menu.addSeparator()
        self.menu.addAction(leave)
        self.menu.aboutToShow.connect(self.refresh)

        self.icon = QSystemTrayIcon(ring_icon(self._accent))
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.refresh()

    def show(self) -> bool:
        """Put the icon in the tray; False if this desktop has no tray."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.icon.show()
        return True

    def hide(self) -> None:
        self.icon.hide()

    def refresh(self) -> None:
        """Bring the counter and the tooltip up to date."""
        self._stats.reload()
        total = self._stats.total
        noun = "window" if total == 1 else "windows"
        self.counter.setText(f"{total:,} {noun} moved")
        state = "paused" if self.paused else f"{total:,} {noun} moved"
        self.icon.setToolTip(f"Ring — {state}")

    def set_paused(self, paused: bool) -> None:
        """Reflect a pause that was requested elsewhere, or request one."""
        self.pause.setChecked(paused)

    def _toggled(self, paused: bool) -> None:
        self.paused = paused
        self._set_paused(paused)
        self.icon.setIcon(ring_icon(self._accent, paused=paused))
        self.refresh()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            open_settings()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.pause.toggle()
