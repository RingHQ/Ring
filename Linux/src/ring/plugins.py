"""Plugins: user code that adds actions to Ring and reacts to what it does.

A plugin is a Python file or a folder in `~/.config/ring/plugins/`, or an
installed package that declares an entry point in the `ring.plugins` group.
A folder holds `plugin.py` and a `README.md` with the plugin's name and
description, which is how <https://github.com/RingHQ/Plugins> is laid out.
The plugin has a module-level `register(api)` function:

    def register(api):
        api.add_action("small", make_small, label="Small and centered")
        api.on("action_applied", lambda action, window, rect: ...)

Plugins run with the user's full permissions, so only those named under
`[plugins] enabled` in the configuration are ever imported.
"""

import ast
import importlib.metadata
import importlib.util
import logging
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ring.actions import Gaps, Rect
from ring.backends.base import Monitor, Window, WindowBackend

log = logging.getLogger("ring")

ENTRY_POINT_GROUP = "ring.plugins"
API_VERSION = 2

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

EVENTS = ("menu_opened", "menu_closed", "action_applied")
"""Events a plugin can listen to, and the keyword arguments they carry:

    menu_opened(window)                   the ring appeared for `window`
    menu_closed(applied)                  it went away; `applied` is a bool
    action_applied(action, window, rect)  `action` (a name) was applied; `rect`
                                          is the window's new frame, or None
"""


@dataclass(frozen=True, slots=True)
class ActionContext:
    """What a plugin action gets to work with."""

    window: Window
    monitor: Monitor
    # The monitor area not covered by panels.
    area: Rect
    backend: WindowBackend
    # The gaps configured for this monitor, for actions that lay windows out.
    gaps: Gaps = field(default_factory=Gaps)


type ActionHandler = Callable[[ActionContext], Rect | None]
"""A plugin action. Return the frame the window should get, and Ring moves it
there (animated, undoable); or do the work through `context.backend` and
return None."""


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """A plugin that was found, whether or not it is enabled."""

    name: str
    description: str
    source: str
    # The name as written for people, if the plugin has a README.md.
    title: str = ""


@dataclass(frozen=True, slots=True)
class PluginActionInfo:
    name: str
    label: str
    handler: ActionHandler


@dataclass(slots=True)
class PluginRegistry:
    """Everything the loaded plugins have registered."""

    actions: dict[str, PluginActionInfo] = field(default_factory=dict)
    listeners: dict[str, list[Callable[..., None]]] = field(default_factory=dict)
    loaded: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # Set by the service once the ring exists: gives it new colors (accent,
    # gradient, ring). None in processes that draw no ring.
    painter: Callable[[str | None, str | None, str | None], None] | None = None

    def emit(self, event: str, **data: Any) -> None:
        """Tell every listener about an event; a failing plugin is only logged."""
        for listener in self.listeners.get(event, []):
            try:
                listener(**data)
            except Exception:
                log.exception("a plugin failed while handling %s", event)

    def clear(self) -> None:
        self.actions.clear()
        self.listeners.clear()
        self.loaded.clear()
        self.errors.clear()


REGISTRY = PluginRegistry()
"""The registry of this process."""


class PluginApi:
    """What a plugin's `register` function is given."""

    version = API_VERSION

    def __init__(self, plugin: str, registry: PluginRegistry) -> None:
        self.plugin = plugin
        self._registry = registry

    def add_action(self, name: str, handler: ActionHandler, *, label: str = "") -> str:
        """Add an action and return its full name, `<plugin>.<name>`.

        That full name is what goes into the configuration, like any built-in
        action: on a key, in the ring, or inside a cycle.
        """
        if not name.replace("_", "").isalnum() or not name.islower():
            raise ValueError(f"action names are lowercase letters, digits and _: {name!r}")
        full = f"{self.plugin}.{name}"
        self._registry.actions[full] = PluginActionInfo(full, label or name, handler)
        return full

    def set_colors(
        self, *, accent: str | None = None, gradient: str | None = None, ring: str | None = None
    ) -> None:
        """Recolor the ring, until Ring restarts. Colors are "#RRGGBB".

        `accent` is the lit segment and, unless the user gave it its own
        color, the preview border; `gradient` is the color the accent fades
        to (the accent itself if left out); `ring` is the ring where it is
        not lit. Call it from an event such as `menu_opened`: while
        `register` runs there is no ring yet, and in `ring snap` there never
        is one, so the call does nothing there.
        """
        for name, color in (("accent", accent), ("gradient", gradient), ("ring", ring)):
            if color is not None and not _COLOR.fullmatch(color):
                raise ValueError(f"{name}: expected a color like #1a2b3c, got {color!r}")
        if self._registry.painter is not None:
            self._registry.painter(accent, gradient, ring)

    def on(self, event: str, callback: Callable[..., None]) -> None:
        """Call `callback` with keyword arguments whenever `event` happens."""
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}; events are: {', '.join(EVENTS)}")
        self._registry.listeners.setdefault(event, []).append(callback)


