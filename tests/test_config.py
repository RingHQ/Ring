import tomllib
from pathlib import Path

import pytest

from looplinux.actions import Action, CustomAction, CycleAction, Direction, FractionRect, Gaps
from looplinux.config import (
    Config,
    ConfigError,
    MonitorOverride,
    load_config,
    parse_config,
    user_config_path,
)

DEFAULT_TOML = Path(__file__).resolve().parents[1] / "config" / "default.toml"


def parse(text: str) -> Config:
    return parse_config(tomllib.loads(text))


def key(*names: str) -> frozenset[str]:
    return frozenset(names)


def test_empty_config_yields_defaults() -> None:
    assert parse("") == Config()


def test_default_toml_matches_builtin_defaults() -> None:
    assert load_config(DEFAULT_TOML) == Config()


def test_defaults_follow_loop() -> None:
    config = Config()
    assert set(config.radial.sectors) == set(Direction)
    assert config.radial.sectors[Direction.CENTER] == CycleAction(
        (Action.MAXIMIZE, Action.MACOS_CENTER)
    )
    assert config.radial.sectors[Direction.WEST] == CycleAction(
        (Action.LEFT_HALF, Action.LEFT_THIRD, Action.LEFT_TWO_THIRDS)
    )
    assert config.radial.sectors[Direction.NORTH_EAST] is Action.TOP_RIGHT_QUARTER
    assert config.keybindings[key("KEY_SPACE")] is Action.MAXIMIZE
    assert config.keybindings[key("KEY_ENTER")] is Action.CENTER
    assert config.keybindings[key("KEY_UP", "KEY_LEFT")] is Action.TOP_LEFT_QUARTER
    assert config.keybindings[key("KEY_LEFT")] == config.radial.sectors[Direction.WEST]
    # WASD and Vim keys mirror the arrows.
    for up, left in (("KEY_W", "KEY_A"), ("KEY_K", "KEY_H")):
        assert config.keybindings[key(left)] == config.keybindings[key("KEY_LEFT")]
        assert config.keybindings[key(up)] == config.keybindings[key("KEY_UP")]
        assert config.keybindings[key(up, left)] is Action.TOP_LEFT_QUARTER
    assert config.keybindings[key("KEY_Q")] is Action.STASH_LEFT
    assert config.keybindings[key("KEY_R")] is Action.UNSTASH


def test_partial_config_keeps_other_defaults() -> None:
    config = parse(
        """
        [trigger]
        shortcut = "F13"

        [gaps]
        inner = 8

        [radial.sectors]
        north = "maximize"
        south = ["bottom_half", "minimize"]

        [keybindings]
        KEY_ENTER = "none"
        KEY_C = "center"
        KEY_F = ["first_fourth", "second_fourth"]
        "KEY_LEFT + KEY_RIGHT" = "horizontal_center_half"
        """
    )
    assert config.trigger.shortcut == "F13"
    assert config.trigger.cancel_key == "KEY_ESC"
    assert config.gaps == Gaps(outer=0, inner=8)
    assert config.radial.sectors[Direction.NORTH] is Action.MAXIMIZE
    assert config.radial.sectors[Direction.SOUTH] == CycleAction(
        (Action.BOTTOM_HALF, Action.MINIMIZE)
    )
    assert config.radial.sectors[Direction.EAST] == Config().radial.sectors[Direction.EAST]
    assert config.keybindings[key("KEY_ENTER")] is Action.NONE
    assert config.keybindings[key("KEY_C")] is Action.CENTER
    assert config.keybindings[key("KEY_F")] == CycleAction(
        (Action.FIRST_FOURTH, Action.SECOND_FOURTH)
    )
    assert config.keybindings[key("KEY_LEFT", "KEY_RIGHT")] is Action.HORIZONTAL_CENTER_HALF
    assert config.keybindings[key("KEY_SPACE")] is Action.MAXIMIZE


def test_all_sections() -> None:
    config = parse(
        """
        [radial]
        radius = 80
        thickness = 30

        [actions]
        almost_maximize_margin = 0.1
        size_increment = 50
        cycle_restart = true
        cycle_backwards_on_shift = false

        [animation]
        style = "instant"
        windows = false

        [stash]
        peek = 40
        animate = false
        shift_focus = false

        [theme]
        accent_color = "#FF000080"
        gradient_color = "#0000ff"
        blur = false

        [preview]
        padding = 0
        corner_radius = 0
        border_thickness = 2
        blur = true
        start = "radial_menu"

        [exclusions]
        apps = ["steam", "Gimp"]
        """
    )
    assert config.radial.radius == 80
    assert config.radial.thickness == 30
    assert config.actions.almost_maximize_margin == 0.1
    assert config.actions.size_increment == 50
    assert config.actions.cycle_restart is True
    assert config.actions.cycle_backwards_on_shift is False
    assert config.animation.style == "instant"
    assert config.animation.windows is False
    assert (config.stash.peek, config.stash.animate, config.stash.shift_focus) == (40, False, False)
    assert config.theme.accent_color == "#FF000080"
    assert config.theme.gradient_color == "#0000ff"
    assert config.theme.blur is False
    assert config.preview.padding == 0
    assert config.preview.border_thickness == 2
    assert config.preview.blur is True
    assert config.preview.start == "radial_menu"
    assert config.excluded_apps == ("steam", "Gimp")


