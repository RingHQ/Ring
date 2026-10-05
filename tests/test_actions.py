from itertools import pairwise

import pytest

from looplinux.actions import (
    FRACTIONS,
    INCREMENTAL_ACTIONS,
    STATEFUL_ACTIONS,
    Action,
    CustomAction,
    CycleAction,
    Direction,
    FractionRect,
    Gaps,
    Rect,
    action_name,
    adjust_size_rect,
    almost_maximize_rect,
    center_rect,
    cycle_monitor,
    cycle_step,
    direction_at,
    directional_monitor,
    fill_available_space_rect,
    fills_radial_menu,
    fraction_rect,
    is_repeatable,
    macos_center_rect,
    maximize_height_rect,
    maximize_width_rect,
    move_rect,
    move_to_monitor_rect,
    radial_angle,
    region_angle,
    sector_angle,
    stash_conflict,
    stash_frames,
    target_rect,
)

SCREEN = Rect(0, 0, 1920, 1080)
# A second monitor to the right, with a 30 px panel reserved at the top.
OFFSET = Rect(1920, 30, 2560, 1410)
WINDOW = Rect(100, 200, 800, 600)


def snap(action: Action, area: Rect = SCREEN, gaps: Gaps = Gaps()) -> Rect:  # noqa: B008
    rect = target_rect(action, area, WINDOW, gaps)
    assert rect is not None
    return rect


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Action.MAXIMIZE, Rect(0, 0, 1920, 1080)),
        (Action.LEFT_HALF, Rect(0, 0, 960, 1080)),
        (Action.RIGHT_HALF, Rect(960, 0, 960, 1080)),
        (Action.TOP_HALF, Rect(0, 0, 1920, 540)),
        (Action.BOTTOM_HALF, Rect(0, 540, 1920, 540)),
        (Action.HORIZONTAL_CENTER_HALF, Rect(480, 0, 960, 1080)),
        (Action.VERTICAL_CENTER_HALF, Rect(0, 270, 1920, 540)),
        (Action.TOP_LEFT_QUARTER, Rect(0, 0, 960, 540)),
        (Action.TOP_RIGHT_QUARTER, Rect(960, 0, 960, 540)),
        (Action.BOTTOM_LEFT_QUARTER, Rect(0, 540, 960, 540)),
        (Action.BOTTOM_RIGHT_QUARTER, Rect(960, 540, 960, 540)),
        (Action.LEFT_THIRD, Rect(0, 0, 640, 1080)),
        (Action.HORIZONTAL_CENTER_THIRD, Rect(640, 0, 640, 1080)),
        (Action.RIGHT_THIRD, Rect(1280, 0, 640, 1080)),
        (Action.LEFT_TWO_THIRDS, Rect(0, 0, 1280, 1080)),
        (Action.RIGHT_TWO_THIRDS, Rect(640, 0, 1280, 1080)),
        (Action.TOP_THIRD, Rect(0, 0, 1920, 360)),
        (Action.TOP_TWO_THIRDS, Rect(0, 0, 1920, 720)),
        (Action.VERTICAL_CENTER_THIRD, Rect(0, 360, 1920, 360)),
        (Action.BOTTOM_THIRD, Rect(0, 720, 1920, 360)),
        (Action.BOTTOM_TWO_THIRDS, Rect(0, 360, 1920, 720)),
        (Action.FIRST_FOURTH, Rect(0, 0, 480, 1080)),
        (Action.SECOND_FOURTH, Rect(480, 0, 480, 1080)),
        (Action.THIRD_FOURTH, Rect(960, 0, 480, 1080)),
        (Action.FOURTH_FOURTH, Rect(1440, 0, 480, 1080)),
        (Action.LEFT_THREE_FOURTHS, Rect(0, 0, 1440, 1080)),
        (Action.RIGHT_THREE_FOURTHS, Rect(480, 0, 1440, 1080)),
    ],
)
def test_fractional_actions_without_gaps(action: Action, expected: Rect) -> None:
    assert snap(action) == expected


def test_work_area_origin_is_respected() -> None:
    assert snap(Action.MAXIMIZE, OFFSET) == OFFSET
    assert snap(Action.RIGHT_HALF, OFFSET) == Rect(3200, 30, 1280, 1410)
    assert snap(Action.BOTTOM_LEFT_QUARTER, OFFSET) == Rect(1920, 735, 1280, 705)


