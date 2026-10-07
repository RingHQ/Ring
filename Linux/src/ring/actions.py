"""Window actions and the pure geometry math behind them.

Nothing in this module talks to a display server. Every function maps plain
rectangles to plain rectangles, so the whole module is unit-testable.

Coordinates are in pixels with the origin at the top-left and y growing
downwards, as on screen.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle in screen pixels."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int) -> bool:
        """Return whether the point lies inside the rectangle."""
        return self.x <= x < self.right and self.y <= y < self.bottom

    def inset(self, amount: int) -> "Rect":
        """Shrink the rectangle by `amount` pixels on every side."""
        return Rect(
            self.x + amount,
            self.y + amount,
            max(self.width - 2 * amount, 1),
            max(self.height - 2 * amount, 1),
        )

    def intersects(self, other: "Rect") -> bool:
        """Return whether the two rectangles share any area."""
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )

    def intersection(self, other: "Rect") -> "Rect | None":
        """Return the shared area, or None if there is none."""
        if not self.intersects(other):
            return None
        x, y = max(self.x, other.x), max(self.y, other.y)
        return Rect(x, y, min(self.right, other.right) - x, min(self.bottom, other.bottom) - y)


@dataclass(frozen=True, slots=True)
class Gaps:
    """Spacing around snapped windows.

    `outer` separates a window from the edge of the work area, `inner` is the
    total space between two windows snapped side by side.
    """

    outer: int = 0
    inner: int = 0

    def __post_init__(self) -> None:
        if self.outer < 0 or self.inner < 0:
            raise ValueError("gaps must not be negative")


NO_GAPS = Gaps()


@dataclass(frozen=True, slots=True)
class FractionRect:
    """A region expressed as fractions (0..1) of a work area."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for start, size, axis in (
            (self.x, self.width, "x/width"),
            (self.y, self.height, "y/height"),
        ):
            if start < 0 or size <= 0 or start + size > 1 + _EPSILON:
                raise ValueError(
                    f"{axis} must describe a non-empty span within 0..1, "
                    f"got start={start} size={size}"
                )


class Action(StrEnum):
    """Everything the user can do to a window."""

    NONE = "none"

    MAXIMIZE = "maximize"
    ALMOST_MAXIMIZE = "almost_maximize"
    FULLSCREEN = "fullscreen"
    MAXIMIZE_HEIGHT = "maximize_height"
    MAXIMIZE_WIDTH = "maximize_width"
    FILL_AVAILABLE_SPACE = "fill_available_space"
    CENTER = "center"
    MACOS_CENTER = "macos_center"
    MINIMIZE = "minimize"
    MINIMIZE_OTHERS = "minimize_others"
    UNDO = "undo"
    INITIAL_FRAME = "initial_frame"

    TOP_HALF = "top_half"
    RIGHT_HALF = "right_half"
    BOTTOM_HALF = "bottom_half"
    LEFT_HALF = "left_half"
    HORIZONTAL_CENTER_HALF = "horizontal_center_half"
    VERTICAL_CENTER_HALF = "vertical_center_half"

    TOP_LEFT_QUARTER = "top_left_quarter"
    TOP_RIGHT_QUARTER = "top_right_quarter"
    BOTTOM_RIGHT_QUARTER = "bottom_right_quarter"
    BOTTOM_LEFT_QUARTER = "bottom_left_quarter"

    RIGHT_THIRD = "right_third"
    RIGHT_TWO_THIRDS = "right_two_thirds"
    HORIZONTAL_CENTER_THIRD = "horizontal_center_third"
    LEFT_THIRD = "left_third"
    LEFT_TWO_THIRDS = "left_two_thirds"

    FIRST_FOURTH = "first_fourth"
    SECOND_FOURTH = "second_fourth"
    THIRD_FOURTH = "third_fourth"
    FOURTH_FOURTH = "fourth_fourth"
    LEFT_THREE_FOURTHS = "left_three_fourths"
    RIGHT_THREE_FOURTHS = "right_three_fourths"

    TOP_THIRD = "top_third"
    TOP_TWO_THIRDS = "top_two_thirds"
    VERTICAL_CENTER_THIRD = "vertical_center_third"
    BOTTOM_THIRD = "bottom_third"
    BOTTOM_TWO_THIRDS = "bottom_two_thirds"

    NEXT_SCREEN = "next_screen"
    PREVIOUS_SCREEN = "previous_screen"
    LEFT_SCREEN = "left_screen"
    RIGHT_SCREEN = "right_screen"
    TOP_SCREEN = "top_screen"
    BOTTOM_SCREEN = "bottom_screen"

    NEXT_DESKTOP = "next_desktop"
    PREVIOUS_DESKTOP = "previous_desktop"

    LARGER = "larger"
    SMALLER = "smaller"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"

    SHRINK_TOP = "shrink_top"
    SHRINK_BOTTOM = "shrink_bottom"
    SHRINK_RIGHT = "shrink_right"
    SHRINK_LEFT = "shrink_left"
    SHRINK_HORIZONTAL = "shrink_horizontal"
    SHRINK_VERTICAL = "shrink_vertical"

    GROW_TOP = "grow_top"
    GROW_BOTTOM = "grow_bottom"
    GROW_RIGHT = "grow_right"
    GROW_LEFT = "grow_left"
    GROW_HORIZONTAL = "grow_horizontal"
    GROW_VERTICAL = "grow_vertical"

    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    MOVE_RIGHT = "move_right"
    MOVE_LEFT = "move_left"

    STASH_LEFT = "stash_left"
    STASH_RIGHT = "stash_right"
    STASH_BOTTOM = "stash_bottom"
    UNSTASH = "unstash"


