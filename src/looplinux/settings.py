"""The settings window: edit the configuration without touching the file.

Everything on screen is read from and written back to a `Config`. Applying
saves it as TOML and asks the running service to restart with it.
"""

import contextlib
import math
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from looplinux import __version__
from looplinux.actions import (
    FRACTIONS,
    SECTORS,
    Action,
    ActionSpec,
    CustomAction,
    CycleAction,
    Direction,
    Gaps,
    Leaf,
    action_name,
)
from looplinux.config import (
    ANIMATION_STYLES,
    PREVIEW_STARTS,
    ActionsConfig,
    AnimationConfig,
    Chord,
    Config,
    ConfigError,
    PreviewConfig,
    RadialConfig,
    StashConfig,
    ThemeConfig,
    TrayConfig,
    TriggerConfig,
    dump_config,
    load_config,
    parse_config,
    user_config_path,
)
from looplinux.input.keys import EVDEV_CODES, evdev_name
from looplinux.ipc import IpcError, send_command
from looplinux.plugins import REGISTRY, discover, load_plugins, plugin_directory
from looplinux.stats import Stats, default_stats_path

_SERVICE = "looplinux.service"
_SECTOR_LABELS = {
    Direction.CENTER: "Inside the ring",
    Direction.NORTH: "Up",
    Direction.NORTH_EAST: "Up right",
    Direction.EAST: "Right",
    Direction.SOUTH_EAST: "Down right",
    Direction.SOUTH: "Down",
    Direction.SOUTH_WEST: "Down left",
    Direction.WEST: "Left",
    Direction.NORTH_WEST: "Up left",
}


def _pretty(name: str) -> str:
    """Turn a config name such as KEY_RIGHTCTRL or left_half into a label."""
    if name in REGISTRY.actions:
        return f"{REGISTRY.actions[name].label} ({name.split('.')[0]})"
    text = name.removeprefix("KEY_")
    for side in ("LEFT", "RIGHT"):
        # KEY_RIGHTCTRL reads better as "Right ctrl".
        if text.startswith(side) and len(text) > len(side):
            text = f"{side} {text[len(side) :]}"
    return text.replace("_", " ").capitalize()


class ColorButton(QPushButton):
    """A swatch that opens a color picker."""

    changed = Signal()

    def __init__(self, color: str) -> None:
        super().__init__()
        self.setFixedWidth(72)
        self._color = color
        self.clicked.connect(self._pick)
        self._paint()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color
        self._paint()

    def _paint(self) -> None:
        self.setStyleSheet(f"background-color: {self._color}; border: 1px solid #80808080;")
        self.setToolTip(self._color)

    def _pick(self) -> None:
        picked = QColorDialog.getColor(QColor(self._color), self, "Pick a color")
        if picked.isValid():
            self.set_color(picked.name())
            self.changed.emit()


class ActionEdit(QWidget):
    """Pick one action, or several to make a cycle."""

    changed = Signal()

    def __init__(self, config: Config, spec: ActionSpec, *, vertical: bool = False) -> None:
        """Show `spec`; `vertical` lists the steps of a cycle below each other."""
        super().__init__()
        self._config = config
        self._names = [
            *(action.value for action in Action),
            *config.custom_actions,
            *REGISTRY.actions,
        ]
        self._combos: list[QComboBox] = []
        outer: QVBoxLayout | QHBoxLayout = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._steps: QVBoxLayout | QHBoxLayout = QVBoxLayout() if vertical else QHBoxLayout()
        buttons = QHBoxLayout()
        self._add = QToolButton(text="+", toolTip="Add a step: repeating the action cycles")
        self._remove = QToolButton(text="-", toolTip="Remove the last step")
        self._add.clicked.connect(lambda: self._append(Action.MAXIMIZE, notify=True))
        self._remove.clicked.connect(self._pop)
        buttons.addWidget(self._add)
        buttons.addWidget(self._remove)
        buttons.addStretch(1)
        outer.addLayout(self._steps, 0 if vertical else 1)
        outer.addLayout(buttons)
        steps = spec.steps if isinstance(spec, CycleAction) else (spec,)
        for step in steps:
            self._append(step, notify=False)

    def first(self) -> Leaf:
        """Return the first step, which is what an icon should show."""
        spec = self.spec()
        return spec.steps[0] if isinstance(spec, CycleAction) else spec

    def spec(self) -> ActionSpec:
        steps: list[Leaf] = []
        for combo in self._combos:
            leaf = self._config.leaf(combo.currentData())
            if leaf is not None and leaf is not Action.NONE:
                steps.append(leaf)
        if not steps:
            return Action.NONE
        return steps[0] if len(steps) == 1 else CycleAction(tuple(steps))

    def _append(self, step: Leaf, *, notify: bool) -> None:
        combo = QComboBox()
        for name in self._names:
            combo.addItem(_pretty(name), name)
        if combo.findData(action_name(step)) < 0:
            # For instance the action of a plugin that is not loaded.
            combo.addItem(_pretty(action_name(step)), action_name(step))
        combo.setCurrentIndex(combo.findData(action_name(step)))
        combo.setMaxVisibleItems(24)
        # Sized for a typical name, not for the longest one in the list.
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)
        combo.currentIndexChanged.connect(self.changed)
        self._combos.append(combo)
        self._steps.addWidget(combo)
        self._remove.setEnabled(len(self._combos) > 1)
        if notify:
            self.changed.emit()

    def _pop(self) -> None:
        if len(self._combos) > 1:
            self._combos.pop().deleteLater()
            self._remove.setEnabled(len(self._combos) > 1)
            self.changed.emit()