def test_outer_gap_applies_to_screen_edges() -> None:
    assert snap(Action.MAXIMIZE, gaps=Gaps(outer=10)) == Rect(10, 10, 1900, 1060)


def test_inner_gap_is_split_between_neighbours() -> None:
    gaps = Gaps(outer=10, inner=8)
    left = snap(Action.LEFT_HALF, gaps=gaps)
    right = snap(Action.RIGHT_HALF, gaps=gaps)
    assert left == Rect(10, 10, 946, 1060)
    assert right == Rect(964, 10, 946, 1060)
    assert right.x - left.right == 8


def test_odd_inner_gap_is_not_lost_to_rounding() -> None:
    gaps = Gaps(inner=5)
    left = snap(Action.LEFT_HALF, gaps=gaps)
    right = snap(Action.RIGHT_HALF, gaps=gaps)
    assert right.x - left.right == 5
    top = snap(Action.TOP_LEFT_QUARTER, gaps=gaps)
    bottom = snap(Action.BOTTOM_LEFT_QUARTER, gaps=gaps)
    assert bottom.y - top.bottom == 5


@pytest.mark.parametrize("width", [1000, 1366, 1920, 2560, 3440])
def test_thirds_and_fourths_tile_the_work_area_exactly(width: int) -> None:
    area = Rect(7, 0, width, 900)
    left = snap(Action.LEFT_THIRD, area)
    center = snap(Action.HORIZONTAL_CENTER_THIRD, area)
    right = snap(Action.RIGHT_THIRD, area)
    assert left.x == area.x
    assert left.right == center.x
    assert center.right == right.x
    assert right.right == area.right
    assert snap(Action.LEFT_TWO_THIRDS, area).right == center.right
    assert snap(Action.RIGHT_TWO_THIRDS, area).x == center.x

    fourths = [
        snap(action, area)
        for action in (
            Action.FIRST_FOURTH,
            Action.SECOND_FOURTH,
            Action.THIRD_FOURTH,
            Action.FOURTH_FOURTH,
        )
    ]
    assert fourths[0].x == area.x
    assert fourths[-1].right == area.right
    for before, after in pairwise(fourths):
        assert before.right == after.x


def test_halves_tile_an_odd_sized_work_area() -> None:
    area = Rect(0, 0, 1367, 769)
    left = snap(Action.LEFT_HALF, area)
    right = snap(Action.RIGHT_HALF, area)
    assert left.right == right.x
    assert left.width + right.width == area.width


def test_huge_gaps_never_produce_an_empty_rect() -> None:
    rect = snap(Action.TOP_LEFT_QUARTER, Rect(0, 0, 100, 100), Gaps(outer=80, inner=80))
    assert rect.width >= 1
    assert rect.height >= 1


def test_center_keeps_the_window_size() -> None:
    assert center_rect(SCREEN, WINDOW) == Rect(560, 240, 800, 600)
    assert center_rect(OFFSET, WINDOW) == Rect(2800, 435, 800, 600)


def test_center_shrinks_windows_that_do_not_fit() -> None:
    huge = Rect(0, 0, 5000, 5000)
    assert center_rect(SCREEN, huge, Gaps(outer=10)) == Rect(10, 10, 1900, 1060)


def test_macos_center_sits_above_the_middle() -> None:
    # Half as tall as the screen: lifted by a quarter of half the screen.
    assert macos_center_rect(SCREEN, Rect(0, 0, 800, 540)) == Rect(560, 135, 800, 540)
    # A window as tall as the screen cannot be lifted.
    assert macos_center_rect(SCREEN, Rect(0, 0, 800, 1080)) == Rect(560, 0, 800, 1080)
    assert macos_center_rect(SCREEN, WINDOW).y < center_rect(SCREEN, WINDOW).y


def test_maximize_height_keeps_horizontal_placement() -> None:
    assert maximize_height_rect(SCREEN, WINDOW) == Rect(100, 0, 800, 1080)
    assert maximize_height_rect(SCREEN, WINDOW, Gaps(outer=10)) == Rect(100, 10, 800, 1060)


def test_maximize_width_keeps_vertical_placement() -> None:
    assert maximize_width_rect(SCREEN, WINDOW) == Rect(0, 200, 1920, 600)


def test_maximize_height_pulls_a_stray_window_into_the_work_area() -> None:
    # The window currently sits on SCREEN but the cursor is on OFFSET.
    assert maximize_height_rect(OFFSET, WINDOW) == Rect(1920, 30, 800, 1410)
    off_right = Rect(4400, 0, 800, 600)
    assert maximize_height_rect(OFFSET, off_right).right == OFFSET.right


