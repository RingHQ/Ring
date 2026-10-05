"""Load and validate the TOML configuration.

The defaults live in the dataclasses below and mirror Loop's own defaults;
`config/default.toml` documents the same values and is checked against them
by the test suite. A user file only needs to contain the keys it wants to
change.
"""

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from looplinux.actions import (
    Action,
    ActionSpec,
    CustomAction,
    CycleAction,
    Direction,
    FractionRect,
    Gaps,
    Leaf,
)

_KEY_NAME = re.compile(r"(KEY|BTN)_[A-Z0-9_]+")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?")
_CUSTOM_ACTION_NAME = re.compile(r"[a-z][a-z0-9_]*")

# Per animation style, from Loop: the cubic bezier curve and duration (ms) of
# frame changes, and the duration of the ring's size change.
ANIMATIONS: dict[str, tuple[tuple[float, float, float, float], int, int]] = {
    "fluid": ((0, 0.26, 0.45, 1), 325, 200),
    "relaxed": ((0.15, 0.8, 0.46, 1), 300, 200),
    "snappy": ((0.22, 1, 0.47, 1), 250, 200),
    "brisk": ((0.25, 1, 0.48, 1), 150, 150),
    "instant": ((0, 0, 1, 1), 0, 100),
}
ANIMATION_STYLES = tuple(ANIMATIONS)
PREVIEW_STARTS = ("action_center", "radial_menu", "screen_center")

type Chord = frozenset[str]
"""Keys held together, as evdev key names."""


class ConfigError(ValueError):
    """The configuration is invalid; the message names the offending key."""


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    use: str = "key"
    key: str = "KEY_RIGHTCTRL"
    shortcut: str = "Meta+X"
    cancel_key: str = "KEY_ESC"


_TOP_CYCLE = CycleAction((Action.TOP_HALF, Action.TOP_THIRD, Action.TOP_TWO_THIRDS))
_BOTTOM_CYCLE = CycleAction((Action.BOTTOM_HALF, Action.BOTTOM_THIRD, Action.BOTTOM_TWO_THIRDS))
_RIGHT_CYCLE = CycleAction((Action.RIGHT_HALF, Action.RIGHT_THIRD, Action.RIGHT_TWO_THIRDS))
_LEFT_CYCLE = CycleAction((Action.LEFT_HALF, Action.LEFT_THIRD, Action.LEFT_TWO_THIRDS))


def _default_sectors() -> dict[Direction, ActionSpec]:
    return {
        Direction.CENTER: CycleAction((Action.MAXIMIZE, Action.MACOS_CENTER)),
        Direction.NORTH: _TOP_CYCLE,
        Direction.NORTH_EAST: Action.TOP_RIGHT_QUARTER,
        Direction.EAST: _RIGHT_CYCLE,
        Direction.SOUTH_EAST: Action.BOTTOM_RIGHT_QUARTER,
        Direction.SOUTH: _BOTTOM_CYCLE,
        Direction.SOUTH_WEST: Action.BOTTOM_LEFT_QUARTER,
        Direction.WEST: _LEFT_CYCLE,
        Direction.NORTH_WEST: Action.TOP_LEFT_QUARTER,
    }


# Direction keys as (up, down, left, right): arrows, WASD and Vim.
_DIRECTION_KEYS = (
    ("KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT"),
    ("KEY_W", "KEY_S", "KEY_A", "KEY_D"),
    ("KEY_K", "KEY_J", "KEY_H", "KEY_L"),
)


