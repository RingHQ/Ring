"""The small parts the settings pages are built from."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeyEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ring.actions import (
    Action,
    ActionSpec,
    CycleAction,
    Direction,
    Leaf,
    action_name,
)
from ring.config import (
    Chord,
    Config,
)
from ring.input.keys import evdev_name
from ring.plugins import REGISTRY

SECTOR_LABELS = {
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


def pretty(name: str) -> str:
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
            combo.addItem(pretty(name), name)
        if combo.findData(action_name(step)) < 0:
            # For instance the action of a plugin that is not loaded.
            combo.addItem(pretty(action_name(step)), action_name(step))
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
            self.setText(" + ".join(pretty(key) for key in sorted(self._chord)))
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


def spin(value: int, low: int, high: int, suffix: str = " px") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


def percent(value: float, high: int = 100) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, high)
    box.setDecimals(0)
    box.setSuffix(" %")
    box.setValue(value * 100)
    return box


BLUR_NOTE = "This feature is still being worked on, things may not work as expected."


def beta(control: QWidget, note: str) -> QWidget:
    """Return `control` with a BETA badge and a button that explains it."""
    badge = QLabel("BETA")
    badge.setObjectName("beta")
    info = QToolButton()
    info.setIcon(QIcon.fromTheme("help-about"))
    if info.icon().isNull():
        info.setText("i")
    info.setAutoRaise(True)
    info.setToolTip(note)
    info.setAccessibleName("About this beta feature")
    # A tooltip only shows on hover; a click should answer as well.
    info.clicked.connect(
        lambda: QToolTip.showText(info.mapToGlobal(info.rect().bottomLeft()), note, info)
    )
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(control)
    layout.addWidget(badge)
    layout.addWidget(info)
    layout.addStretch(1)
    return row


def combo(choices: dict[str, str], current: str) -> QComboBox:
    box = QComboBox()
    for value, label in choices.items():
        box.addItem(label, value)
    box.setCurrentIndex(max(box.findData(current), 0))
    return box


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


def scroll_page(title: str, subtitle: str, *widgets: QWidget) -> QWidget:
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


def style_sheet(palette: QPalette) -> str:
    """Build the window's style sheet from the desktop's colors."""
    text = palette.windowText().color()
    faded = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.62)"
    line = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.14)"
    return f"""
        QLabel#pageTitle {{ font-size: 20pt; font-weight: 600; }}
        QLabel#brand {{ font-size: 17pt; font-weight: 700; }}
        QLabel#cardTitle {{ font-weight: 600; }}
        QLabel#hint {{ color: {faded}; }}
        QLabel#beta {{
            color: palette(highlight);
            border: 1px solid palette(highlight);
            border-radius: 4px;
            padding: 0px 5px;
            font-size: 8pt;
            font-weight: 700;
        }}
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