def test_almost_maximize() -> None:
    assert almost_maximize_rect(SCREEN, 0.05) == Rect(96, 54, 1728, 972)
    assert almost_maximize_rect(SCREEN, 0) == SCREEN
    assert almost_maximize_rect(SCREEN, 0, Gaps(outer=10)) == Rect(10, 10, 1900, 1060)


@pytest.mark.parametrize("margin", [-0.1, 0.5, 1.0])
def test_almost_maximize_rejects_bad_margins(margin: float) -> None:
    with pytest.raises(ValueError, match="margin"):
        almost_maximize_rect(SCREEN, margin)


def test_target_rect_uses_the_given_margin() -> None:
    rect = target_rect(Action.ALMOST_MAXIMIZE, SCREEN, WINDOW, almost_maximize_margin=0.1)
    assert rect == Rect(192, 108, 1536, 864)


def test_larger_and_smaller_move_every_free_edge() -> None:
    assert adjust_size_rect(Action.LARGER, WINDOW, SCREEN, 20) == Rect(80, 180, 840, 640)
    assert adjust_size_rect(Action.SMALLER, WINDOW, SCREEN, 20) == Rect(120, 220, 760, 560)


def test_larger_keeps_edges_that_touch_the_screen_attached() -> None:
    left_half = Rect(0, 0, 960, 1080)
    # Only the right edge is free.
    assert adjust_size_rect(Action.LARGER, left_half, SCREEN, 20) == Rect(0, 0, 980, 1080)
    assert adjust_size_rect(Action.SMALLER, left_half, SCREEN, 20) == Rect(0, 0, 940, 1080)
    corner = Rect(960, 540, 960, 540)
    assert adjust_size_rect(Action.SMALLER, corner, SCREEN, 20) == Rect(980, 560, 940, 520)


def test_scale_keeps_the_aspect_ratio() -> None:
    scaled = adjust_size_rect(Action.SCALE_UP, WINDOW, SCREEN, 30)
    assert scaled.width / scaled.height == pytest.approx(800 / 600, abs=0.01)
    assert scaled.width > WINDOW.width
    # The center stays where it was.
    assert scaled.x + scaled.width // 2 == pytest.approx(500, abs=1)
    assert scaled.y + scaled.height // 2 == pytest.approx(500, abs=1)
    assert adjust_size_rect(Action.SCALE_DOWN, WINDOW, SCREEN, 30).width < WINDOW.width


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Action.GROW_TOP, Rect(100, 180, 800, 620)),
        (Action.GROW_BOTTOM, Rect(100, 200, 800, 620)),
        (Action.GROW_LEFT, Rect(80, 200, 820, 600)),
        (Action.GROW_RIGHT, Rect(100, 200, 820, 600)),
        (Action.GROW_HORIZONTAL, Rect(80, 200, 840, 600)),
        (Action.GROW_VERTICAL, Rect(100, 180, 800, 640)),
        (Action.SHRINK_TOP, Rect(100, 220, 800, 580)),
        (Action.SHRINK_BOTTOM, Rect(100, 200, 800, 580)),
        (Action.SHRINK_LEFT, Rect(120, 200, 780, 600)),
        (Action.SHRINK_RIGHT, Rect(100, 200, 780, 600)),
        (Action.SHRINK_HORIZONTAL, Rect(120, 200, 760, 600)),
        (Action.SHRINK_VERTICAL, Rect(100, 220, 800, 560)),
    ],
)
def test_grow_and_shrink_move_one_side(action: Action, expected: Rect) -> None:
    assert adjust_size_rect(action, WINDOW, SCREEN, 20) == expected


def test_resizing_stops_at_the_screen_and_at_the_minimum_size() -> None:
    assert adjust_size_rect(Action.LARGER, SCREEN, SCREEN, 20) == SCREEN
    assert adjust_size_rect(Action.GROW_LEFT, Rect(0, 0, 500, 500), SCREEN, 20) == Rect(
        0, 0, 500, 500
    )
    small = Rect(500, 500, 120, 120)
    assert adjust_size_rect(Action.SMALLER, small, SCREEN, 20, min_size=110) == Rect(
        505, 505, 110, 110
    )
    assert adjust_size_rect(Action.SHRINK_RIGHT, small, SCREEN, 20, min_size=110).width == 110


