from collections.abc import Sequence
from pathlib import Path

import pytest

from looplinux.actions import Action, CustomAction, CycleAction, FractionRect, Gaps, Rect
from looplinux.backends.base import Monitor, StashEntry, Window, WindowBackend
from looplinux.config import Config, MonitorOverride
from looplinux.executor import (
    ActionError,
    compute_target,
    monitor_at,
    monitor_for_rect,
    perform,
    recorded_action,
)
from looplinux.history import History

LEFT = Monitor("DP-1", Rect(0, 0, 1920, 1080))
RIGHT = Monitor("DP-2", Rect(1920, 0, 2560, 1440))
START = Rect(100, 200, 800, 600)


class FakeBackend(WindowBackend):
    name = "fake"

    def __init__(self, window: Window | None, monitors: list[Monitor] | None = None) -> None:
        self.window = window
        self.monitors = monitors or [LEFT]
        self.others: list[Window] = []
        self.minimized: list[str] = []
        self.desktop = 0
        self.watched: list[str] = []

    def get_active_window(self) -> Window | None:
        return self.window

    def get_windows(self) -> list[Window]:
        return [*self.others, *([self.window] if self.window else [])]

    def get_monitors(self) -> list[Monitor]:
        return self.monitors

    def get_work_area(self, monitor: Monitor) -> Rect:
        # Every monitor has a 40 px panel at the bottom.
        area = monitor.geometry
        return Rect(area.x, area.y, area.width, area.height - 40)

    def get_cursor_position(self) -> tuple[int, int]:
        return (0, 0)

    def set_geometry(self, window: Window, rect: Rect) -> None:
        assert self.window is not None
        self.window = Window(window.id, window.app_id, window.title, rect)

    def set_fullscreen(self, window: Window, fullscreen: bool) -> None:
        assert self.window is not None
        self.window = Window(window.id, window.app_id, window.title, window.geometry, fullscreen)

    def minimize(self, window: Window) -> None:
        self.minimized.append(window.id)

    def move_to_desktop(self, window: Window, offset: int) -> None:
        self.desktop += offset

    def watch_stash(self, entries: Sequence[StashEntry], hide: Sequence[str] = ()) -> None:
        self.watched = [entry.window_id for entry in entries]
        # The real backend slides the hidden windows out of sight.
        for entry in entries:
            if entry.window_id in hide and self.window and self.window.id == entry.window_id:
                self.set_geometry(self.window, entry.stashed)

    @property
    def geometry(self) -> Rect:
        assert self.window is not None
        return self.window.geometry


def make_backend(monitors: list[Monitor] | None = None, geometry: Rect = START) -> FakeBackend:
    return FakeBackend(Window("w1", "konsole", "Terminal", geometry), monitors)


def test_snap_uses_the_work_area() -> None:
    backend = make_backend()
    assert perform(Action.LEFT_HALF, backend, Config(), History()) == Rect(0, 0, 960, 1040)
    assert backend.geometry == Rect(0, 0, 960, 1040)


def test_snap_applies_configured_gaps_and_monitor_overrides() -> None:
    config = Config(gaps=Gaps(outer=10), monitors={"DP-2": MonitorOverride(outer_gap=20)})
    backend = make_backend([LEFT, RIGHT])
    assert perform(Action.MAXIMIZE, backend, config, History()) == Rect(10, 10, 1900, 1020)
    perform(Action.MAXIMIZE, backend, config, History(), monitor=RIGHT)
    assert backend.geometry == Rect(1940, 20, 2520, 1360)


def test_custom_action() -> None:
    backend = make_backend()
    column = CustomAction("column", FractionRect(0.25, 0, 0.5, 1))
    assert perform(column, backend, Config(), History()) == Rect(480, 0, 960, 1040)


def test_window_snaps_on_the_monitor_it_is_on() -> None:
    backend = make_backend([LEFT, RIGHT], Rect(2500, 100, 800, 600))
    perform(Action.RIGHT_HALF, backend, Config(), History())
    assert backend.geometry == Rect(3200, 0, 1280, 1400)


def test_undo_walks_back_one_step_at_a_time() -> None:
    backend, history = make_backend(), History()
    perform(Action.LEFT_HALF, backend, Config(), history)
    perform(Action.TOP_RIGHT_QUARTER, backend, Config(), history)
    perform(Action.UNDO, backend, Config(), history)
    assert backend.geometry == Rect(0, 0, 960, 1040)
    perform(Action.UNDO, backend, Config(), history)
    assert backend.geometry == START
    with pytest.raises(ActionError, match="Nothing to undo"):
        perform(Action.UNDO, backend, Config(), history)


def test_restore_returns_to_the_geometry_before_the_first_snap() -> None:
    backend, history = make_backend(), History()
    perform(Action.LEFT_HALF, backend, Config(), history)
    perform(Action.MAXIMIZE, backend, Config(), history)
    assert perform(Action.INITIAL_FRAME, backend, Config(), history) == START
    assert backend.geometry == START
    with pytest.raises(ActionError, match="no initial frame"):
        perform(Action.INITIAL_FRAME, backend, Config(), history)