def _default_keybindings() -> dict[Chord, ActionSpec]:
    bindings: dict[Chord, ActionSpec] = {
        frozenset({"KEY_SPACE"}): Action.MAXIMIZE,
        frozenset({"KEY_ENTER"}): Action.CENTER,
        frozenset({"KEY_Q"}): Action.STASH_LEFT,
        frozenset({"KEY_E"}): Action.STASH_RIGHT,
        frozenset({"KEY_Z"}): Action.STASH_BOTTOM,
        frozenset({"KEY_R"}): Action.UNSTASH,
    }
    for up, down, left, right in _DIRECTION_KEYS:
        bindings[frozenset({up})] = _TOP_CYCLE
        bindings[frozenset({down})] = _BOTTOM_CYCLE
        bindings[frozenset({right})] = _RIGHT_CYCLE
        bindings[frozenset({left})] = _LEFT_CYCLE
        bindings[frozenset({up, left})] = Action.TOP_LEFT_QUARTER
        bindings[frozenset({up, right})] = Action.TOP_RIGHT_QUARTER
        bindings[frozenset({down, right})] = Action.BOTTOM_RIGHT_QUARTER
        bindings[frozenset({down, left})] = Action.BOTTOM_LEFT_QUARTER
    return bindings


@dataclass(frozen=True, slots=True)
class RadialConfig:
    radius: int = 50
    thickness: int = 22
    sectors: dict[Direction, ActionSpec] = field(default_factory=_default_sectors)


@dataclass(frozen=True, slots=True)
class ActionsConfig:
    almost_maximize_margin: float = 0.05
    size_increment: int = 20
    cycle_restart: bool = False
    cycle_backwards_on_shift: bool = True


@dataclass(frozen=True, slots=True)
class AnimationConfig:
    style: str = "snappy"
    windows: bool = True


@dataclass(frozen=True, slots=True)
class StashConfig:
    peek: int = 20
    animate: bool = True
    shift_focus: bool = True


@dataclass(frozen=True, slots=True)
class ThemeConfig:
    accent_color: str = "system"
    gradient_color: str = ""
    blur: bool = True


@dataclass(frozen=True, slots=True)
class PreviewConfig:
    padding: int = 10
    corner_radius: int = 10
    border_thickness: int = 4
    blur: bool = False
    start: str = "action_center"


@dataclass(frozen=True, slots=True)
class MonitorOverride:
    """Per-monitor settings; None means "use the global value"."""

    outer_gap: int | None = None
    inner_gap: int | None = None
    almost_maximize_margin: float | None = None


@dataclass(frozen=True, slots=True)
class Config:
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    radial: RadialConfig = field(default_factory=RadialConfig)
    keybindings: dict[Chord, ActionSpec] = field(default_factory=_default_keybindings)
    gaps: Gaps = field(default_factory=Gaps)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    stash: StashConfig = field(default_factory=StashConfig)
    excluded_apps: tuple[str, ...] = ()
    monitors: dict[str, MonitorOverride] = field(default_factory=dict)
    custom_actions: dict[str, CustomAction] = field(default_factory=dict)

    def gaps_for(self, monitor: str) -> Gaps:
        """Return the gaps to use on the named monitor."""
        override = self.monitors.get(monitor)
        if override is None:
            return self.gaps
        return Gaps(
            outer=self.gaps.outer if override.outer_gap is None else override.outer_gap,
            inner=self.gaps.inner if override.inner_gap is None else override.inner_gap,
        )

    def almost_maximize_margin_for(self, monitor: str) -> float:
        """Return the almost-maximize margin to use on the named monitor."""
        override = self.monitors.get(monitor)
        if override is None or override.almost_maximize_margin is None:
            return self.actions.almost_maximize_margin
        return override.almost_maximize_margin

    def is_excluded(self, app_id: str) -> bool:
        """Return whether windows of this application must be left alone."""
        return app_id.casefold() in {app.casefold() for app in self.excluded_apps}

    def leaf(self, name: str) -> Leaf | None:
        """Look up a built-in or custom action by name."""
        if name in self.custom_actions:
            return self.custom_actions[name]
        try:
            return Action(name)
        except ValueError:
            return None