@dataclass(frozen=True, slots=True)
class CustomAction:
    """A user-defined snap region from the config file."""

    name: str
    region: FractionRect


@dataclass(frozen=True, slots=True)
class PluginAction:
    """An action provided by a plugin, known here only by its name."""

    name: str


type Leaf = Action | CustomAction | PluginAction
"""An action that does one thing, as opposed to a cycle of them."""


@dataclass(frozen=True, slots=True)
class CycleAction:
    """Several actions behind one key or sector; repeating it steps through them."""

    steps: tuple[Leaf, ...]

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("a cycle needs at least one action")


type ActionSpec = Action | CustomAction | PluginAction | CycleAction


def action_name(action: ActionSpec) -> str:
    """Return the name an action goes by in the config file and in logs."""
    if isinstance(action, CycleAction):
        return "cycle(" + ", ".join(action_name(step) for step in action.steps) + ")"
    return action.value if isinstance(action, Action) else action.name


_THIRD = 1 / 3

FRACTIONS: dict[Action, FractionRect] = {
    Action.MAXIMIZE: FractionRect(0, 0, 1, 1),
    Action.TOP_HALF: FractionRect(0, 0, 1, 0.5),
    Action.RIGHT_HALF: FractionRect(0.5, 0, 0.5, 1),
    Action.BOTTOM_HALF: FractionRect(0, 0.5, 1, 0.5),
    Action.LEFT_HALF: FractionRect(0, 0, 0.5, 1),
    Action.HORIZONTAL_CENTER_HALF: FractionRect(0.25, 0, 0.5, 1),
    Action.VERTICAL_CENTER_HALF: FractionRect(0, 0.25, 1, 0.5),
    Action.TOP_LEFT_QUARTER: FractionRect(0, 0, 0.5, 0.5),
    Action.TOP_RIGHT_QUARTER: FractionRect(0.5, 0, 0.5, 0.5),
    Action.BOTTOM_RIGHT_QUARTER: FractionRect(0.5, 0.5, 0.5, 0.5),
    Action.BOTTOM_LEFT_QUARTER: FractionRect(0, 0.5, 0.5, 0.5),
    Action.RIGHT_THIRD: FractionRect(2 * _THIRD, 0, _THIRD, 1),
    Action.RIGHT_TWO_THIRDS: FractionRect(_THIRD, 0, 2 * _THIRD, 1),
    Action.HORIZONTAL_CENTER_THIRD: FractionRect(_THIRD, 0, _THIRD, 1),
    Action.LEFT_THIRD: FractionRect(0, 0, _THIRD, 1),
    Action.LEFT_TWO_THIRDS: FractionRect(0, 0, 2 * _THIRD, 1),
    Action.TOP_THIRD: FractionRect(0, 0, 1, _THIRD),
    Action.TOP_TWO_THIRDS: FractionRect(0, 0, 1, 2 * _THIRD),
    Action.VERTICAL_CENTER_THIRD: FractionRect(0, _THIRD, 1, _THIRD),
    Action.BOTTOM_THIRD: FractionRect(0, 2 * _THIRD, 1, _THIRD),
    Action.BOTTOM_TWO_THIRDS: FractionRect(0, _THIRD, 1, 2 * _THIRD),
    Action.FIRST_FOURTH: FractionRect(0, 0, 0.25, 1),
    Action.SECOND_FOURTH: FractionRect(0.25, 0, 0.25, 1),
    Action.THIRD_FOURTH: FractionRect(0.5, 0, 0.25, 1),
    Action.FOURTH_FOURTH: FractionRect(0.75, 0, 0.25, 1),
    Action.LEFT_THREE_FOURTHS: FractionRect(0, 0, 0.75, 1),
    Action.RIGHT_THREE_FOURTHS: FractionRect(0.25, 0, 0.75, 1),
}
"""Actions that are fully described by a fixed fraction of the work area."""