class KeyButton(QPushButton):
    """Shows a key or key combination; click it and press keys to change it."""

    changed = Signal()

    def __init__(self, chord: Chord) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setMinimumWidth(110)
        self._chord = chord
        self._held: set[str] = set()
        self._pressed: set[str] = set()
        self.toggled.connect(self._toggled)
        self._label()

    def chord(self) -> Chord:
        return self._chord

    def _label(self) -> None:
        if self.isChecked():
            self.setText("Press keys…")
        elif self._chord:
            self.setText(" + ".join(_pretty(key) for key in sorted(self._chord)))
        else:
            self.setText("Click to set")

    def _toggled(self, recording: bool) -> None:
        self._held.clear()
        self._pressed.clear()
        if recording:
            self.setFocus()
        self._label()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        name = evdev_name(event.key())
        if not self.isChecked() or name is None:
            super().keyPressEvent(event)
            return
        if not event.isAutoRepeat():
            self._held.add(name)
            self._pressed.add(name)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        name = evdev_name(event.key())
        if not self.isChecked() or name is None or event.isAutoRepeat():
            super().keyReleaseEvent(event)
            return
        self._held.discard(name)
        if not self._held and self._pressed:
            self._chord = frozenset(self._pressed)
            self.setChecked(False)
            self.changed.emit()


class SectorPicker(QWidget):
    """The ring, drawn large: click a direction to choose what it does."""

    picked = Signal(object)

    def __init__(self, first_step: Callable[[Direction], Leaf]) -> None:
        super().__init__()
        self._first_step = first_step
        self.selected = Direction.NORTH
        self.setFixedSize(300, 300)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _radii(self) -> tuple[float, float]:
        outer = min(self.width(), self.height()) / 2 - 6
        return outer, outer * 0.36

    def _direction_at(self, point: QPointF) -> Direction | None:
        outer, inner = self._radii()
        offset = point - QPointF(self.width() / 2, self.height() / 2)
        distance = (offset.x() ** 2 + offset.y() ** 2) ** 0.5
        if distance > outer:
            return None
        if distance < inner:
            return Direction.CENTER
        degrees = math.degrees(math.atan2(offset.x(), -offset.y())) % 360
        return SECTORS[int((degrees + 22.5) / 45) % len(SECTORS)]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        direction = self._direction_at(event.position())
        if direction is not None:
            self.selected = direction
            self.update()
            self.picked.emit(direction)

    def _icon(self, painter: QPainter, center: QPointF, leaf: Leaf, color: QColor) -> None:
        """Draw a tiny screen with the area the action snaps to filled in."""
        screen = QRectF(0, 0, 34, 23)
        screen.moveCenter(center)
        if leaf is Action.NONE:
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(screen, 3, 3)
            return
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(screen, 3, 3)
        region = leaf.region if isinstance(leaf, CustomAction) else None
        if isinstance(leaf, Action):
            region = FRACTIONS.get(leaf)
        if region is None:
            # No fixed area: show the first letters of its name instead.
            font = painter.font()
            font.setPointSizeF(6.5)
            font.setBold(True)
            painter.setFont(font)
            words = action_name(leaf).split(".")[-1].split("_")
            painter.drawText(
                screen, Qt.AlignmentFlag.AlignCenter, "".join(w[:1] for w in words)[:3].upper()
            )
            return
        inside = screen.adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(
                inside.x() + region.x * inside.width(),
                inside.y() + region.y * inside.height(),
                region.width * inside.width(),
                region.height * inside.height(),
            ),
            1.5,
            1.5,
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        accent, on_accent = palette.highlight().color(), palette.highlightedText().color()
        base, text = palette.base().color(), palette.text().color()
        edge = palette.mid().color()
        center = QPointF(self.width() / 2, self.height() / 2)
        outer, inner = self._radii()
        ring, hole = QRectF(0, 0, 2 * outer, 2 * outer), QRectF(0, 0, 2 * inner, 2 * inner)
        ring.moveCenter(center)
        hole.moveCenter(center)
        gap = 2.0
        for index, direction in enumerate(SECTORS):
            # Qt angles run counter-clockwise from east; bearings clockwise from north.
            start = 90 - index * 45 - 22.5 + gap / 2
            path = QPainterPath()
            path.arcMoveTo(ring, start)
            path.arcTo(ring, start, 45 - gap)
            path.arcTo(hole.adjusted(-5, -5, 5, 5), start + 45 - gap, -(45 - gap))
            path.closeSubpath()
            chosen = direction is self.selected
            painter.setPen(QPen(edge, 1))
            painter.setBrush(accent if chosen else base)
            painter.drawPath(path)
            bearing = math.radians(index * 45)
            middle = (outer + inner + 5) / 2
            spot = center + QPointF(math.sin(bearing) * middle, -math.cos(bearing) * middle)
            self._icon(painter, spot, self._first_step(direction), on_accent if chosen else text)
        chosen = self.selected is Direction.CENTER
        painter.setPen(QPen(edge, 1))
        painter.setBrush(accent if chosen else base)
        painter.drawEllipse(hole)
        self._icon(
            painter, center, self._first_step(Direction.CENTER), on_accent if chosen else text
        )
        painter.end()


