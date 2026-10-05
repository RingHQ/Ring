"""Plugins: user code that adds actions to Ring and reacts to what it does.

A plugin is a Python file (or package) in `~/.config/looplinux/plugins/`, or
an installed package that declares an entry point in the `looplinux.plugins`
group. It has a module-level `register(api)` function:

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
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from looplinux.actions import Rect
from looplinux.backends.base import Monitor, Window, WindowBackend

log = logging.getLogger("looplinux")

ENTRY_POINT_GROUP = "looplinux.plugins"
API_VERSION = 1

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

    def on(self, event: str, callback: Callable[..., None]) -> None:
        """Call `callback` with keyword arguments whenever `event` happens."""
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}; events are: {', '.join(EVENTS)}")
        self._registry.listeners.setdefault(event, []).append(callback)


def plugin_directory(config_directory: Path) -> Path:
    return config_directory / "plugins"


def _file_plugins(directory: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if directory.is_dir():
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.suffix == ".py" and entry.is_file():
                found[entry.stem] = entry
            elif (entry / "__init__.py").is_file():
                found[entry.name] = entry / "__init__.py"
    return found


def _describe(path: Path) -> str:
    """Return the first line of a plugin file's docstring, without running it."""
    try:
        docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    except (OSError, SyntaxError, ValueError):
        return ""
    return docstring.strip().splitlines()[0] if docstring else ""


def discover(directory: Path) -> list[PluginInfo]:
    """List the plugins that could be enabled. Nothing is imported."""
    plugins = [
        PluginInfo(name, _describe(path), str(path))
        for name, path in _file_plugins(directory).items()
    ]
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
            f"looplinux_plugin_{name}",
            path,
            submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered first, so that a package plugin can import its own files.
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