SCREEN_ACTIONS = frozenset(
    {
        Action.NEXT_SCREEN,
        Action.PREVIOUS_SCREEN,
        Action.LEFT_SCREEN,
        Action.RIGHT_SCREEN,
        Action.TOP_SCREEN,
        Action.BOTTOM_SCREEN,
    }
)
DESKTOP_ACTIONS = frozenset({Action.NEXT_DESKTOP, Action.PREVIOUS_DESKTOP})
SIZE_ACTIONS = frozenset({Action.LARGER, Action.SMALLER, Action.SCALE_UP, Action.SCALE_DOWN})
SHRINK_ACTIONS = frozenset(
    {
        Action.SHRINK_TOP,
        Action.SHRINK_BOTTOM,
        Action.SHRINK_RIGHT,
        Action.SHRINK_LEFT,
        Action.SHRINK_HORIZONTAL,
        Action.SHRINK_VERTICAL,
    }
)
GROW_ACTIONS = frozenset(
    {
        Action.GROW_TOP,
        Action.GROW_BOTTOM,
        Action.GROW_RIGHT,
        Action.GROW_LEFT,
        Action.GROW_HORIZONTAL,
        Action.GROW_VERTICAL,
    }
)
MOVE_ACTIONS = frozenset({Action.MOVE_UP, Action.MOVE_DOWN, Action.MOVE_RIGHT, Action.MOVE_LEFT})

STASH_ACTIONS = frozenset({Action.STASH_LEFT, Action.STASH_RIGHT, Action.STASH_BOTTOM})
"""Actions that tuck a window away behind a screen edge, leaving a strip to hover."""

INCREMENTAL_ACTIONS = SIZE_ACTIONS | SHRINK_ACTIONS | GROW_ACTIONS | MOVE_ACTIONS
"""Actions that nudge the current frame, so repeating them keeps going."""

STATEFUL_ACTIONS: frozenset[Action] = (
    frozenset(
        {
            Action.NONE,
            Action.FULLSCREEN,
            Action.MINIMIZE,
            Action.MINIMIZE_OTHERS,
            Action.UNDO,
            Action.INITIAL_FRAME,
            Action.UNSTASH,
        }
    )
    | SCREEN_ACTIONS
    | DESKTOP_ACTIONS
)
"""Actions that have no target rectangle on the current work area.

They depend on window state, history or another monitor and are resolved by
the executor rather than by `target_rect`.
"""