def test_move() -> None:
    assert move_rect(Action.MOVE_UP, WINDOW, 20) == Rect(100, 180, 800, 600)
    assert move_rect(Action.MOVE_DOWN, WINDOW, 20) == Rect(100, 220, 800, 600)
    assert move_rect(Action.MOVE_LEFT, WINDOW, 20) == Rect(80, 200, 800, 600)
    assert move_rect(Action.MOVE_RIGHT, WINDOW, 20) == Rect(120, 200, 800, 600)


def test_incremental_actions_continue_from_the_given_frame() -> None:
    frame = WINDOW
    for _ in range(3):
        step = target_rect(Action.GROW_RIGHT, SCREEN, frame, size_increment=10)
        assert step is not None
        frame = step
    assert frame == Rect(100, 200, 830, 600)


def test_fill_available_space_grows_up_to_the_neighbours() -> None:
    left = Rect(0, 0, 600, 1080)
    right = Rect(1500, 0, 420, 1080)
    window = Rect(800, 300, 400, 300)
    assert fill_available_space_rect(window, SCREEN, [left, right]) == Rect(600, 0, 900, 1080)


def test_fill_available_space_ignores_overlapping_windows() -> None:
    behind = Rect(700, 250, 600, 500)
    window = Rect(800, 300, 400, 300)
    assert fill_available_space_rect(window, SCREEN, [behind]) == SCREEN
    assert fill_available_space_rect(window, SCREEN, []) == SCREEN


def test_fill_available_space_picks_the_largest_free_rectangle() -> None:
    # A window above only blocks the columns it covers.
    above = Rect(0, 0, 900, 200)
    window = Rect(1000, 400, 400, 300)
    result = fill_available_space_rect(window, SCREEN, [above])
    assert not result.intersects(above)
    assert result == Rect(0, 200, 1920, 880)


def test_move_to_monitor_scales_position_and_size() -> None:
    target = Rect(1920, 0, 2560, 1440)
    window = Rect(480, 270, 960, 540)
    assert move_to_monitor_rect(window, SCREEN, target) == Rect(2560, 360, 1280, 720)


def test_move_to_monitor_preserves_snapped_layouts() -> None:
    left_half = snap(Action.LEFT_HALF)
    assert move_to_monitor_rect(left_half, SCREEN, OFFSET) == snap(Action.LEFT_HALF, OFFSET)


def test_move_to_monitor_keeps_the_window_on_the_target() -> None:
    target = Rect(1920, 0, 1280, 720)
    stray = Rect(-300, 900, 2500, 800)
    moved = move_to_monitor_rect(stray, SCREEN, target)
    assert moved.x >= target.x
    assert moved.y >= target.y
    assert moved.right <= target.right
    assert moved.bottom <= target.bottom


def test_cycle_monitor_wraps() -> None:
    assert cycle_monitor(0, 3, 1) == 1
    assert cycle_monitor(2, 3, 1) == 0
    assert cycle_monitor(0, 3, -1) == 2
    assert cycle_monitor(0, 1, 1) == 0
    with pytest.raises(ValueError, match="monitor"):
        cycle_monitor(0, 0, 1)


def test_directional_monitor() -> None:
    main = Rect(0, 0, 1920, 1080)
    right = Rect(1920, 0, 1920, 1080)
    far_right = Rect(3840, 0, 1920, 1080)
    above = Rect(0, -1080, 1920, 1080)
    monitors = [main, right, far_right, above]
    assert directional_monitor(0, monitors, Action.RIGHT_SCREEN) == 1
    assert directional_monitor(2, monitors, Action.LEFT_SCREEN) == 1
    assert directional_monitor(0, monitors, Action.TOP_SCREEN) == 3
    assert directional_monitor(0, monitors, Action.LEFT_SCREEN) is None
    assert directional_monitor(0, monitors, Action.BOTTOM_SCREEN) is None
    assert directional_monitor(3, monitors, Action.BOTTOM_SCREEN) == 0


def test_custom_action() -> None:
    column = CustomAction("center_column", FractionRect(0.25, 0, 0.5, 1))
    assert target_rect(column, SCREEN, WINDOW) == Rect(480, 0, 960, 1080)
    # Edges that do not touch the work area border count as inner edges.
    assert target_rect(column, SCREEN, WINDOW, Gaps(outer=20, inner=8)) == Rect(484, 20, 952, 1040)