def plugin_directory(config_directory: Path) -> Path:
    return config_directory / "plugins"


PLUGIN_NAME = re.compile(r"[a-z][a-z0-9_]*")
"""What a plugin may be called: its name is the first half of its actions' names."""

# The file a plugin folder is loaded from, in order of preference.
FOLDER_MODULES = ("plugin.py", "__init__.py")
ABOUT_FILE = "README.md"


def _file_plugins(directory: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if directory.is_dir():
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.suffix == ".py" and entry.is_file():
                found[entry.stem] = entry
                continue
            module = next((entry / m for m in FOLDER_MODULES if (entry / m).is_file()), None)
            if module is not None:
                found[entry.name] = module
    return found


def read_about(text: str) -> tuple[str, str]:
    """Return the name and the description from a plugin's README.md.

    The name is the first heading, the description the first paragraph
    after it.
    """
    title, description = "", []
    for line in (raw.strip() for raw in text.splitlines()):
        if not title:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
        elif line.startswith("#"):
            break
        elif line:
            description.append(line)
        elif description:
            break
    return title, " ".join(description)


def _describe(path: Path) -> tuple[str, str]:
    """Return a plugin's name and description, without running it.

    They come from the README.md of a plugin folder, or else from the first
    line of the file's docstring.
    """
    try:
        title, description = read_about((path.parent / ABOUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        title, description = "", ""
    if path.name not in FOLDER_MODULES or not description:
        try:
            docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        except (OSError, SyntaxError, ValueError):
            docstring = None
        if path.name not in FOLDER_MODULES:
            title = ""
        description = docstring.strip().splitlines()[0] if docstring else ""
    return title, description


def discover(directory: Path) -> list[PluginInfo]:
    """List the plugins that could be enabled. Nothing is imported."""
    plugins = []
    for name, path in _file_plugins(directory).items():
        title, description = _describe(path)
        plugins.append(PluginInfo(name, description, str(path), title))
    names = {plugin.name for plugin in plugins}
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.name not in names:
            package = entry_point.value.split(":")[0]
            plugins.append(PluginInfo(entry_point.name, "", f"installed package {package}"))
    return sorted(plugins, key=lambda plugin: plugin.name)


def _import(name: str, directory: Path) -> Any:
    path = _file_plugins(directory).get(name)
    if path is not None:
        spec = importlib.util.spec_from_file_location(
            f"ring_plugin_{name}",
            path,
            # A folder is a package, so that the plugin can import its own files.
            submodule_search_locations=[str(path.parent)] if path.parent != directory else None,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered first: importing the plugin's own files looks it up.
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[spec.name]
            raise
        return module
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.name == name:
            return entry_point.load()
    raise ImportError("no such plugin is installed")


def load_plugins(
    enabled: Sequence[str], directory: Path, registry: PluginRegistry = REGISTRY
) -> PluginRegistry:
    """Import the enabled plugins and let them register themselves.

    A plugin that cannot be loaded is skipped; why is kept in
    `registry.errors` and logged. Whatever was registered before is dropped.
    """
    registry.clear()
    for name in enabled:
        try:
            module = _import(name, directory)
            register = getattr(module, "register", None)
            if not callable(register):
                raise TypeError("it has no register(api) function")
            register(PluginApi(name, registry))
        except Exception as error:
            registry.errors[name] = f"{type(error).__name__}: {error}"
            log.warning("plugin %s was not loaded: %s", name, registry.errors[name])
            # Drop whatever it managed to register before failing.
            for action in [key for key in registry.actions if key.startswith(f"{name}.")]:
                del registry.actions[action]
        else:
            registry.loaded.append(name)
    return registry