_FILLS_RADIAL_MENU = frozenset(
    {
        Action.FULLSCREEN,
        Action.MAXIMIZE,
        Action.ALMOST_MAXIMIZE,
        Action.MAXIMIZE_HEIGHT,
        Action.MAXIMIZE_WIDTH,
        Action.FILL_AVAILABLE_SPACE,
        Action.CENTER,
        Action.MACOS_CENTER,
        Action.VERTICAL_CENTER_HALF,
        Action.HORIZONTAL_CENTER_HALF,
    }
)


_STASH_ANGLES = {Action.STASH_LEFT: 270.0, Action.STASH_RIGHT: 90.0, Action.STASH_BOTTOM: 180.0}


def is_repeatable(action: Leaf) -> bool:
    """Return whether selecting the action again should apply it again."""
    return action in INCREMENTAL_ACTIONS or action is Action.UNDO


def fills_radial_menu(action: Leaf) -> bool:
    """Return whether the action lights up the whole ring rather than a segment."""
    return action in _FILLS_RADIAL_MENU


def radial_angle(action: Leaf) -> float | None:
    """Return where on the ring an action that is not in the menu should point.

    The result is the compass bearing, in degrees clockwise from north, from
    the center of the screen to the center of the area the action snaps to.
    None means the ring shows no segment for this action.
    """
    if not isinstance(action, Action) or action in _FILLS_RADIAL_MENU:
        return None
    if action in _STASH_ANGLES:
        return _STASH_ANGLES[action]
    region = FRACTIONS.get(action)
    return None if region is None else region_angle(region)


def region_angle(region: FractionRect) -> float | None:
    """Return the compass bearing from the screen center to the region's center.

    The result is in degrees clockwise from north (west is 270), or None for a
    region centered on the screen, which points nowhere.
    """
    dx = region.x + region.width / 2 - 0.5
    dy = region.y + region.height / 2 - 0.5
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    return math.degrees(math.atan2(dx, -dy)) % 360


def cycle_step(
    cycle: CycleAction,
    current: Leaf,
    recorded: Leaf | None,
    *,
    advance: bool,
    backward: bool = False,
    restart: bool = False,
) -> Leaf:
    """Pick the step of `cycle` to select.

    `current` is what is selected right now and `recorded` what the window
    was last snapped to. Inside the cycle, `advance` moves one step and
    otherwise the selection stays put. Coming from outside, the cycle
    continues after the window's recorded step, so a window that already
    fills the left half goes straight to the left third; `restart` disables
    that and always begins at the first step.
    """
    steps = cycle.steps
    offset = -1 if advance and backward else 1
    if current in steps:
        if not advance:
            return current
        return steps[(steps.index(current) + offset) % len(steps)]
    if not restart and recorded is not None and recorded in steps:
        return steps[(steps.index(recorded) + offset) % len(steps)]
    return steps[-1] if offset < 0 else steps[0]


def _span(origin: int, length: int, start: float, size: float, gaps: Gaps) -> tuple[int, int]:
    """Resolve one axis of a fractional region to (position, size) in pixels.

    Both edges are rounded from the same fractions that neighbouring regions
    use, so adjacent regions always share an exact pixel boundary. An edge on
    the border of the work area is moved in by the outer gap; an interior edge
    gives up half of the inner gap, with the odd pixel going to the far edge.
    """
    end = start + size
    low = origin + round(length * start)
    high = origin + round(length * end)
    low += gaps.outer if start <= _EPSILON else gaps.inner // 2
    high -= gaps.outer if end >= 1 - _EPSILON else gaps.inner - gaps.inner // 2
    return low, max(high - low, 1)