@pytest.mark.parametrize(
    ("x", "y", "w", "h"),
    [(-0.1, 0, 0.5, 1), (0, 0, 0, 1), (0.6, 0, 0.5, 1), (0, 0.5, 1, 0.6), (0, 0, 1, -1)],
)
def test_fraction_rect_validation(x: float, y: float, w: float, h: float) -> None:
    with pytest.raises(ValueError, match=r"within 0\.\.1"):
        FractionRect(x, y, w, h)


def test_fraction_rect_direct() -> None:
    assert fraction_rect(SCREEN, FractionRect(0, 0, 1, 1)) == SCREEN


def test_negative_gaps_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        Gaps(outer=-1)


def test_stateful_actions_have_no_target_rect() -> None:
    for action in STATEFUL_ACTIONS:
        assert target_rect(action, SCREEN, WINDOW) is None


def test_every_action_is_either_geometric_or_stateful() -> None:
    for action in Action:
        has_rect = target_rect(action, SCREEN, WINDOW) is not None
        assert has_rect != (action in STATEFUL_ACTIONS), action
    assert not STATEFUL_ACTIONS & FRACTIONS.keys()
    assert not STATEFUL_ACTIONS & INCREMENTAL_ACTIONS


def test_rect_helpers() -> None:
    assert SCREEN.contains(0, 0)
    assert SCREEN.contains(1919, 1079)
    assert not SCREEN.contains(1920, 0)
    assert OFFSET.contains(1920, 30)
    assert not OFFSET.contains(1920, 29)
    assert Rect(0, 0, 10, 10).intersection(Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5)
    # Sharing an edge is not overlapping.
    assert Rect(0, 0, 10, 10).intersection(Rect(10, 0, 10, 10)) is None


LEFT_CYCLE = CycleAction((Action.LEFT_HALF, Action.LEFT_THIRD, Action.LEFT_TWO_THIRDS))


def test_cycle_starts_at_its_first_step() -> None:
    assert cycle_step(LEFT_CYCLE, Action.NONE, None, advance=True) is Action.LEFT_HALF
    assert cycle_step(LEFT_CYCLE, Action.NONE, None, advance=False) is Action.LEFT_HALF
    assert cycle_step(LEFT_CYCLE, Action.RIGHT_HALF, None, advance=True) is Action.LEFT_HALF


def test_cycle_advances_only_when_asked() -> None:
    assert cycle_step(LEFT_CYCLE, Action.LEFT_HALF, None, advance=False) is Action.LEFT_HALF
    assert cycle_step(LEFT_CYCLE, Action.LEFT_HALF, None, advance=True) is Action.LEFT_THIRD
    assert cycle_step(LEFT_CYCLE, Action.LEFT_THIRD, None, advance=True) is Action.LEFT_TWO_THIRDS
    assert cycle_step(LEFT_CYCLE, Action.LEFT_TWO_THIRDS, None, advance=True) is Action.LEFT_HALF


def test_cycle_steps_backwards() -> None:
    step = cycle_step(LEFT_CYCLE, Action.LEFT_HALF, None, advance=True, backward=True)
    assert step is Action.LEFT_TWO_THIRDS
    step = cycle_step(LEFT_CYCLE, Action.NONE, None, advance=True, backward=True)
    assert step is Action.LEFT_TWO_THIRDS


def test_cycle_continues_after_what_the_window_is_snapped_to() -> None:
    recorded = Action.LEFT_HALF
    assert cycle_step(LEFT_CYCLE, Action.NONE, recorded, advance=True) is Action.LEFT_THIRD
    # Pointing at the sector is enough to move on from the current size.
    assert cycle_step(LEFT_CYCLE, Action.NONE, recorded, advance=False) is Action.LEFT_THIRD
    # A snap from another cycle does not count.
    assert cycle_step(LEFT_CYCLE, Action.NONE, Action.TOP_HALF, advance=True) is Action.LEFT_HALF
    restarted = cycle_step(LEFT_CYCLE, Action.NONE, recorded, advance=True, restart=True)
    assert restarted is Action.LEFT_HALF


def test_action_names() -> None:
    assert action_name(Action.LEFT_HALF) == "left_half"
    assert action_name(CustomAction("mine", FractionRect(0, 0, 1, 1))) == "mine"
    assert action_name(LEFT_CYCLE) == "cycle(left_half, left_third, left_two_thirds)"
    with pytest.raises(ValueError, match="at least one"):
        CycleAction(())