class LookPreview(QWidget):
    """A miniature of the ring and the preview with the current colors."""

    def __init__(self, current: Callable[[], Config]) -> None:
        super().__init__()
        self._current = current
        self.setMinimumSize(260, 210)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        config = self._current()
        theme, look = config.theme, config.preview
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        # A stand-in for the desktop behind the overlay.
        desktop = QLinearGradient(area.topLeft(), area.bottomRight())
        desktop.setColorAt(0, QColor("#3b6ea5"))
        desktop.setColorAt(1, QColor("#7a4f8f"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(desktop))
        painter.drawRoundedRect(area, 8, 8)

        accent = self.palette().highlight().color()
        if theme.accent_color != "system":
            accent = QColor(theme.accent_color)
        second = QColor(theme.gradient_color) if theme.gradient_color else accent

        if look.visible:
            scale = 0.5
            target = QRectF(area.center().x(), area.top(), area.width() / 2, area.height() / 2)
            inset = look.padding * scale + look.border_thickness * scale / 2
            target = target.adjusted(inset, inset, -inset, -inset)
            fill = QColor(look.fill_color)
            fill.setAlphaF(look.fill_opacity)
            border = accent if look.border_color == "accent" else QColor(look.border_color)
            painter.setBrush(fill)
            painter.setPen(QPen(border, max(look.border_thickness * scale, 1)))
            radius = look.corner_radius * scale
            painter.drawRoundedRect(target, radius, radius)

        if config.radial.visible:
            outer = min(float(config.radial.radius), area.height() / 2 - 12)
            thickness = min(float(config.radial.thickness), outer - 2)
            middle = outer - thickness / 2
            circle = QRectF(0, 0, 2 * middle, 2 * middle)
            circle.moveCenter(area.center())
            ring = QColor(theme.ring_color)
            ring.setAlphaF(theme.ring_opacity)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_flat_pen(ring, thickness))
            painter.drawEllipse(circle)
            lit = QLinearGradient(circle.topLeft(), circle.bottomRight())
            lit.setColorAt(0, accent)
            lit.setColorAt(1, second)
            painter.setPen(_flat_pen(QBrush(lit), thickness))
            # Qt counts sixteenths of a degree counter-clockwise from east;
            # this is the segment pointing up right.
            painter.drawArc(circle, round((45 - 22.5) * 16), 45 * 16)
        painter.end()


def _flat_pen(paint: QColor | QBrush, width: float) -> QPen:
    pen = QPen(paint, width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pen


def _spin(value: int, low: int, high: int, suffix: str = " px") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


def _percent(value: float, high: int = 100) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, high)
    box.setDecimals(0)
    box.setSuffix(" %")
    box.setValue(value * 100)
    return box


def _combo(choices: dict[str, str], current: str) -> QComboBox:
    box = QComboBox()
    for value, label in choices.items():
        box.addItem(label, value)
    box.setCurrentIndex(max(box.findData(current), 0))
    return box