def fraction_rect(area: Rect, region: FractionRect, gaps: Gaps = NO_GAPS) -> Rect:
    """Place a fractional region inside a work area, applying gaps."""
    x, width = _span(area.x, area.width, region.x, region.width, gaps)
    y, height = _span(area.y, area.height, region.y, region.height, gaps)
    return Rect(x, y, width, height)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def center_rect(area: Rect, window: Rect, gaps: Gaps = NO_GAPS) -> Rect:
    """Center the window in the work area, shrinking it only if it does not fit."""
    usable = area.inset(gaps.outer)
    width = min(window.width, usable.width)
    height = min(window.height, usable.height)
    return Rect(
        usable.x + (usable.width - width) // 2,
        usable.y + (usable.height - height) // 2,
        width,
        height,
    )


def macos_center_rect(area: Rect, window: Rect, gaps: Gaps = NO_GAPS) -> Rect:
    """Center the window the way macOS does: slightly above the middle.

    The smaller the window, the higher it sits; a window as tall as the work
    area is not lifted at all.
    """
    centered = center_rect(area, window, gaps)
    usable = area.inset(gaps.outer)
    lift = (0.5 * centered.height / usable.height - 0.5) * (usable.height / 2)
    return Rect(centered.x, centered.y + round(lift), centered.width, centered.height)


def maximize_height_rect(area: Rect, window: Rect, gaps: Gaps = NO_GAPS) -> Rect:
    """Stretch the window vertically, keeping its horizontal placement."""
    usable = area.inset(gaps.outer)
    width = min(window.width, usable.width)
    x = _clamp(window.x, usable.x, usable.right - width)
    return Rect(x, usable.y, width, usable.height)


def maximize_width_rect(area: Rect, window: Rect, gaps: Gaps = NO_GAPS) -> Rect:
    """Stretch the window horizontally, keeping its vertical placement."""
    usable = area.inset(gaps.outer)
    height = min(window.height, usable.height)
    y = _clamp(window.y, usable.y, usable.bottom - height)
    return Rect(usable.x, y, usable.width, height)


def almost_maximize_rect(area: Rect, margin: float, gaps: Gaps = NO_GAPS) -> Rect:
    """Fill the work area except for a margin on every side.

    `margin` is the fraction of each dimension left free per side, so 0.05
    yields a window covering 90% of the width and 90% of the height.
    """
    if not 0 <= margin < 0.5:
        raise ValueError(f"margin must be in the range 0 <= margin < 0.5, got {margin}")
    usable = area.inset(gaps.outer)
    dx = round(usable.width * margin)
    dy = round(usable.height * margin)
    return Rect(
        usable.x + dx,
        usable.y + dy,
        max(usable.width - 2 * dx, 1),
        max(usable.height - 2 * dy, 1),
    )


def fill_available_space_rect(frame: Rect, bounds: Rect, others: Sequence[Rect]) -> Rect:
    """Grow the window into the free space around it.

    `others` are the frames of the other windows. Those overlapping the
    window are ignored; the result is the largest rectangle that contains no
    other window, built from the window's own edges, its nearest neighbours
    and the edges of `bounds`.
    """
    obstacles = [
        cropped
        for other in others
        if not other.intersects(frame) and (cropped := other.intersection(bounds)) is not None
    ]
    min_x, min_y, max_x, max_y = bounds.x, bounds.y, bounds.right, bounds.bottom
    for other in obstacles:
        if other.right <= frame.x:
            min_x = max(min_x, other.right)
        if other.bottom <= frame.y:
            min_y = max(min_y, other.bottom)
        if other.x >= frame.right:
            max_x = min(max_x, other.x)
        if other.y >= frame.bottom:
            max_y = min(max_y, other.y)

    horizontal = {
        (min_x, max_x),
        (frame.x, max_x),
        (min_x, frame.right),
        (frame.x, bounds.right),
        (bounds.x, frame.right),
        (bounds.x, bounds.right),
    }
    vertical = {
        (min_y, max_y),
        (frame.y, max_y),
        (min_y, frame.bottom),
        (frame.y, bounds.bottom),
        (bounds.y, frame.bottom),
        (bounds.y, bounds.bottom),
    }
    best = frame
    best_area = 0
    # Sorted so that ties are broken the same way on every run.
    for left, right in sorted(horizontal):
        for top, bottom in sorted(vertical):
            if right <= left or bottom <= top:
                continue
            candidate = Rect(left, top, right - left, bottom - top)
            area = candidate.width * candidate.height
            if area > best_area and not any(other.intersects(candidate) for other in obstacles):
                best, best_area = candidate, area
    return best


