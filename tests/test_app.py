"""The hold-to-snap state machine, driven without a display."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QObject, Signal

from looplinux.actions import Action, Direction, Rect
from looplinux.app import Controller
from looplinux.backends.base import Monitor, Window
from looplinux.config import Config
from looplinux.history import History
from looplinux.overlay import Highlight
from test_executor import LEFT, START, FakeBackend, make_backend

KEY_X = 0x58
KEY_ESC = 0x01000000
KEY_ENTER = 0x01000004
KEY_LEFT = 0x01000012
KEY_UP = 0x01000013
KEY_RIGHT = 0x01000014
KEY_SPACE = 0x20

# FakeBackend reports the cursor at 0,0, so that is where the menu opens.
WORK_AREA = Rect(0, 0, 1920, 1040)


class FakeOverlay(QObject):
    pointer_moved = Signal(float, float)
    key_pressed = Signal(int, bool)
    key_released = Signal(int, int)
    clicked = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.visible = False
        self.highlight = Highlight.NONE
        self.angle = 0.0
        self.target: Rect | None = None

    def show(self, monitor: Monitor, center: tuple[int, int]) -> None:
        self.visible = True
        self.highlight, self.target = Highlight.NONE, None

    def select(self, highlight: Highlight, angle: float, target: Rect | None) -> None:
        self.highlight, self.angle, self.target = highlight, angle, target

    def hide(self) -> None:
        self.visible = False


class Rig:
    def __init__(self, config: Config, backend: FakeBackend) -> None:
        self.backend = backend
        self.overlay = FakeOverlay()
        self.history = History()
        self.controller = Controller(
            config,
            backend,
            self.overlay,
            self.history,
            KEY_X,
        )

    def point(self, dx: float, dy: float) -> None:
        self.overlay.pointer_moved.emit(dx, dy)

    def key(self, key: int, shift: bool = False) -> None:
        self.overlay.key_pressed.emit(key, shift)

    def release_key(self, key: int, scan_code: int = 0) -> None:
        self.overlay.key_released.emit(key, scan_code)

    def tap(self, key: int, shift: bool = False) -> None:
        self.key(key, shift)
        self.release_key(key)

    def click(self, secondary: bool = False) -> None:
        self.overlay.clicked.emit(secondary)

    @property
    def geometry(self) -> Rect:
        return self.backend.geometry


@pytest.fixture(scope="module")
def qt_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def rig(qt_app: QCoreApplication) -> Rig:
    rig = Rig(Config(), make_backend())
    rig.controller.press()
    return rig


def test_opening_selects_nothing(rig: Rig) -> None:
    assert rig.overlay.visible
    assert rig.overlay.highlight is Highlight.NONE
    assert rig.overlay.target is None
    rig.controller.release()
    assert not rig.overlay.visible
    assert rig.geometry == START


def test_pointing_at_a_sector_previews_and_release_applies(rig: Rig) -> None:
    rig.point(-80, 0)
    assert (rig.overlay.highlight, rig.overlay.angle) == (Highlight.SEGMENT, 270)
    assert rig.overlay.target == Rect(0, 0, 960, 1040)
    assert rig.geometry == START
    rig.controller.release()
    assert rig.geometry == Rect(0, 0, 960, 1040)


def test_diagonals_are_quarters(rig: Rig) -> None:
    rig.point(60, -60)
    assert (rig.overlay.highlight, rig.overlay.angle) == (Highlight.SEGMENT, 45)
    assert rig.overlay.target == Rect(960, 0, 960, 520)


def test_inside_the_ring_is_the_center_action(rig: Rig) -> None:
    rig.point(15, 5)
    assert rig.overlay.highlight is Highlight.FULL
    assert rig.overlay.target == WORK_AREA
    # A click steps to the second action of the center cycle.
    rig.click()
    assert rig.overlay.highlight is Highlight.FULL
    assert rig.overlay.target is not None
    assert (rig.overlay.target.width, rig.overlay.target.height) == (800, 600)
    rig.controller.release()
    assert rig.geometry.width == 800


def test_returning_to_the_start_deselects(rig: Rig) -> None:
    rig.point(-80, 0)
    rig.point(2, 2)
    assert rig.overlay.highlight is Highlight.NONE
    assert rig.overlay.target is None
    rig.controller.release()
    assert rig.geometry == START


def test_click_steps_through_the_pointed_cycle(rig: Rig) -> None:
    rig.point(-80, 0)
    rig.click()
    assert rig.overlay.target == Rect(0, 0, 640, 1040)
    rig.click()
    assert rig.overlay.target == Rect(0, 0, 1280, 1040)
    rig.click()
    assert rig.overlay.target == Rect(0, 0, 960, 1040)
    # Moving within the same sector does not step.
    rig.point(-90, 10)
    assert rig.overlay.target == Rect(0, 0, 960, 1040)
    # A click on something that is not a cycle changes nothing.
    rig.point(60, -60)
    rig.click()
    assert rig.overlay.target == Rect(960, 0, 960, 520)


def test_arrow_keys_cycle_and_shift_goes_back(rig: Rig) -> None:
    rig.tap(KEY_LEFT)
    assert rig.overlay.target == Rect(0, 0, 960, 1040)
    assert (rig.overlay.highlight, rig.overlay.angle) == (Highlight.SEGMENT, 270)
    rig.tap(KEY_LEFT)
    assert rig.overlay.target == Rect(0, 0, 640, 1040)
    rig.tap(KEY_LEFT, shift=True)
    assert rig.overlay.target == Rect(0, 0, 960, 1040)
    rig.tap(KEY_RIGHT)
    assert rig.overlay.target == Rect(960, 0, 960, 1040)


def test_two_arrows_together_pick_a_quarter(rig: Rig) -> None:
    rig.key(KEY_UP)
    assert rig.overlay.target == Rect(0, 0, 1920, 520)
    rig.key(KEY_LEFT)
    assert rig.overlay.target == Rect(0, 0, 960, 520)
    assert (rig.overlay.highlight, rig.overlay.angle) == (Highlight.SEGMENT, 315)
    rig.controller.release()
    assert rig.geometry == Rect(0, 0, 960, 520)


def test_space_maximizes_and_enter_centers(rig: Rig) -> None:
    rig.tap(KEY_SPACE)
    assert rig.overlay.highlight is Highlight.FULL
    assert rig.overlay.target == WORK_AREA
    rig.tap(KEY_ENTER)
    assert rig.overlay.highlight is Highlight.FULL
    assert rig.overlay.target == Rect(560, 220, 800, 600)


def test_key_selection_survives_pointer_jitter(rig: Rig) -> None:
    rig.point(-80, 0)
    rig.tap(KEY_SPACE)
    rig.point(-83, 2)
    assert rig.overlay.target == WORK_AREA
    rig.point(-80, 60)
    assert rig.overlay.target == Rect(0, 520, 960, 520)


def test_escape_and_right_click_cancel(rig: Rig) -> None:
    rig.point(-80, 0)
    rig.key(KEY_ESC)
    assert not rig.overlay.visible
    rig.controller.release()
    assert rig.geometry == START

    rig.controller.press()
    rig.point(-80, 0)
    rig.click(secondary=True)
    assert not rig.overlay.visible
    assert rig.geometry == START


def test_releasing_the_trigger_key_applies(rig: Rig) -> None:
    rig.point(80, 0)
    rig.key(KEY_X)  # the trigger key itself is never an action
    assert rig.overlay.target == Rect(960, 0, 960, 1040)
    rig.release_key(KEY_X)
    assert not rig.overlay.visible
    assert rig.geometry == Rect(960, 0, 960, 1040)


def test_a_cycle_continues_from_the_size_the_window_has(rig: Rig) -> None:
    rig.point(-80, 0)
    rig.controller.release()
    assert rig.geometry == Rect(0, 0, 960, 1040)

    rig.controller.press()
    rig.point(-80, 0)
    assert rig.overlay.target == Rect(0, 0, 640, 1040)
    rig.controller.release()
    assert rig.geometry == Rect(0, 0, 640, 1040)


def test_incremental_actions_repeat_on_click(qt_app: QCoreApplication) -> None:
    config = Config()
    config.radial.sectors[Direction.EAST] = Action.GROW_RIGHT
    rig = Rig(config, make_backend())
    rig.controller.press()
    rig.point(80, 0)
    assert rig.overlay.target == Rect(100, 200, 820, 600)
    assert rig.overlay.highlight is Highlight.SEGMENT
    rig.point(85, 3)
    assert rig.overlay.target == Rect(100, 200, 820, 600)
    rig.click()
    rig.click()
    assert rig.overlay.target == Rect(100, 200, 860, 600)
    rig.controller.release()
    assert rig.geometry == Rect(100, 200, 860, 600)


def test_excluded_and_missing_windows_do_not_open_the_menu(qt_app: QCoreApplication) -> None:
    rig = Rig(Config(excluded_apps=("konsole",)), make_backend())
    rig.controller.press()
    assert not rig.overlay.visible

    rig = Rig(Config(), FakeBackend(None))
    rig.controller.press()
    assert not rig.overlay.visible


def test_stateful_actions_apply_on_release(qt_app: QCoreApplication) -> None:
    config = Config()
    config.radial.sectors[Direction.SOUTH] = Action.MINIMIZE
    backend = FakeBackend(Window("w1", "konsole", "Terminal", START), [LEFT])
    rig = Rig(config, backend)
    rig.controller.press()
    rig.point(0, 80)
    assert rig.overlay.target is None
    assert rig.overlay.highlight is Highlight.SEGMENT
    assert backend.minimized == []
    rig.controller.release()
    assert backend.minimized == ["w1"]


def test_a_single_trigger_key_is_recognised_by_its_scan_code(qt_app: QCoreApplication) -> None:
    key_control, right_ctrl, left_ctrl = 0x01000021, 105, 37
    rig = Rig(Config(), make_backend())
    rig.controller = Controller(Config(), rig.backend, rig.overlay, rig.history, -1, right_ctrl)
    rig.controller.press()
    rig.point(80, 0)
    rig.release_key(key_control, left_ctrl)
    assert rig.overlay.visible
    rig.release_key(key_control, right_ctrl)
    assert not rig.overlay.visible
    assert rig.geometry == Rect(960, 0, 960, 1040)