class SettingsWindow(QWidget):
    """The settings window."""

    def __init__(self, path: Path, config: Config, stats: Stats) -> None:
        super().__init__()
        self._path = path
        self._config = config
        self._stats = stats
        self._key_rows: list[tuple[KeyButton, ActionEdit, QWidget]] = []
        self.setWindowTitle("Ring Settings")
        self.resize(1000, 720)

        self._pages = QStackedWidget()
        self._sidebar = QListWidget()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(190)
        self._sidebar.setIconSize(QSize(20, 20))
        self._sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for title, icon, build in (
            ("General", "preferences-system", self._general_tab),
            ("Appearance", "preferences-desktop-color", self._look_tab),
            ("Ring", "draw-circle", self._ring_tab),
            ("Keys", "input-keyboard", self._keys_tab),
            ("Behavior", "preferences-system-windows", self._behavior_tab),
            ("Plugins", "preferences-plugin", self._plugins_tab),
            ("About", "help-about", self._about_tab),
        ):
            self._sidebar.addItem(QListWidgetItem(QIcon.fromTheme(icon), title))
            self._pages.addWidget(build())
        self._sidebar.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._sidebar.setCurrentRow(0)

        brand = QLabel("Ring")
        brand.setObjectName("brand")
        version = QLabel(f"Version {__version__}")
        version.setObjectName("hint")
        side = QVBoxLayout()
        side.setContentsMargins(0, 16, 0, 12)
        side.setSpacing(2)
        side.addWidget(brand)
        side.addWidget(version)
        side.addSpacing(12)
        side.addWidget(self._sidebar, 1)
        for label in (brand, version):
            label.setContentsMargins(20, 0, 0, 0)

        self._status = QLabel()
        self._status.setObjectName("hint")
        apply = QPushButton("Apply")
        apply.setObjectName("primary")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        defaults = QPushButton("Reset to defaults\u2026")
        defaults.clicked.connect(self._reset)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(20, 10, 20, 14)
        buttons.addWidget(defaults)
        buttons.addWidget(self._status, 1)
        buttons.addWidget(close)
        buttons.addWidget(apply)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._pages, 1)
        main.addLayout(buttons)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(side)
        layout.addLayout(main, 1)
        self.setStyleSheet(_style(self.palette()))

    # Tabs

    def _general_tab(self) -> QWidget:
        trigger = self._config.trigger
        self._use = _combo({"key": "A single key", "shortcut": "A keyboard shortcut"}, trigger.use)
        self._key = _combo({name: _pretty(name) for name in sorted(EVDEV_CODES)}, trigger.key)
        self._shortcut = QKeySequenceEdit(QKeySequence(trigger.shortcut))
        self._shortcut.setMaximumSequenceLength(1)
        self._use.currentIndexChanged.connect(self._sync_trigger)
        self._sync_trigger()
        hint = QLabel(
            "A single key keeps working in applications, so pick one you do not use for "
            "shortcuts. A keyboard shortcut is taken over completely while Ring runs."
        )
        hint.setWordWrap(True)

        box = Card("Trigger")
        form = QFormLayout(box.body)
        form.addRow("Hold to open the ring:", self._use)
        form.addRow("Key:", self._key)
        form.addRow("Shortcut:", self._shortcut)
        form.addRow(hint)

        self._style = _combo(
            {s: s.capitalize() for s in ANIMATION_STYLES}, self._config.animation.style
        )
        self._animate_windows = QCheckBox("Glide windows to their new place")
        self._animate_windows.setChecked(self._config.animation.windows)
        motion = Card("Animation")
        motion_form = QFormLayout(motion.body)
        motion_form.addRow("Style:", self._style)
        motion_form.addRow(self._animate_windows)

        self._tray = QCheckBox("Show an icon in the system tray")
        self._tray.setChecked(self._config.tray.visible)
        self._autostart = QCheckBox("Start Ring when I log in")
        self._autostart_known = _service_state("is-enabled") is not None
        self._autostart.setChecked(_service_state("is-enabled") == "enabled")
        self._autostart.setEnabled(self._autostart_known)
        if not self._autostart_known:
            self._autostart.setToolTip("The looplinux systemd user service is not installed.")
        system = Card("System")
        system_layout = QVBoxLayout(system.body)
        system_layout.addWidget(self._tray)
        system_layout.addWidget(self._autostart)

        return _page("General", "How Ring is triggered and how it moves.", box, motion, system)

    def _look_tab(self) -> QWidget:
        theme, look, radial = self._config.theme, self._config.preview, self._config.radial
        self._system_accent = QCheckBox("Follow the desktop accent color")
        self._system_accent.setChecked(theme.accent_color == "system")
        fallback = self.palette().highlight().color().name()
        self._accent = ColorButton(
            fallback if theme.accent_color == "system" else theme.accent_color
        )
        self._use_gradient = QCheckBox("Gradient to a second color")
        self._use_gradient.setChecked(bool(theme.gradient_color))
        self._gradient = ColorButton(theme.gradient_color or "#b45cff")

        colors = Card("Accent")
        grid = QGridLayout(colors.body)
        grid.addWidget(self._system_accent, 0, 0, 1, 2)
        grid.addWidget(QLabel("Accent color:"), 1, 0)
        grid.addWidget(self._accent, 1, 1)
        grid.addWidget(self._use_gradient, 2, 0)
        grid.addWidget(self._gradient, 2, 1)
        grid.setColumnStretch(2, 1)

        self._ring_visible = QCheckBox("Show the ring")
        self._ring_visible.setChecked(radial.visible)
        self._radius = _spin(radial.radius, 20, 200)
        self._thickness = _spin(radial.thickness, 4, 120)
        self._ring_color = ColorButton(theme.ring_color)
        self._ring_opacity = _percent(theme.ring_opacity)
        self._ring_blur = QCheckBox("Blur what is behind it")
        self._ring_blur.setChecked(theme.blur)
        ring = Card("Ring")
        ring_form = QFormLayout(ring.body)
        ring_form.addRow(self._ring_visible)
        ring_form.addRow("Radius:", self._radius)
        ring_form.addRow("Thickness:", self._thickness)
        ring_form.addRow("Color:", self._ring_color)
        ring_form.addRow("Opacity:", self._ring_opacity)
        ring_form.addRow(self._ring_blur)

        self._preview_visible = QCheckBox("Show the preview")
        self._preview_visible.setChecked(look.visible)
        self._own_border = QCheckBox("Border in its own color")
        self._own_border.setChecked(look.border_color != "accent")
        self._border_color = ColorButton(
            "#ffffff" if look.border_color == "accent" else look.border_color
        )
        self._border = _spin(look.border_thickness, 0, 40)
        self._corner = _spin(look.corner_radius, 0, 80)
        self._padding = _spin(look.padding, 0, 100)
        self._fill_color = ColorButton(look.fill_color)
        self._fill_opacity = _percent(look.fill_opacity)
        self._preview_blur = QCheckBox("Blur what is behind it")
        self._preview_blur.setChecked(look.blur)
        self._start = _combo(
            {
                "action_center": "Its own center",
                "radial_menu": "The ring",
                "screen_center": "The center of the screen",
            },
            look.start,
        )
        assert set(PREVIEW_STARTS) == {self._start.itemData(i) for i in range(self._start.count())}
        border_row = QHBoxLayout()
        border_row.addWidget(self._own_border)
        border_row.addWidget(self._border_color)
        border_row.addStretch(1)
        preview = Card("Preview")
        preview_form = QFormLayout(preview.body)
        preview_form.addRow(self._preview_visible)
        preview_form.addRow(border_row)
        preview_form.addRow("Border thickness:", self._border)
        preview_form.addRow("Corner radius:", self._corner)
        preview_form.addRow("Padding:", self._padding)
        preview_form.addRow("Fill color:", self._fill_color)
        preview_form.addRow("Fill opacity:", self._fill_opacity)
        preview_form.addRow("Grows out of:", self._start)
        preview_form.addRow(self._preview_blur)

        self._look_preview = LookPreview(self._look_config)
        for widget in (self._system_accent, self._use_gradient, self._ring_visible,
                       self._preview_visible, self._own_border):  # fmt: skip
            widget.toggled.connect(self._look_changed)
        for button in (self._accent, self._gradient, self._ring_color,
                       self._border_color, self._fill_color):  # fmt: skip
            button.changed.connect(self._look_changed)
        for box in (self._radius, self._thickness, self._ring_opacity, self._border,
                    self._corner, self._padding, self._fill_opacity):  # fmt: skip
            box.valueChanged.connect(self._look_changed)
        self._look_changed()

        look = Card("Live preview")
        look_layout = QVBoxLayout(look.body)
        look_layout.addWidget(self._look_preview)
        left = QVBoxLayout()
        left.addWidget(look)
        left.addWidget(colors)
        left.addWidget(ring)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(preview)
        right.addStretch(1)
        columns = QWidget()
        row = QHBoxLayout(columns)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        return _page("Appearance", "Colors and shapes of the ring and the preview.", columns)

    def _ring_tab(self) -> QWidget:
        self._sectors: dict[Direction, ActionEdit] = {}
        self._sector_editors = QStackedWidget()
        for direction in _SECTOR_LABELS:
            spec = self._config.radial.sectors.get(direction, Action.NONE)
            edit = ActionEdit(self._config, spec, vertical=True)
            self._sectors[direction] = edit
            self._sector_editors.addWidget(edit)
        self._picker = SectorPicker(lambda direction: self._sectors[direction].first())
        for edit in self._sectors.values():
            edit.changed.connect(self._picker.update)
        self._sector_name = QLabel()
        self._sector_name.setObjectName("cardTitle")
        self._picker.picked.connect(self._show_sector)
        self._show_sector(self._picker.selected)

        hint = QLabel(
            "Click a part of the ring, then choose what it does. Add steps with + to make "
            "a cycle: a left click, or pointing there again once the window is snapped, "
            "moves on to the next step."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        editor = QVBoxLayout()
        editor.addWidget(self._sector_name)
        editor.addWidget(self._sector_editors)
        editor.addSpacing(8)
        editor.addWidget(hint)
        editor.addStretch(1)
        card = Card("Directions")
        row = QHBoxLayout(card.body)
        row.setSpacing(24)
        row.addWidget(self._picker)
        row.addLayout(editor, 1)
        return _page("Ring", "What each direction of the ring does.", card)

    def _show_sector(self, direction: Direction) -> None:
        self._sector_name.setText(_SECTOR_LABELS[direction])
        self._sector_editors.setCurrentWidget(self._sectors[direction])

    def _keys_tab(self) -> QWidget:
        self._keys_layout = QVBoxLayout()
        self._keys_layout.setSpacing(6)
        bindings = sorted(
            self._config.keybindings.items(), key=lambda item: (len(item[0]), sorted(item[0]))
        )
        for chord, spec in bindings:
            if spec is not Action.NONE:
                self._add_key_row(chord, spec)
        add = QPushButton("Add a key")
        add.clicked.connect(lambda: self._add_key_row(frozenset(), Action.MAXIMIZE))
        card = Card("While the trigger is held")
        layout = QVBoxLayout(card.body)
        layout.addLayout(self._keys_layout)
        layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        return _page(
            "Keys",
            "Click a key to change it; press two keys together for a combination.",
            card,
        )

    def _behavior_tab(self) -> QWidget:
        config = self._config
        self._outer = _spin(config.gaps.outer, 0, 200)
        self._inner = _spin(config.gaps.inner, 0, 200)
        gaps = Card("Gaps")
        gaps_form = QFormLayout(gaps.body)
        gaps_form.addRow("Around the screen edge:", self._outer)
        gaps_form.addRow("Between windows:", self._inner)

        self._margin = _percent(config.actions.almost_maximize_margin, 49)
        self._increment = _spin(config.actions.size_increment, 1, 500)
        self._restart = QCheckBox("Always start a cycle at its first action")
        self._restart.setChecked(config.actions.cycle_restart)
        self._backwards = QCheckBox("Shift steps backwards through a cycle")
        self._backwards.setChecked(config.actions.cycle_backwards_on_shift)
        actions = Card("Actions")
        actions_form = QFormLayout(actions.body)
        actions_form.addRow("Almost maximize leaves free, per side:", self._margin)
        actions_form.addRow("Step for grow, shrink and move:", self._increment)
        actions_form.addRow(self._restart)
        actions_form.addRow(self._backwards)

        self._peek = _spin(config.stash.peek, 1, 200)
        self._stash_animate = QCheckBox("Slide in and out")
        self._stash_animate.setChecked(config.stash.animate)
        self._stash_focus = QCheckBox("Focus a stashed window when it slides out")
        self._stash_focus.setChecked(config.stash.shift_focus)
        stash = Card("Stashed windows")
        stash_form = QFormLayout(stash.body)
        stash_form.addRow("Strip left visible:", self._peek)
        stash_form.addRow(self._stash_animate)
        stash_form.addRow(self._stash_focus)

        self._excluded = QLineEdit(", ".join(config.excluded_apps))
        self._excluded.setPlaceholderText("for example: steam, org.kde.plasmashell")
        excluded = Card("Applications Ring never touches")
        excluded_layout = QVBoxLayout(excluded.body)
        excluded_layout.addWidget(self._excluded)
        excluded_layout.addWidget(QLabel("Window classes, separated by commas."))

        return _page(
            "Behavior", "Gaps, steps, stashing and exceptions.", gaps, actions, stash, excluded
        )

    def _plugins_tab(self) -> QWidget:
        directory = plugin_directory(self._path.parent)
        found = {plugin.name: plugin for plugin in discover(directory)}
        self._plugin_boxes: dict[str, QCheckBox] = {}
        card = Card("Installed plugins")
        layout = QVBoxLayout(card.body)
        for name in sorted({*found, *self._config.enabled_plugins}):
            plugin = found.get(name)
            box = QCheckBox(name)
            box.setChecked(name in self._config.enabled_plugins)
            self._plugin_boxes[name] = box
            layout.addWidget(box)
            if plugin is None:
                detail = "Not found. It stays in the list until you untick it."
            elif name in REGISTRY.errors:
                detail = f"Could not be loaded: {REGISTRY.errors[name]}"
            else:
                detail = plugin.description or plugin.source
            note = QLabel(detail)
            note.setObjectName("hint")
            note.setWordWrap(True)
            note.setContentsMargins(26, 0, 0, 8)
            layout.addWidget(note)
        if not self._plugin_boxes:
            empty = QLabel("No plugins found.")
            empty.setObjectName("hint")
            layout.addWidget(empty)

        hint = QLabel(
            "Plugins add actions to Ring and can react to what it does. They are Python "
            "files in the plugins folder and run with your full permissions, so only tick "
            "plugins you trust. Their actions show up in the Ring and Keys pages after "
            "you apply and reopen this window."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        folder = QPushButton("Open the plugins folder")

        def open_folder() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

        folder.clicked.connect(open_folder)
        about = Card("About plugins")
        about_layout = QVBoxLayout(about.body)
        about_layout.addWidget(hint)
        about_layout.addWidget(folder, 0, Qt.AlignmentFlag.AlignLeft)
        return _page("Plugins", "Extend Ring with your own actions.", card, about)

    def _about_tab(self) -> QWidget:
        self._count = QLabel()
        self._count.setObjectName("counter")
        self._count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_caption = QLabel()
        self._count_caption.setObjectName("hint")
        self._count_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._top = QGridLayout()
        self._top.setColumnStretch(1, 1)
        self._top.setHorizontalSpacing(12)
        self._top_rows: list[tuple[str, int]] = []
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._show_stats)
        reset = QPushButton("Reset the counter\u2026")
        reset.clicked.connect(self._reset_stats)
        counter_buttons = QHBoxLayout()
        counter_buttons.addStretch(1)
        counter_buttons.addWidget(refresh)
        counter_buttons.addWidget(reset)
        counter = Card("Used counter")
        counter_layout = QVBoxLayout(counter.body)
        counter_layout.addWidget(self._count)
        counter_layout.addWidget(self._count_caption)
        counter_layout.addSpacing(10)
        counter_layout.addLayout(self._top)
        counter_layout.addLayout(counter_buttons)
        self._show_stats()

        text = QLabel(
            f"<b>Ring</b> {__version__}<br>A radial window snapper for Linux, ported from "
            '<a href="https://github.com/MrKai77/Loop">Loop</a> for macOS.<br>'
            "Licensed under the GNU GPL v3."
        )
        text.setOpenExternalLinks(True)
        location = QLabel(f"Settings file: {self._path}")
        location.setObjectName("hint")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about = Card("About")
        about_layout = QVBoxLayout(about.body)
        about_layout.addWidget(text)
        about_layout.addWidget(location)
        return _page("About", "How much Ring has done for you.", counter, about)

    # Behavior

    def _sync_trigger(self) -> None:
        single = self._use.currentData() == "key"
        self._key.setEnabled(single)
        self._shortcut.setEnabled(not single)

    def _look_changed(self) -> None:
        self._accent.setEnabled(not self._system_accent.isChecked())
        self._gradient.setEnabled(self._use_gradient.isChecked())
        self._border_color.setEnabled(self._own_border.isChecked())
        self._look_preview.update()

    def _add_key_row(self, chord: Chord, spec: ActionSpec) -> None:
        key = KeyButton(chord)
        action = ActionEdit(self._config, spec)
        remove = QToolButton(text="✕", toolTip="Remove this key")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(key)
        layout.addWidget(action, 1)
        layout.addWidget(remove)
        entry = (key, action, row)
        self._key_rows.append(entry)

        def drop() -> None:
            self._key_rows.remove(entry)
            row.deleteLater()

        remove.clicked.connect(drop)
        self._keys_layout.addWidget(row)
        if not chord:
            key.setChecked(True)

    def _show_stats(self) -> None:
        self._stats.reload()
        total = self._stats.total
        self._count.setText(f"{total:,}")
        noun = "window" if total == 1 else "windows"
        self._count_caption.setText(f"{noun} moved with Ring since {self._stats.since}")
        while (item := self._top.takeAt(0)) is not None:
            if item.widget() is not None:
                item.widget().deleteLater()
        self._top_rows = self._stats.top(6)
        most = self._top_rows[0][1] if self._top_rows else 1
        for row, (name, count) in enumerate(self._top_rows):
            bar = QProgressBar()
            bar.setRange(0, most)
            bar.setValue(count)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            number = QLabel(f"{count:,}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._top.addWidget(QLabel(_pretty(name)), row, 0)
            self._top.addWidget(bar, row, 1)
            self._top.addWidget(number, row, 2)

    def _reset_stats(self) -> None:
        answer = QMessageBox.question(self, "Ring", "Set the used counter back to zero?")
        if answer == QMessageBox.StandardButton.Yes:
            self._stats.reset()
            self._show_stats()

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self, "Ring", "Put every setting back to its default? Nothing is saved until you apply."
        )
        if answer == QMessageBox.StandardButton.Yes:
            fresh = SettingsWindow(self._path, Config(), self._stats)
            fresh.setGeometry(self.geometry())
            fresh._status.setText("Defaults loaded; press Apply to keep them.")
            fresh.show()
            # Keep the new window alive after this one closes.
            QApplication.instance().setProperty("ring_window", fresh)
            self.close()

    def _look_config(self) -> Config:
        """Return the configuration with the Appearance tab's current values."""
        theme = ThemeConfig(
            accent_color="system" if self._system_accent.isChecked() else self._accent.color(),
            gradient_color=self._gradient.color() if self._use_gradient.isChecked() else "",
            ring_color=self._ring_color.color(),
            ring_opacity=round(self._ring_opacity.value() / 100, 2),
            blur=self._ring_blur.isChecked(),
        )
        preview = PreviewConfig(
            visible=self._preview_visible.isChecked(),
            padding=self._padding.value(),
            corner_radius=self._corner.value(),
            border_thickness=self._border.value(),
            border_color=self._border_color.color() if self._own_border.isChecked() else "accent",
            fill_color=self._fill_color.color(),
            fill_opacity=round(self._fill_opacity.value() / 100, 2),
            blur=self._preview_blur.isChecked(),
            start=self._start.currentData(),
        )
        radial = replace(
            self._config.radial,
            visible=self._ring_visible.isChecked(),
            radius=self._radius.value(),
            thickness=self._thickness.value(),
        )
        return replace(self._config, theme=theme, preview=preview, radial=radial)

    def current(self) -> Config:
        """Build the configuration shown in the window."""
        config = self._look_config()
        sectors = {direction: edit.spec() for direction, edit in self._sectors.items()}
        # A default binding that was removed has to be switched off explicitly.
        keybindings: dict[Chord, ActionSpec] = dict.fromkeys(Config().keybindings, Action.NONE)
        for key, action, _ in self._key_rows:
            if key.chord():
                keybindings[key.chord()] = action.spec()
        shortcut = self._shortcut.keySequence().toString() or self._config.trigger.shortcut
        apps = tuple(app.strip() for app in self._excluded.text().split(",") if app.strip())
        return replace(
            config,
            trigger=TriggerConfig(
                use=self._use.currentData(),
                key=self._key.currentData(),
                shortcut=shortcut,
                cancel_key=self._config.trigger.cancel_key,
            ),
            radial=RadialConfig(
                visible=config.radial.visible,
                radius=config.radial.radius,
                thickness=config.radial.thickness,
                sectors=sectors,
            ),
            keybindings=keybindings,
            gaps=Gaps(outer=self._outer.value(), inner=self._inner.value()),
            actions=ActionsConfig(
                almost_maximize_margin=round(self._margin.value() / 100, 2),
                size_increment=self._increment.value(),
                cycle_restart=self._restart.isChecked(),
                cycle_backwards_on_shift=self._backwards.isChecked(),
            ),
            animation=AnimationConfig(
                style=self._style.currentData(), windows=self._animate_windows.isChecked()
            ),
            stash=StashConfig(
                peek=self._peek.value(),
                animate=self._stash_animate.isChecked(),
                shift_focus=self._stash_focus.isChecked(),
            ),
            tray=TrayConfig(visible=self._tray.isChecked()),
            excluded_apps=apps,
            enabled_plugins=tuple(
                name for name, box in self._plugin_boxes.items() if box.isChecked()
            ),
        )

    def apply(self) -> None:
        """Save the settings and make the running service pick them up."""
        try:
            text = dump_config(self.current())
            # Whatever is written must load again.
            parse_config(tomllib.loads(text))
        except (ConfigError, ValueError) as error:
            QMessageBox.warning(self, "Ring", f"These settings cannot be saved:\n\n{error}")
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Ring", f"Cannot write {self._path}:\n\n{error.strerror}")
            return
        if self._autostart_known:
            _service_state("enable" if self._autostart.isChecked() else "disable")
        try:
            answer = send_command("reload")
        except IpcError:
            self._status.setText("Saved. Ring is not running right now.")
            return
        if answer == "ok":
            self._status.setText("Saved. Ring restarted with the new settings.")
        else:
            self._status.setText("Saved. Restart Ring to use the new settings.")


class Card(QFrame):
    """A titled panel; put the content into `body`."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("card")
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        self.body = QWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(self.body)
        if self.body.layout() is not None:
            self.body.layout().setContentsMargins(0, 0, 0, 0)


def _page(title: str, subtitle: str, *widgets: QWidget) -> QWidget:
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    caption = QLabel(subtitle)
    caption.setObjectName("hint")
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 22, 28, 20)
    layout.setSpacing(14)
    layout.addWidget(heading)
    layout.addWidget(caption)
    layout.addSpacing(4)
    for widget in widgets:
        if isinstance(widget, Card) and widget.body.layout() is not None:
            widget.body.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
    layout.addStretch(1)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(page)
    return area


def _style(palette: QPalette) -> str:
    """Build the window's style sheet from the desktop's colors."""
    text = palette.windowText().color()
    faded = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.62)"
    line = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.14)"
    return f"""
        QLabel#pageTitle {{ font-size: 20pt; font-weight: 600; }}
        QLabel#brand {{ font-size: 17pt; font-weight: 700; }}
        QLabel#cardTitle {{ font-weight: 600; }}
        QLabel#hint {{ color: {faded}; }}
        QLabel#counter {{ font-size: 44pt; font-weight: 700; }}
        QFrame#card {{
            background: palette(base);
            border: 1px solid {line};
            border-radius: 12px;
        }}
        QListWidget#sidebar {{ background: transparent; border: none; outline: 0; }}
        QListWidget#sidebar::item {{ padding: 9px 12px; margin: 2px 10px; border-radius: 8px; }}
        QListWidget#sidebar::item:hover {{ background: {line}; }}
        QListWidget#sidebar::item:selected {{
            background: palette(highlight);
            color: palette(highlighted-text);
        }}
        QPushButton#primary {{ font-weight: 600; padding-left: 18px; padding-right: 18px; }}
        QProgressBar {{ border: none; border-radius: 4px; background: {line}; }}
        QProgressBar::chunk {{ border-radius: 4px; background: palette(highlight); }}
    """


def _service_state(verb: str) -> str | None:
    """Run `systemctl --user <verb>` on the service; None if it is not installed."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["systemctl", "--user", verb, _SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = result.stdout.strip()
        if verb != "is-enabled":
            return state
        return state if state in ("enabled", "disabled") else None
    return None


def run_settings(path: Path | None = None) -> int:
    """Show the settings window and run until it is closed."""
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Ring Settings")
    app.setDesktopFileName("looplinux-settings")
    target = path or user_config_path()
    try:
        config = load_config(target) if target.exists() else Config()
    except ConfigError as error:
        QMessageBox.warning(
            None,
            "Ring",
            f"The settings file has a problem, so the defaults are shown instead:\n\n{error}",
        )
        config = Config()
    # Loaded so that their actions can be offered; same trust as the service.
    load_plugins(config.enabled_plugins, plugin_directory(target.parent))
    window = SettingsWindow(target, config, Stats(default_stats_path()))
    window.show()
    return int(app.exec())