def test_history_survives_between_processes(tmp_path: Path) -> None:
    path = tmp_path / "state" / "history.json"
    backend = make_backend()
    perform(Action.LEFT_HALF, backend, Config(), History(path))
    perform(Action.MAXIMIZE, backend, Config(), History(path))
    perform(Action.UNDO, backend, Config(), History(path))
    assert backend.geometry == Rect(0, 0, 960, 1040)
    perform(Action.INITIAL_FRAME, backend, Config(), History(path))
    assert backend.geometry == START


def test_damaged_history_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    for content in ("not json", "[]", '{"original": {"w1": [1]}, "undo": {}}'):
        path.write_text(content)
        assert History(path).original("w1") is None


def test_history_is_bounded() -> None:
    history = History()
    for index in range(200):
        history.record(f"w{index}", START)
    assert history.original("w0") is None
    assert history.original("w199") == START
    for index in range(100):
        history.record("w199", Rect(index, 0, 10, 10))
    steps = 0
    while history.pop_undo("w199") is not None:
        steps += 1
    assert steps == 16


def test_move_between_monitors() -> None:
    backend, history = make_backend([LEFT, RIGHT], Rect(0, 0, 960, 1040)), History()
    perform(Action.NEXT_SCREEN, backend, Config(), history)
    assert backend.geometry == Rect(1920, 0, 1280, 1400)
    perform(Action.NEXT_SCREEN, backend, Config(), history)
    assert backend.geometry == Rect(0, 0, 960, 1040)
    perform(Action.PREVIOUS_SCREEN, backend, Config(), history)
    assert backend.geometry == Rect(1920, 0, 1280, 1400)


def test_move_between_monitors_needs_two_monitors() -> None:
    with pytest.raises(ActionError, match="only one monitor"):
        perform(Action.NEXT_SCREEN, make_backend(), Config(), History())


def test_fullscreen_toggles() -> None:
    backend = make_backend()
    perform(Action.FULLSCREEN, backend, Config(), History())
    assert backend.window is not None
    assert backend.window.fullscreen
    perform(Action.FULLSCREEN, backend, Config(), History())
    assert not backend.window.fullscreen


def test_minimize() -> None:
    backend = make_backend()
    assert perform(Action.MINIMIZE, backend, Config(), History()) is None
    assert backend.minimized == ["w1"]


def test_minimize_others() -> None:
    backend = make_backend()
    backend.others = [Window("w2", "kate", "", START), Window("w3", "dolphin", "", START)]
    perform(Action.MINIMIZE_OTHERS, backend, Config(), History())
    assert backend.minimized == ["w2", "w3"]


def test_move_between_desktops() -> None:
    backend = make_backend()
    perform(Action.NEXT_DESKTOP, backend, Config(), History())
    perform(Action.NEXT_DESKTOP, backend, Config(), History())
    perform(Action.PREVIOUS_DESKTOP, backend, Config(), History())
    assert backend.desktop == 1


def test_directional_screens() -> None:
    backend = make_backend([LEFT, RIGHT], Rect(0, 0, 960, 1040))
    perform(Action.RIGHT_SCREEN, backend, Config(), History())
    assert backend.geometry == Rect(1920, 0, 1280, 1400)
    with pytest.raises(ActionError, match="no monitor in that direction"):
        perform(Action.RIGHT_SCREEN, backend, Config(), History())
    perform(Action.LEFT_SCREEN, backend, Config(), History())
    assert backend.geometry == Rect(0, 0, 960, 1040)


def test_cycle_from_the_command_line_advances_with_the_window() -> None:
    cycle = CycleAction((Action.LEFT_HALF, Action.LEFT_THIRD, Action.LEFT_TWO_THIRDS))
    backend, history = make_backend(), History()
    assert perform(cycle, backend, Config(), history) == Rect(0, 0, 960, 1040)
    assert recorded_action(Config(), history, backend.window) is Action.LEFT_HALF  # type: ignore[arg-type]
    assert perform(cycle, backend, Config(), history) == Rect(0, 0, 640, 1040)
    assert perform(cycle, backend, Config(), history) == Rect(0, 0, 1280, 1040)
    assert perform(cycle, backend, Config(), history) == Rect(0, 0, 960, 1040)


def test_a_window_moved_by_hand_no_longer_counts_as_snapped() -> None:
    cycle = CycleAction((Action.LEFT_HALF, Action.LEFT_THIRD))
    backend, history = make_backend(), History()
    perform(cycle, backend, Config(), history)
    assert backend.window is not None
    backend.window = Window("w1", "konsole", "Terminal", Rect(300, 300, 500, 500))
    assert recorded_action(Config(), history, backend.window) is None
    assert perform(cycle, backend, Config(), history) == Rect(0, 0, 960, 1040)