def test_repeatable_actions() -> None:
    assert is_repeatable(Action.LARGER)
    assert is_repeatable(Action.MOVE_LEFT)
    assert is_repeatable(Action.UNDO)
    assert not is_repeatable(Action.LEFT_HALF)


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Action.TOP_HALF, 0),
        (Action.TOP_THIRD, 0),
        (Action.TOP_RIGHT_QUARTER, 45),
        (Action.RIGHT_HALF, 90),
        (Action.RIGHT_THIRD, 90),
        (Action.FOURTH_FOURTH, 90),
        (Action.BOTTOM_RIGHT_QUARTER, 135),
        (Action.BOTTOM_TWO_THIRDS, 180),
        (Action.BOTTOM_LEFT_QUARTER, 225),
        (Action.LEFT_TWO_THIRDS, 270),
        (Action.TOP_LEFT_QUARTER, 315),
    ],
)
def test_radial_angle(action: Action, expected: float) -> None:
    assert radial_angle(action) == pytest.approx(expected)
    assert not fills_radial_menu(action)


@pytest.mark.parametrize(
    "action",
    [Action.MAXIMIZE, Action.CENTER, Action.MACOS_CENTER, Action.HORIZONTAL_CENTER_HALF],
)
def test_centered_actions_fill_the_ring(action: Action) -> None:
    assert fills_radial_menu(action)
    assert radial_angle(action) is None


def test_actions_without_a_place_on_the_ring() -> None:
    for action in (Action.MINIMIZE, Action.UNDO, Action.LARGER, Action.NEXT_SCREEN):
        assert radial_angle(action) is None
        assert not fills_radial_menu(action)
    assert region_angle(FRACTIONS[Action.HORIZONTAL_CENTER_THIRD]) is None


@pytest.mark.parametrize(
    ("dx", "dy", "expected"),
    [
        (0, 0, None),
        (7, 7, None),
        (15, 0, Direction.CENTER),
        (0, -27, Direction.CENTER),
        (0, -29, Direction.NORTH),
        (30, -30, Direction.NORTH_EAST),
        (100, 0, Direction.EAST),
        (70, 70, Direction.SOUTH_EAST),
        (0, 100, Direction.SOUTH),
        (-70, 70, Direction.SOUTH_WEST),
        (-100, 0, Direction.WEST),
        (-70, -70, Direction.NORTH_WEST),
        # Just inside either edge of the 45 degree wide east sector.
        (100, -41, Direction.EAST),
        (100, 41, Direction.EAST),
        (100, -42, Direction.NORTH_EAST),
        (100, 42, Direction.SOUTH_EAST),
    ],
)
def test_direction_at(dx: float, dy: float, expected: Direction | None) -> None:
    assert direction_at(dx, dy, radius=50, thickness=22) is expected


def test_sector_angles() -> None:
    assert sector_angle(Direction.NORTH) == 0
    assert sector_angle(Direction.EAST) == 90
    assert sector_angle(Direction.NORTH_WEST) == 315


def test_stash_frames() -> None:
    area = Rect(0, 0, 1920, 1040)
    assert stash_frames(Action.STASH_LEFT, WINDOW, area, 20) == (
        Rect(0, 200, 800, 600),
        Rect(-780, 200, 800, 600),
    )
    assert stash_frames(Action.STASH_RIGHT, WINDOW, area, 20) == (
        Rect(1120, 200, 800, 600),
        Rect(1900, 200, 800, 600),
    )
    assert stash_frames(Action.STASH_BOTTOM, WINDOW, area, 20) == (
        Rect(100, 440, 800, 600),
        Rect(100, 1020, 800, 600),
    )
    # The strip is never more than a fifth of the window.
    narrow = Rect(500, 500, 50, 300)
    assert stash_frames(Action.STASH_LEFT, narrow, area, 20)[1].right == 10
    # The preview of a stash action is where the window slides out to.
    assert target_rect(Action.STASH_RIGHT, area, WINDOW) == Rect(1120, 200, 800, 600)
    assert radial_angle(Action.STASH_LEFT) == 270


def test_stash_conflict() -> None:
    tall = Rect(0, 100, 800, 600)
    assert stash_conflict(tall, Rect(0, 150, 600, 300), vertical=True)
    assert stash_conflict(tall, Rect(0, 650, 600, 120), vertical=True)
    assert not stash_conflict(tall, Rect(0, 650, 600, 300), vertical=True)
    assert not stash_conflict(tall, Rect(0, 800, 600, 200), vertical=True)
    assert not stash_conflict(Rect(0, 0, 400, 300), Rect(900, 0, 400, 300), vertical=False)