_TOP, _BOTTOM, _LEFT, _RIGHT = "top", "bottom", "left", "right"
_ALL_SIDES = frozenset({_TOP, _BOTTOM, _LEFT, _RIGHT})
_SIDES = {
    Action.SHRINK_TOP: {_TOP},
    Action.GROW_TOP: {_TOP},
    Action.SHRINK_BOTTOM: {_BOTTOM},
    Action.GROW_BOTTOM: {_BOTTOM},
    Action.SHRINK_LEFT: {_LEFT},
    Action.GROW_LEFT: {_LEFT},
    Action.SHRINK_RIGHT: {_RIGHT},
    Action.GROW_RIGHT: {_RIGHT},
    Action.SHRINK_HORIZONTAL: {_LEFT, _RIGHT},
    Action.GROW_HORIZONTAL: {_LEFT, _RIGHT},
    Action.SHRINK_VERTICAL: {_TOP, _BOTTOM},
    Action.GROW_VERTICAL: {_TOP, _BOTTOM},
}


def adjust_size_rect(
    action: Action, frame: Rect, bounds: Rect, increment: int, min_size: int = 110
) -> Rect:
    """Resize `frame` by one step for a larger/smaller, scale, grow or shrink action.

    `larger` and `smaller` move every edge that does not already touch
    `bounds`, so a window snapped to a screen edge stays attached to it.
    `scale_up` and `scale_down` keep the aspect ratio when all four edges are
    free. The result never leaves `bounds` and never drops below `min_size`.
    """
    grows = action in GROW_ACTIONS or action in (Action.LARGER, Action.SCALE_UP)
    step = -increment if grows else increment
    if action in SIZE_ACTIONS:
        touching = {
            side
            for side, near in (
                (_LEFT, abs(frame.x - bounds.x) <= 1),
                (_TOP, abs(frame.y - bounds.y) <= 1),
                (_RIGHT, abs(frame.right - bounds.right) <= 1),
                (_BOTTOM, abs(frame.bottom - bounds.bottom) <= 1),
            )
            if near
        }
        sides = set(_ALL_SIDES - touching)
    else:
        sides = _SIDES[action]

    x, y, width, height = float(frame.x), float(frame.y), float(frame.width), float(frame.height)
    mid_x, mid_y = x + width / 2, y + height / 2
    if not sides or sides == _ALL_SIDES:
        scale = min((width - 2 * step) / width, (height - 2 * step) / height)
        if action in (Action.SCALE_UP, Action.SCALE_DOWN) and scale > 0:
            scale = max(scale, min_size / width, min_size / height)
            width, height = width * scale, height * scale
        else:
            width = max(float(min_size), width - 2 * step)
            height = max(float(min_size), height - 2 * step)
        x, y = mid_x - width / 2, mid_y - height / 2
    else:
        if _TOP in sides:
            y += step
            height -= step
        if _BOTTOM in sides:
            height -= step
        if _LEFT in sides:
            x += step
            width -= step
        if _RIGHT in sides:
            width -= step
        if width < min_size:
            width, x = float(min_size), mid_x - min_size / 2
        if height < min_size:
            height, y = float(min_size), mid_y - min_size / 2

    left, top = round(x), round(y)
    resized = Rect(left, top, round(x + width) - left, round(y + height) - top)
    result = resized.intersection(bounds) if resized.width > 0 and resized.height > 0 else None
    if result is None:
        return frame
    if abs(result.width - frame.width) <= 2 and abs(result.height - frame.height) <= 2:
        return frame
    return result