def test_incremental_actions() -> None:
    backend = make_backend()
    config = Config()
    assert perform(Action.LARGER, backend, config, History()) == Rect(80, 180, 840, 640)
    assert perform(Action.MOVE_RIGHT, backend, config, History()) == Rect(100, 180, 840, 640)
    assert backend.window is not None
    # Within one menu session, steps continue from the previewed frame.
    chained = compute_target(
        Action.GROW_RIGHT, backend, config, backend.window, LEFT, Rect(0, 0, 1920, 1040), START
    )
    assert chained == Rect(100, 200, 820, 600)


def test_fill_available_space_uses_the_other_windows() -> None:
    backend = make_backend(geometry=Rect(800, 300, 400, 300))
    backend.others = [Window("w2", "kate", "", Rect(0, 0, 600, 1040))]
    assert perform(Action.FILL_AVAILABLE_SPACE, backend, Config(), History()) == Rect(
        600, 0, 1320, 1040
    )


def test_none_does_nothing_even_without_a_window() -> None:
    assert perform(Action.NONE, FakeBackend(None), Config(), History()) is None


def test_no_active_window() -> None:
    with pytest.raises(ActionError, match="no active window"):
        perform(Action.MAXIMIZE, FakeBackend(None), Config(), History())


def test_excluded_apps_are_left_alone() -> None:
    backend = make_backend()
    config = Config(excluded_apps=("Konsole",))
    with pytest.raises(ActionError, match="exclusions"):
        perform(Action.MAXIMIZE, backend, config, History())
    assert backend.geometry == START


def test_monitor_lookup() -> None:
    monitors = [LEFT, RIGHT]
    assert monitor_for_rect(Rect(1800, 0, 400, 400), monitors) is RIGHT
    assert monitor_for_rect(Rect(1500, 0, 500, 400), monitors) is LEFT
    assert monitor_for_rect(Rect(-5000, 0, 10, 10), monitors) is LEFT
    assert monitor_at(1920, 0, monitors) is RIGHT
    assert monitor_at(-1, -1, monitors) is LEFT
    with pytest.raises(ActionError, match="No monitors"):
        monitor_at(0, 0, [])


def test_stash_hides_the_window_behind_the_edge() -> None:
    backend, history = make_backend(), History()
    # Slides out against the left edge of the work area...
    assert perform(Action.STASH_LEFT, backend, Config(), history) == Rect(0, 200, 800, 600)
    # ...and is left with a 20 px strip showing.
    assert backend.geometry == Rect(-780, 200, 800, 600)
    assert backend.watched == ["w1"]
    entry = history.stash_entry("w1")
    assert entry is not None
    assert (entry.restore, entry.revealed) == (START, Rect(0, 200, 800, 600))


def test_stash_right_and_bottom() -> None:
    backend = make_backend()
    perform(Action.STASH_RIGHT, backend, Config(), History())
    assert backend.geometry == Rect(1900, 200, 800, 600)
    backend = make_backend()
    perform(Action.STASH_BOTTOM, backend, Config(), History())
    assert backend.geometry == Rect(100, 1020, 800, 600)


def test_unstash_puts_the_window_back() -> None:
    backend, history = make_backend(), History()
    perform(Action.STASH_LEFT, backend, Config(), history)
    assert perform(Action.UNSTASH, backend, Config(), history) == START
    assert backend.geometry == START
    assert backend.watched == []
    with pytest.raises(ActionError, match="not stashed"):
        perform(Action.UNSTASH, backend, Config(), history)


def test_restashing_keeps_the_original_frame() -> None:
    backend, history = make_backend(), History()
    perform(Action.STASH_LEFT, backend, Config(), history)
    perform(Action.STASH_RIGHT, backend, Config(), history)
    assert backend.geometry == Rect(1900, 200, 800, 600)
    perform(Action.UNSTASH, backend, Config(), history)
    assert backend.geometry == START


def test_any_other_action_ends_the_stash() -> None:
    backend, history = make_backend(), History()
    perform(Action.STASH_LEFT, backend, Config(), history)
    perform(Action.RIGHT_HALF, backend, Config(), history)
    assert backend.geometry == Rect(960, 0, 960, 1040)
    assert history.stashed() == []
    assert backend.watched == []


def test_stash_state_survives_between_processes(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    backend = make_backend()
    perform(Action.STASH_LEFT, backend, Config(), History(path))
    assert [entry.window_id for entry in History(path).stashed()] == ["w1"]
    perform(Action.UNSTASH, backend, Config(), History(path))
    assert backend.geometry == START
    assert History(path).stashed() == []


def test_a_second_window_on_the_same_spot_replaces_the_first() -> None:
    backend, history = make_backend(), History()
    perform(Action.STASH_LEFT, backend, Config(), history)
    first = backend.window
    backend.window = Window("w2", "kate", "", Rect(300, 250, 700, 500))
    perform(Action.STASH_LEFT, backend, Config(), history)
    assert [entry.window_id for entry in history.stashed()] == ["w2"]
    assert first is not None

    # Far enough apart along the edge, both stay.
    backend.window = Window("w3", "dolphin", "", Rect(300, 0, 400, 150))
    perform(Action.STASH_LEFT, backend, Config(), history)
    assert [entry.window_id for entry in history.stashed()] == ["w2", "w3"]
