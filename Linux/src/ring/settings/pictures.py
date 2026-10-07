"""The two widgets of the settings window that are painted by hand."""

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QWidget,
)

from ring.actions import (
    FRACTIONS,
    SECTORS,
    Action,
    CustomAction,
    Direction,
    Leaf,
    action_name,
)
from ring.config import (
    Config,
)


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
            radius = look.corner_radius * scale
            if look.style == "liquid_glass":
                paint_glass(painter, target, radius, fill, border)
            else:
                painter.setBrush(fill)
                painter.setPen(QPen(border, max(look.border_thickness * scale, 1)))
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
            painter.setPen(flat_pen(ring, thickness))
            painter.drawEllipse(circle)
            lit = QLinearGradient(circle.topLeft(), circle.bottomRight())
            lit.setColorAt(0, accent)
            lit.setColorAt(1, second)
            painter.setPen(flat_pen(QBrush(lit), thickness))
            # Qt counts sixteenths of a degree counter-clockwise from east;
            # this is the segment pointing up right.
            painter.drawArc(circle, round((45 - 22.5) * 16), 45 * 16)
        painter.end()


def paint_glass(
    painter: QPainter, target: QRectF, radius: float, tint: QColor, accent: QColor
) -> None:
    """Draw the Liquid Glass preview in small, after overlay/Preview.qml."""
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for step in range(3):
        halo = QColor(accent)
        halo.setAlphaF(0.34 * 0.58**step)
        grown = target.adjusted(-step - 1, -step - 1, step + 1, step + 1)
        painter.setPen(QPen(halo, 1))
        painter.drawRoundedRect(grown, radius + step + 1, radius + step + 1)
    pane = QLinearGradient(target.topLeft(), target.bottomLeft())
    for position, alpha in ((0.0, 0.22), (0.3, 0.07), (0.75, 0.05), (1.0, 0.13)):
        pane.setColorAt(position, QColor.fromRgbF(1, 1, 1, alpha))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(pane))
    painter.drawRoundedRect(target, radius, radius)
    painter.setBrush(tint)
    painter.drawRoundedRect(target, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for step in range(4):
        inside = target.adjusted(step + 1, step + 1, -step - 1, -step - 1)
        painter.setPen(QPen(QColor.fromRgbF(1, 1, 1, 0.2 * 0.66**step), 1))
        painter.drawRoundedRect(inside, max(radius - step - 1, 0), max(radius - step - 1, 0))
    painter.setPen(QPen(QColor.fromRgbF(1, 1, 1, 0.62), 1))
    painter.drawRoundedRect(target, radius, radius)
    glint = QLinearGradient(target.topLeft(), target.topRight())
    for position, alpha in ((0.0, 0.0), (0.25, 0.85), (0.6, 0.25), (1.0, 0.0)):
        glint.setColorAt(position, QColor.fromRgbF(1, 1, 1, alpha))
    painter.setPen(QPen(QBrush(glint), 1.5))
    top = target.top() + 1
    painter.drawLine(QPointF(target.left() + radius, top), QPointF(target.right() - radius, top))


def flat_pen(paint: QColor | QBrush, width: float) -> QPen:
    pen = QPen(paint, width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pen