def test_exclusions_are_case_insensitive() -> None:
    config = parse('[exclusions]\napps = ["Steam"]')
    assert config.is_excluded("steam")
    assert config.is_excluded("STEAM")
    assert not config.is_excluded("firefox")


def test_custom_actions_can_be_bound() -> None:
    config = parse(
        """
        [custom_actions.center_column]
        x = 0.25
        y = 0
        w = 0.5
        h = 1

        [radial.sectors]
        center = "center_column"

        [keybindings]
        KEY_X = ["center_column", "maximize"]
        """
    )
    expected = CustomAction("center_column", FractionRect(0.25, 0, 0.5, 1))
    assert config.custom_actions == {"center_column": expected}
    assert config.radial.sectors[Direction.CENTER] == expected
    assert config.keybindings[key("KEY_X")] == CycleAction((expected, Action.MAXIMIZE))
    assert config.leaf("center_column") == expected
    assert config.leaf("left_half") is Action.LEFT_HALF
    assert config.leaf("nope") is None


def test_monitor_overrides() -> None:
    config = parse(
        """
        [gaps]
        outer = 4
        inner = 6

        [monitors."DP-1"]
        outer_gap = 12

        [monitors."HDMI-A-1"]
        inner_gap = 0
        almost_maximize_margin = 0.2
        """
    )
    assert config.monitors["DP-1"] == MonitorOverride(outer_gap=12)
    assert config.gaps_for("DP-1") == Gaps(outer=12, inner=6)
    assert config.gaps_for("HDMI-A-1") == Gaps(outer=4, inner=0)
    assert config.gaps_for("eDP-1") == Gaps(outer=4, inner=6)
    assert config.almost_maximize_margin_for("DP-1") == 0.05
    assert config.almost_maximize_margin_for("HDMI-A-1") == 0.2
    assert config.almost_maximize_margin_for("eDP-1") == 0.05


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[trigger]\nkey = 5", "trigger.key: expected a string"),
        ('[trigger]\nkey = "rightctrl"', "trigger.key: 'rightctrl' is not an evdev key name"),
        ('[trigger]\nkey = "KEY_ESC"', "trigger.cancel_key: must differ"),
        ('[trigger]\nshortcut = " "', "trigger.shortcut: must not be empty"),
        ('[trigger]\nuse = "mouse"', "trigger.use: expected one of key, shortcut"),
        ('[trigger]\nkee = "KEY_A"', r"unknown configuration key\(s\): trigger.kee"),
        ("[typo]\nvalue = 1", r"unknown configuration key\(s\): typo"),
        ('trigger = "KEY_A"', "trigger: expected a table"),
        ("[gaps]\nouter = -1", "gaps.outer: must be at least 0"),
        ("[gaps]\nouter = 1.5", "gaps.outer: expected an integer"),
        ("[gaps]\nouter = true", "gaps.outer: expected an integer"),
        ("[radial]\nradius = 5", "radial.radius: must be at least 20"),
        ("[radial]\nthickness = 50", "radial.thickness: must be smaller than radial.radius"),
        ("[radial]\ndead_zone = 5", r"unknown configuration key\(s\): radial.dead_zone"),
        ('[radial.sectors]\nnorth = "explode"', "radial.sectors.north: unknown action 'explode'"),
        ('[radial.sectors]\nup = "maximize"', "radial.sectors.up: unknown sector"),
        ("[radial.sectors]\nnorth = 3", "radial.sectors.north: unknown action 3"),
        ("[radial.sectors]\nnorth = []", "radial.sectors.north: a cycle needs at least one"),
        (
            '[radial.sectors]\nnorth = ["top_half", "nope"]',
            "radial.sectors.north: unknown action 'nope'",
        ),
        (
            '[radial.sectors]\nnorth = ["top_half", "none"]',
            "radial.sectors.north: 'none' cannot be part of a cycle",
        ),
        ('[keybindings]\nenter = "maximize"', "keybindings.enter: 'enter' is not an evdev"),
        ('[keybindings]\n"KEY_A+b" = "maximize"', "keybindings.KEY_A\\+b: 'b' is not an evdev"),
        ('[keybindings]\nKEY_Q = "nope"', "keybindings.KEY_Q: unknown action 'nope'"),
        ('[keybindings]\nKEY_ESC = "center"', "keybindings.KEY_ESC: .* trigger.cancel_key"),
        (
            '[keybindings]\n"KEY_ESC+KEY_A" = "center"',
            r"keybindings.KEY_A\+KEY_ESC: .* trigger.cancel_key",
        ),
        ('[keybindings]\nKEY_RIGHTCTRL = "center"', "keybindings.KEY_RIGHTCTRL: .* trigger.key"),
        ("[actions]\nalmost_maximize_margin = 0.5", "actions.almost_maximize_margin: must be in"),
        ("[actions]\nsize_increment = 0", "actions.size_increment: must be at least 1"),
        ('[actions]\ncycle_restart = "yes"', "actions.cycle_restart: expected true or false"),
        ('[animation]\nstyle = "bouncy"', "animation.style: expected one of fluid, relaxed"),
        ("[animation]\nenabled = true", r"unknown configuration key\(s\): animation.enabled"),
        ('[theme]\naccent_color = "blue"', "theme.accent_color: expected"),
        ('[theme]\ngradient_color = "system"', "theme.gradient_color: expected"),
        ("[stash]\npeek = 0", "stash.peek: must be at least 1"),
        ('[preview]\nstart = "nowhere"', "preview.start: expected one of action_center"),
        ("[preview]\npadding = -1", "preview.padding: must be at least 0"),
        ('[exclusions]\napps = "steam"', "exclusions.apps: expected a list of strings"),
        ("[exclusions]\napps = [1]", "exclusions.apps: expected a list of strings"),
        ('[monitors."DP-1"]\ngap = 1', r"unknown configuration key\(s\): monitors.DP-1.gap"),
        ('[monitors."DP-1"]\nouter_gap = -2', "monitors.DP-1.outer_gap: must be at least 0"),
        ("[monitors]\nDP-1 = 3", "monitors.DP-1: expected a table"),
        ("[custom_actions.a]\nx = 0\ny = 0\nw = 1", "custom_actions.a: missing required key 'h'"),
        (
            "[custom_actions.a]\nx = 0.6\ny = 0\nw = 0.5\nh = 1",
            "custom_actions.a: x/width must describe",
        ),
        (
            '[custom_actions.a]\nx = "0"\ny = 0\nw = 1\nh = 1',
            "custom_actions.a.x: expected a number",
        ),
        (
            "[custom_actions.a]\nx = 0\ny = 0\nw = 1\nh = 1\nz = 1",
            r"unknown configuration key\(s\): custom_actions.a.z",
        ),
        (
            "[custom_actions.maximize]\nx = 0\ny = 0\nw = 1\nh = 1",
            "custom_actions.maximize: this name is already used",
        ),
        (
            "[custom_actions.Bad-Name]\nx = 0\ny = 0\nw = 1\nh = 1",
            "custom_actions.Bad-Name: custom action names must be",
        ),
    ],
)
def test_invalid_config(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse(text)


def test_rebound_trigger_frees_the_old_key() -> None:
    config = parse('[trigger]\nkey = "KEY_F13"\n[keybindings]\nKEY_RIGHTCTRL = "center"')
    assert config.keybindings[key("KEY_RIGHTCTRL")] is Action.CENTER


def test_trigger_cannot_shadow_a_default_binding() -> None:
    with pytest.raises(ConfigError, match=r"keybindings\.KEY_SPACE: .* trigger\.key"):
        parse('[trigger]\nkey = "KEY_SPACE"')
    config = parse('[trigger]\nkey = "KEY_SPACE"\n[keybindings]\nKEY_SPACE = "none"')
    assert config.trigger.key == "KEY_SPACE"


def test_load_config_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[gaps]\nouter = 16\n")
    assert load_config(path).gaps.outer == 16


def test_load_config_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[gaps]\nouter = -1\n")
    with pytest.raises(ConfigError, match=r"config\.toml: gaps\.outer"):
        load_config(path)

    path.write_text("[gaps\n")
    with pytest.raises(ConfigError, match=r"config\.toml: invalid TOML"):
        load_config(path)

    with pytest.raises(ConfigError, match=r"missing\.toml: config file not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_uses_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_config_path() == tmp_path / "looplinux" / "config.toml"
    assert load_config() == Config()

    user_config_path().parent.mkdir()
    user_config_path().write_text('[theme]\naccent_color = "#112233"\n')
    assert load_config().theme.accent_color == "#112233"