class _Table:
    """A TOML table that reports type errors and leftover keys with their path."""

    def __init__(self, data: Mapping[str, object], path: str = "") -> None:
        self._data = dict(data)
        self._path = path

    def where(self, name: str) -> str:
        return f"{self._path}.{name}" if self._path else name

    def has(self, name: str) -> bool:
        return name in self._data

    def integer(self, name: str, default: int, *, minimum: int = 0) -> int:
        value = self._data.pop(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{self.where(name)}: expected an integer, got {value!r}")
        if value < minimum:
            raise ConfigError(f"{self.where(name)}: must be at least {minimum}, got {value}")
        return value

    def number(self, name: str, default: float) -> float:
        value = self._data.pop(name, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{self.where(name)}: expected a number, got {value!r}")
        return float(value)

    def boolean(self, name: str, default: bool) -> bool:
        value = self._data.pop(name, default)
        if not isinstance(value, bool):
            raise ConfigError(f"{self.where(name)}: expected true or false, got {value!r}")
        return value

    def string(self, name: str, default: str) -> str:
        value = self._data.pop(name, default)
        if not isinstance(value, str):
            raise ConfigError(f"{self.where(name)}: expected a string, got {value!r}")
        return value

    def choice(self, name: str, default: str, choices: tuple[str, ...]) -> str:
        value = self.string(name, default)
        if value not in choices:
            raise ConfigError(
                f"{self.where(name)}: expected one of {', '.join(choices)}; got {value!r}"
            )
        return value

    def string_list(self, name: str) -> tuple[str, ...]:
        value = self._data.pop(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{self.where(name)}: expected a list of strings, got {value!r}")
        return tuple(value)

    def table(self, name: str) -> "_Table":
        value = self._data.pop(name, {})
        if not isinstance(value, dict):
            raise ConfigError(f"{self.where(name)}: expected a table, got {value!r}")
        return _Table(value, self.where(name))

    def subtables(self) -> list[tuple[str, "_Table"]]:
        """Consume every remaining key as a named sub-table."""
        names = list(self._data)
        return [(name, self.table(name)) for name in names]

    def items(self) -> list[tuple[str, object]]:
        """Consume every remaining key as a free-form entry."""
        entries = list(self._data.items())
        self._data.clear()
        return entries

    def finish(self) -> None:
        """Reject keys nobody asked for, which are almost always typos."""
        if self._data:
            unknown = ", ".join(sorted(self.where(name) for name in self._data))
            raise ConfigError(f"unknown configuration key(s): {unknown}")


def _key_name(table: _Table, name: str, default: str) -> str:
    value = table.string(name, default)
    _check_key_name(value, table.where(name))
    return value


def _check_key_name(value: str, where: str) -> None:
    if not _KEY_NAME.fullmatch(value):
        raise ConfigError(
            f"{where}: {value!r} is not an evdev key name; use names such as "
            "KEY_RIGHTCTRL, KEY_F13 or BTN_SIDE"
        )


def _margin(table: _Table, name: str, default: float) -> float:
    value = table.number(name, default)
    if not 0 <= value < 0.5:
        raise ConfigError(
            f"{table.where(name)}: must be in the range 0 <= value < 0.5, got {value}"
        )
    return value


def _color(table: _Table, name: str, default: str, *, also: str) -> str:
    value = table.string(name, default)
    if value != also and not _COLOR.fullmatch(value):
        raise ConfigError(
            f'{table.where(name)}: expected "{also}", #RRGGBB or #RRGGBBAA, got {value!r}'
        )
    return value


def _resolve_leaf(value: object, where: str, custom: Mapping[str, CustomAction]) -> Leaf:
    if isinstance(value, str):
        if value in custom:
            return custom[value]
        try:
            return Action(value)
        except ValueError:
            pass
    valid = ", ".join([*(action.value for action in Action), *custom])
    raise ConfigError(f"{where}: unknown action {value!r}; valid actions are: {valid}")


def _resolve_action(value: object, where: str, custom: Mapping[str, CustomAction]) -> ActionSpec:
    """Resolve an action name, or a list of names meaning a cycle."""
    if not isinstance(value, list):
        return _resolve_leaf(value, where, custom)
    if not value:
        raise ConfigError(f"{where}: a cycle needs at least one action")
    steps = tuple(_resolve_leaf(item, where, custom) for item in value)
    if Action.NONE in steps:
        raise ConfigError(f"{where}: 'none' cannot be part of a cycle")
    return CycleAction(steps)


def _parse_custom_actions(table: _Table) -> dict[str, CustomAction]:
    builtin = {action.value for action in Action}
    actions: dict[str, CustomAction] = {}
    for name, entry in table.subtables():
        where = table.where(name)
        if not _CUSTOM_ACTION_NAME.fullmatch(name):
            raise ConfigError(
                f"{where}: custom action names must be lowercase letters, digits and underscores"
            )
        if name in builtin:
            raise ConfigError(f"{where}: this name is already used by a built-in action")
        for required in ("x", "y", "w", "h"):
            if not entry.has(required):
                raise ConfigError(f"{where}: missing required key {required!r}")
        x, y, w, h = (entry.number(key, 0.0) for key in ("x", "y", "w", "h"))
        entry.finish()
        try:
            region = FractionRect(x, y, w, h)
        except ValueError as error:
            raise ConfigError(f"{where}: {error}") from error
        actions[name] = CustomAction(name, region)
    return actions


def _parse_sectors(
    table: _Table, custom: Mapping[str, CustomAction]
) -> dict[Direction, ActionSpec]:
    sectors = _default_sectors()
    for name, value in table.items():
        where = table.where(name)
        try:
            direction = Direction(name)
        except ValueError:
            valid = ", ".join(direction.value for direction in Direction)
            raise ConfigError(f"{where}: unknown sector; valid sectors are: {valid}") from None
        sectors[direction] = _resolve_action(value, where, custom)
    return sectors


def _parse_keybindings(
    table: _Table, custom: Mapping[str, CustomAction]
) -> dict[Chord, ActionSpec]:
    bindings = _default_keybindings()
    for name, value in table.items():
        where = table.where(name)
        keys = [key.strip() for key in name.split("+")]
        for key in keys:
            _check_key_name(key, where)
        bindings[frozenset(keys)] = _resolve_action(value, where, custom)
    return bindings


def _parse_monitors(table: _Table) -> dict[str, MonitorOverride]:
    monitors: dict[str, MonitorOverride] = {}
    for name, entry in table.subtables():
        monitors[name] = MonitorOverride(
            outer_gap=entry.integer("outer_gap", 0) if entry.has("outer_gap") else None,
            inner_gap=entry.integer("inner_gap", 0) if entry.has("inner_gap") else None,
            almost_maximize_margin=(
                _margin(entry, "almost_maximize_margin", 0.0)
                if entry.has("almost_maximize_margin")
                else None
            ),
        )
        entry.finish()
    return monitors


def parse_config(data: Mapping[str, object]) -> Config:
    """Validate parsed TOML data and fill in defaults for everything missing."""
    root = _Table(data)
    defaults = Config()

    custom_actions = _parse_custom_actions(root.table("custom_actions"))

    trigger_table = root.table("trigger")
    trigger = TriggerConfig(
        use=trigger_table.choice("use", defaults.trigger.use, ("key", "shortcut")),
        key=_key_name(trigger_table, "key", defaults.trigger.key),
        shortcut=trigger_table.string("shortcut", defaults.trigger.shortcut),
        cancel_key=_key_name(trigger_table, "cancel_key", defaults.trigger.cancel_key),
    )
    trigger_table.finish()
    if not trigger.shortcut.strip():
        raise ConfigError("trigger.shortcut: must not be empty")
    if trigger.key == trigger.cancel_key:
        raise ConfigError("trigger.cancel_key: must differ from trigger.key")

    radial_table = root.table("radial")
    radial = RadialConfig(
        radius=radial_table.integer("radius", defaults.radial.radius, minimum=20),
        thickness=radial_table.integer("thickness", defaults.radial.thickness, minimum=1),
        sectors=_parse_sectors(radial_table.table("sectors"), custom_actions),
    )
    radial_table.finish()
    if radial.thickness >= radial.radius:
        raise ConfigError("radial.thickness: must be smaller than radial.radius")

    keybindings = _parse_keybindings(root.table("keybindings"), custom_actions)
    for reserved, owner in (
        (trigger.key, "trigger.key"),
        (trigger.cancel_key, "trigger.cancel_key"),
    ):
        for chord, action in keybindings.items():
            if reserved in chord and action is not Action.NONE:
                name = "+".join(sorted(chord))
                raise ConfigError(f"keybindings.{name}: {reserved} is already used as {owner}")

    gaps_table = root.table("gaps")
    gaps = Gaps(
        outer=gaps_table.integer("outer", defaults.gaps.outer),
        inner=gaps_table.integer("inner", defaults.gaps.inner),
    )
    gaps_table.finish()

    actions_table = root.table("actions")
    actions = ActionsConfig(
        almost_maximize_margin=_margin(
            actions_table, "almost_maximize_margin", defaults.actions.almost_maximize_margin
        ),
        size_increment=actions_table.integer(
            "size_increment", defaults.actions.size_increment, minimum=1
        ),
        cycle_restart=actions_table.boolean("cycle_restart", defaults.actions.cycle_restart),
        cycle_backwards_on_shift=actions_table.boolean(
            "cycle_backwards_on_shift", defaults.actions.cycle_backwards_on_shift
        ),
    )
    actions_table.finish()

    animation_table = root.table("animation")
    animation = AnimationConfig(
        style=animation_table.choice("style", defaults.animation.style, ANIMATION_STYLES),
        windows=animation_table.boolean("windows", defaults.animation.windows),
    )
    animation_table.finish()

    theme_table = root.table("theme")
    theme = ThemeConfig(
        accent_color=_color(
            theme_table, "accent_color", defaults.theme.accent_color, also="system"
        ),
        gradient_color=_color(
            theme_table, "gradient_color", defaults.theme.gradient_color, also=""
        ),
        blur=theme_table.boolean("blur", defaults.theme.blur),
    )
    theme_table.finish()

    preview_table = root.table("preview")
    preview = PreviewConfig(
        padding=preview_table.integer("padding", defaults.preview.padding),
        corner_radius=preview_table.integer("corner_radius", defaults.preview.corner_radius),
        border_thickness=preview_table.integer(
            "border_thickness", defaults.preview.border_thickness
        ),
        blur=preview_table.boolean("blur", defaults.preview.blur),
        start=preview_table.choice("start", defaults.preview.start, PREVIEW_STARTS),
    )
    preview_table.finish()

    stash_table = root.table("stash")
    stash = StashConfig(
        peek=stash_table.integer("peek", defaults.stash.peek, minimum=1),
        animate=stash_table.boolean("animate", defaults.stash.animate),
        shift_focus=stash_table.boolean("shift_focus", defaults.stash.shift_focus),
    )
    stash_table.finish()

    exclusions_table = root.table("exclusions")
    excluded_apps = exclusions_table.string_list("apps")
    exclusions_table.finish()

    monitors = _parse_monitors(root.table("monitors"))
    root.finish()

    return Config(
        trigger=trigger,
        radial=radial,
        keybindings=keybindings,
        gaps=gaps,
        actions=actions,
        animation=animation,
        theme=theme,
        preview=preview,
        stash=stash,
        excluded_apps=excluded_apps,
        monitors=monitors,
        custom_actions=custom_actions,
    )


def user_config_path() -> Path:
    """Return the per-user config location, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "looplinux" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load the configuration.

    Without `path`, the user config is read if it exists and the built-in
    defaults are used otherwise. An explicitly given file must exist.
    """
    if path is None:
        path = user_config_path()
        if not path.exists():
            return Config()
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except OSError as error:
        raise ConfigError(f"{path}: cannot read config file: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error
    try:
        return parse_config(data)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error