def move_rect(action: Action, frame: Rect, increment: int) -> Rect:
    """Shift `frame` by one step for a move action."""
    dx = {Action.MOVE_LEFT: -increment, Action.MOVE_RIGHT: increment}.get(action, 0)
    dy = {Action.MOVE_UP: -increment, Action.MOVE_DOWN: increment}.get(action, 0)
    return Rect(frame.x + dx, frame.y + dy, frame.width, frame.height)


def stash_frames(action: Action, frame: Rect, bounds: Rect, peek: int) -> tuple[Rect, Rect]:
    """Return the (revealed, stashed) frames for stashing a window at an edge.

    Revealed, the window sits against the edge of `bounds` at its own size.
    Stashed, it is pushed out past that edge until only a strip of `peek`
    pixels is left to hover over; the strip never exceeds a fifth of the
    window.
    """
    width, height = min(frame.width, bounds.width), min(frame.height, bounds.height)
    x = _clamp(frame.x, bounds.x, bounds.right - width)
    y = _clamp(frame.y, bounds.y, bounds.bottom - height)
    if action is Action.STASH_BOTTOM:
        strip = max(1, min(peek, height // 5))
        revealed = Rect(x, bounds.bottom - height, width, height)
        return revealed, Rect(x, bounds.bottom - strip, width, height)
    strip = max(1, min(peek, width // 5))
    if action is Action.STASH_LEFT:
        revealed = Rect(bounds.x, y, width, height)
        return revealed, Rect(bounds.x - width + strip, y, width, height)
    revealed = Rect(bounds.right - width, y, width, height)
    return revealed, Rect(bounds.right - strip, y, width, height)


def stash_conflict(first: Rect, second: Rect, *, vertical: bool, tolerance: int = 100) -> bool:
    """Return whether two windows stashed at the same edge get in each other's way.

    `vertical` says the edge runs vertically (left or right), so the windows
    are compared along y. They may overlap as long as the shorter one still
    sticks out past the longer one by `tolerance` pixels on one side.
    """
    spans = [(r.y, r.bottom) if vertical else (r.x, r.right) for r in (first, second)]
    (long_low, long_high), (short_low, short_high) = sorted(
        spans, key=lambda span: span[1] - span[0], reverse=True
    )
    if short_high < long_low or long_high < short_low:
        return False
    return max(long_low - short_low, short_high - long_high, 0) < tolerance


def move_to_monitor_rect(window: Rect, source: Rect, target: Rect) -> Rect:
    """Move a window from one work area to another.

    Size and position are scaled with the work areas, so a window covering the
    left half of the source covers the left half of the target. The result is
    always kept inside the target.
    """
    scale_x = target.width / source.width
    scale_y = target.height / source.height
    width = _clamp(round(window.width * scale_x), 1, target.width)
    height = _clamp(round(window.height * scale_y), 1, target.height)
    x = target.x + round((window.x - source.x) * scale_x)
    y = target.y + round((window.y - source.y) * scale_y)
    return Rect(
        _clamp(x, target.x, target.right - width),
        _clamp(y, target.y, target.bottom - height),
        width,
        height,
    )


def cycle_monitor(index: int, count: int, step: int) -> int:
    """Return the monitor index `step` positions away, wrapping around."""
    if count <= 0:
        raise ValueError("there must be at least one monitor")
    return (index + step) % count


def directional_monitor(index: int, monitors: Sequence[Rect], action: Action) -> int | None:
    """Return the index of the nearest monitor on the given side, if any.

    `action` is one of left/right/top/bottom_screen. A monitor counts as
    being on that side when its center lies further that way than across.
    """
    here = monitors[index]
    here_x, here_y = here.x + here.width / 2, here.y + here.height / 2
    best: tuple[float, int] | None = None
    for other_index, other in enumerate(monitors):
        if other_index == index:
            continue
        dx = other.x + other.width / 2 - here_x
        dy = other.y + other.height / 2 - here_y
        on_side = {
            Action.LEFT_SCREEN: dx < 0 and abs(dx) >= abs(dy),
            Action.RIGHT_SCREEN: dx > 0 and abs(dx) >= abs(dy),
            Action.TOP_SCREEN: dy < 0 and abs(dy) > abs(dx),
            Action.BOTTOM_SCREEN: dy > 0 and abs(dy) > abs(dx),
        }.get(action, False)
        if on_side and (best is None or math.hypot(dx, dy) < best[0]):
            best = (math.hypot(dx, dy), other_index)
    return None if best is None else best[1]


def target_rect(
    action: Leaf,
    area: Rect,
    frame: Rect,
    gaps: Gaps = NO_GAPS,
    almost_maximize_margin: float = 0.05,
    size_increment: int = 20,
    min_size: int = 110,
    others: Sequence[Rect] = (),
) -> Rect | None:
    """Compute where `action` puts a window on the work area `area`.

    `frame` is the frame the action starts from: the window's current one,
    or the previous target while several actions are chained in one session.
    `others` are the other windows on the screen, used only by
    `fill_available_space`. Returns None for the actions in
    `STATEFUL_ACTIONS`, which cannot be answered from the work area alone.
    """
    if isinstance(action, CustomAction):
        return fraction_rect(area, action.region, gaps)
    if isinstance(action, PluginAction):
        return None
    if action in FRACTIONS:
        return fraction_rect(area, FRACTIONS[action], gaps)
    bounds = area.inset(gaps.outer)
    if action in SIZE_ACTIONS or action in SHRINK_ACTIONS or action in GROW_ACTIONS:
        return adjust_size_rect(action, frame, bounds, size_increment, min_size)
    if action in MOVE_ACTIONS:
        return move_rect(action, frame, size_increment)
    if action in STASH_ACTIONS:
        return stash_frames(action, frame, area, 0)[0]
    match action:
        case Action.CENTER:
            return center_rect(area, frame, gaps)
        case Action.MACOS_CENTER:
            return macos_center_rect(area, frame, gaps)
        case Action.MAXIMIZE_HEIGHT:
            return maximize_height_rect(area, frame, gaps)
        case Action.MAXIMIZE_WIDTH:
            return maximize_width_rect(area, frame, gaps)
        case Action.ALMOST_MAXIMIZE:
            return almost_maximize_rect(area, almost_maximize_margin, gaps)
        case Action.FILL_AVAILABLE_SPACE:
            return fill_available_space_rect(frame, bounds, others)
        case _:
            return None


class Direction(StrEnum):
    """A slot of the radial menu: the center or one of eight sectors."""

    CENTER = "center"
    NORTH = "north"
    NORTH_EAST = "north_east"
    EAST = "east"
    SOUTH_EAST = "south_east"
    SOUTH = "south"
    SOUTH_WEST = "south_west"
    WEST = "west"
    NORTH_WEST = "north_west"


SECTORS = (
    Direction.NORTH,
    Direction.NORTH_EAST,
    Direction.EAST,
    Direction.SOUTH_EAST,
    Direction.SOUTH,
    Direction.SOUTH_WEST,
    Direction.WEST,
    Direction.NORTH_WEST,
)
"""The outer sectors, clockwise from north."""

NO_SELECTION_DISTANCE = 10
"""Pointer travel, in pixels, below which nothing is selected at all."""


def sector_angle(direction: Direction) -> float:
    """Return the compass bearing of an outer sector, in degrees."""
    return SECTORS.index(direction) * 360 / len(SECTORS)


def direction_at(dx: float, dy: float, radius: float, thickness: float) -> Direction | None:
    """Map the pointer offset from the menu center to a slot of the menu.

    Offsets are in screen pixels (y grows downwards). Next to where the
    pointer started nothing is selected (None). Inside the hole of the ring
    it is the center action, and from the ring outwards the sector the
    pointer has moved towards.
    """
    distance = math.hypot(dx, dy)
    if distance > radius - thickness:
        span = 360 / len(SECTORS)
        bearing = math.degrees(math.atan2(dx, -dy)) % 360
        return SECTORS[int((bearing + span / 2) / span) % len(SECTORS)]
    if distance > NO_SELECTION_DISTANCE:
        return Direction.CENTER
    return None
