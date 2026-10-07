"""Command-line entry point."""

import argparse
import contextlib
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from ring import __version__, catalog
from ring.actions import Action, action_name
from ring.backends import BackendError, create_backend, detect_backend
from ring.config import Config, ConfigError, dump_config, load_config, user_config_path
from ring.executor import ActionError, configure_backend, perform
from ring.history import History, default_history_path
from ring.ipc import COMMANDS, IpcError, send_command
from ring.migrate import migrate_legacy_paths
from ring.plugins import REGISTRY, discover, load_plugins, plugin_directory
from ring.stats import Stats, default_stats_path


def _fail(message: str) -> int:
    print(f"ring: {message}", file=sys.stderr)
    return 1


def _load_plugins(config: Config, config_file: Path | None) -> None:
    directory = plugin_directory((config_file or user_config_path()).parent)
    load_plugins(config.enabled_plugins, directory)


def _snap(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    action = config.leaf(args.action)
    if action is None or action is Action.NONE:
        valid = ", ".join(
            [*(a.value for a in Action if a is not Action.NONE), *config.custom_actions]
        )
        return _fail(f"unknown action '{args.action}'. Valid actions: {valid}")

    if args.delay > 0:
        time.sleep(args.delay)
    _load_plugins(config, args.config)
    backend = create_backend(args.backend)
    configure_backend(backend, config)
    try:
        window = backend.get_active_window()
        rect = perform(action, backend, config, History(default_history_path()), window=window)
    finally:
        backend.close()
    Stats(default_stats_path()).record(action_name(action))
    REGISTRY.emit("action_applied", action=action_name(action), window=window, rect=rect)
    if args.verbose:
        where = f" -> {rect.width}x{rect.height} at {rect.x},{rect.y}" if rect else ""
        print(f"{action_name(action)}{where}")
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        # Imported late: only the daemon needs Qt.
        from ring.app import RESTART, StartupError, run
    except ImportError as error:
        return _fail(
            f"cannot load the user interface ({error}). Install the distribution's "
            "PySide6 package (Arch: pyside6)."
        )
    _load_plugins(config, args.config)
    try:
        code = run(config, args.backend)
    except StartupError as error:
        return _fail(str(error))
    if code == RESTART:
        # The settings changed: start over in place, keeping the process id.
        os.execv(sys.executable, [sys.executable, "-m", "ring", *sys.argv[1:]])
    return code


def _settings(args: argparse.Namespace) -> int:
    try:
        from ring.settings import run_settings
    except ImportError as error:
        return _fail(
            f"cannot load the user interface ({error}). Install the distribution's "
            "PySide6 package (Arch: pyside6)."
        )
    return run_settings(args.config)


def _installer(args: argparse.Namespace) -> int:
    try:
        from ring.installer import InstallerError, run_installer
    except ImportError as error:
        return _fail(
            f"cannot load the user interface ({error}). Install the distribution's "
            "PySide6 package (Arch: pyside6)."
        )
    try:
        return run_installer()
    except InstallerError as error:
        return _fail(str(error))


def _stats(args: argparse.Namespace) -> int:
    stats = Stats(default_stats_path())
    print(f"Ring has moved windows {stats.total} times since {stats.since}.")
    for name, count in stats.top(10):
        print(f"{count:>7}  {name}")
    return 0


def _plugin_config(args: argparse.Namespace) -> Config:
    """Load the configuration; a file that is not there yet means the defaults."""
    if args.config is not None and not args.config.exists():
        return Config()
    return load_config(args.config)


def _set_enabled(args: argparse.Namespace, name: str, enabled: bool) -> None:
    """Switch a plugin on or off in the config file and tell the service."""
    path = args.config or user_config_path()
    config = _plugin_config(args)
    names = [plugin for plugin in config.enabled_plugins if plugin != name]
    if enabled:
        names.append(name)
    if tuple(names) == config.enabled_plugins:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(replace(config, enabled_plugins=tuple(names))), encoding="utf-8")
    # A service that is not running picks the change up when it starts.
    with contextlib.suppress(IpcError):
        send_command("reload")


def _plugins(args: argparse.Namespace) -> int:
    directory = plugin_directory((args.config or user_config_path()).parent)
    command, name = args.plugin_command or "list", getattr(args, "name", "")
    if command == "list":
        enabled = _plugin_config(args).enabled_plugins
        found = discover(directory)
        for plugin in found:
            mark = "on " if plugin.name in enabled else "off"
            print(f"[{mark}] {plugin.name:<16} {plugin.description}")
        for missing in sorted(set(enabled) - {plugin.name for plugin in found}):
            print(f"[on ] {missing:<16} (enabled, but not installed)")
        if not found and not enabled:
            print("No plugins installed. `ring plugins available` lists what there is.")
        return 0
    if command == "available":
        installed = {plugin.name for plugin in discover(directory)}
        for entry in catalog.read_catalog(args.source).values():
            mark = " (installed)" if entry.name in installed else ""
            print(f"{entry.name:<16} {entry.description}{mark}")
        return 0
    if command in ("install", "update"):
        chosen = catalog.read_catalog(args.source).get(name)
        if chosen is None:
            return _fail(f"there is no plugin called '{name}' in the catalog.")
        target = catalog.install(chosen, directory, replace=command == "update")
        print(f"Installed {chosen.title} in {target}.")
        if name in _plugin_config(args).enabled_plugins:
            with contextlib.suppress(IpcError):
                send_command("reload")
        else:
            print(f"It runs with your full permissions; `ring plugins enable {name}` turns it on.")
        return 0
    if command in ("enable", "disable"):
        if command == "enable" and name not in {plugin.name for plugin in discover(directory)}:
            return _fail(f"no plugin called '{name}' is installed.")
        _set_enabled(args, name, command == "enable")
        return 0
    # remove
    _set_enabled(args, name, False)
    if not catalog.remove(name, directory):
        return _fail(f"no plugin called '{name}' is installed in {directory}.")
    return 0


def _trigger(args: argparse.Namespace) -> int:
    answer = send_command(args.event)
    return 0 if answer == "ok" else _fail(answer)


type _Report = Callable[..., None]


def _kwin_setting(group: str, key: str) -> str | None:
    """Return a value the user set in kwinrc, or None if it is at its default."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    try:
        lines = (Path(base) / "kwinrc").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    inside = False
    for line in lines:
        if line.startswith("["):
            inside = line.strip() == f"[{group}]"
        elif inside and line.partition("=")[0].strip() == key:
            return line.partition("=")[2].strip()
    return None


def _check_overlay(report: _Report) -> None:
    """Check what the ring is drawn with: the system's PySide6, and as what."""
    try:
        from PySide6.QtCore import qVersion

        from ring.overlay.surface import LAYER_SHELL, OverlayError, choose_surface
    except ImportError as error:
        report(
            False,
            f"PySide6 cannot be imported ({error})",
            "Install the distribution's package (Arch: pyside6) and create the venv",
            "with --system-site-packages.",
        )
        return
    report(True, f"PySide6 with Qt {qVersion()}")
    try:
        surface = choose_surface()
    except OverlayError as error:
        report(False, str(error))
        return
    if surface == LAYER_SHELL:
        report(True, "The ring is drawn as a layer-shell surface (LayerShellQt found)")
    elif os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        report(
            None,
            "The ring is drawn as an X11 window through Xwayland: no LayerShellQt for Qt 6",
            "Keys pressed while the ring is open also reach the application underneath,",
            "and which of them Ring sees is up to KWin's Legacy X11 App Support setting.",
            "On Plasma 6, install LayerShellQt (Arch: layer-shell-qt). If it is installed,",
            "this PySide6 is not the distribution's: the PyPI build cannot load it.",
        )
    else:
        report(True, "The ring is drawn as an X11 window")


def _check_trigger(report: _Report, config: Config) -> None:
    """Check that the configured trigger can be observed."""
    trigger = config.trigger
    if trigger.use != "key":
        report(True, f"Trigger: the global shortcut {trigger.shortcut}")
        return
    from ring.input.xkey import KeyListener, KeyListenerError

    try:
        KeyListener(trigger.key, lambda: None, lambda: None).close()
    except KeyListenerError as error:
        report(
            False,
            f"Trigger key {trigger.key}: {error}",
            f"Ring falls back to the shortcut {trigger.shortcut}. A single key needs X11",
            "or Xwayland.",
        )
        return
    wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    if wayland and _kwin_setting("Xwayland", "XwaylandEavesdrops") == "None":
        report(
            False,
            f"Trigger key {trigger.key}: KWin is set to never show key presses to X11 apps",
            "Allow at least modifier keys in System Settings > Application Permissions >",
            'Legacy X11 App Support, or use trigger.use = "shortcut".',
        )
        return
    through = "Xwayland" if wayland else "the X server"
    report(True, f"Trigger key {trigger.key} can be watched through {through}")


def _doctor(args: argparse.Namespace) -> int:
    problems = 0

    def report(ok: bool | None, text: str, *fixes: str) -> None:
        nonlocal problems
        mark = {True: "ok", False: "FAIL", None: "warn"}[ok]
        print(f"[{mark:>4}] {text}")
        for fix in fixes:
            print(f"       {fix}")
        problems += ok is False

    print(f"ring {__version__}")
    session = os.environ.get("XDG_SESSION_TYPE") or "unset"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unset"
    known = session.lower() in ("wayland", "x11")
    report(
        known,
        f"Session: XDG_SESSION_TYPE={session}, XDG_CURRENT_DESKTOP={desktop}",
        *(() if known else ("Ring needs a Wayland or X11 session of KDE Plasma.",)),
    )

    path = args.config or user_config_path()
    try:
        config: Config | None = load_config(args.config)
        source = str(path) if path.exists() else "built-in defaults"
        report(True, f"Configuration is valid ({source})")
    except ConfigError as error:
        config = None
        report(False, f"Configuration: {error}")

    try:
        name = args.backend or detect_backend()
        backend = create_backend(name)
    except BackendError as error:
        report(False, f"Backend: {error}")
    else:
        try:
            monitors = backend.get_monitors()
            areas = [backend.get_work_area(monitor) for monitor in monitors]
            window = backend.get_active_window()
        except BackendError as error:
            report(False, f"Backend '{name}': {error}")
        else:
            report(True, f"Backend '{name}' is responding")
            for monitor, area in zip(monitors, areas, strict=True):
                size = f"{monitor.geometry.width}x{monitor.geometry.height}"
                usable = f"{area.width}x{area.height} at {area.x},{area.y}"
                report(True, f"Monitor {monitor.name}: {size}, work area {usable}")
            if window is None:
                report(None, "No active window right now")
            else:
                excluded = config is not None and config.is_excluded(window.app_id)
                note = " (excluded by config)" if excluded else ""
                report(True, f"Active window: {window.app_id}{note}")
        finally:
            backend.close()

    try:
        send_command("ping", timeout=0.5)
        held = "?"
        if config is not None:
            use_key = config.trigger.use == "key"
            held = config.trigger.key if use_key else config.trigger.shortcut
        report(True, f"Daemon is running (hold {held})")
    except IpcError:
        report(None, "Daemon is not running", "Start it: systemctl --user start ring")

    _check_overlay(report)
    if config is not None:
        _check_trigger(report, config)

    print("All good." if problems == 0 else f"{problems} problem(s) found.")
    return 0 if problems == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ring", description="Radial window snapper for Linux.")
    parser.add_argument("--version", action="version", version=f"ring {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, metavar="FILE", help="use this config file")
    common.add_argument("--backend", metavar="NAME", help="skip auto-detection (e.g. kwin)")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    snap = commands.add_parser(
        "snap", parents=[common], help="apply an action to the active window"
    )
    snap.add_argument("action", help="action name, e.g. left_half (see config/default.toml)")
    snap.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait before acting, to switch to another window when testing from a terminal",
    )
    snap.add_argument("-v", "--verbose", action="store_true", help="print the resulting geometry")
    snap.set_defaults(handler=_snap)

    run = commands.add_parser("run", parents=[common], help="run the radial menu daemon")
    run.set_defaults(handler=_run)

    settings = commands.add_parser("settings", help="open the settings window")
    settings.add_argument("--config", type=Path, metavar="FILE", help="edit this config file")
    settings.set_defaults(handler=_settings)

    installer = commands.add_parser(
        "installer", help="the window that installs or removes Ring (from the AppImage)"
    )
    installer.set_defaults(handler=_installer)

    stats = commands.add_parser("stats", help="show how often Ring has been used")
    stats.set_defaults(handler=_stats)

    plugins = commands.add_parser(
        "plugins", help="list, install and enable plugins (see docs/PLUGINS.md)"
    )
    plugins.add_argument("--config", type=Path, metavar="FILE", help="use this config file")
    plugins.set_defaults(handler=_plugins)
    plugin_commands = plugins.add_subparsers(dest="plugin_command", metavar="COMMAND")
    plugin_commands.add_parser("list", help="the installed plugins (the default)")
    source = argparse.ArgumentParser(add_help=False)
    source.add_argument(
        "--from",
        dest="source",
        metavar="FOLDER",
        help=f"a local copy of the catalog instead of {catalog.REPOSITORY}",
    )
    plugin_commands.add_parser("available", parents=[source], help="the plugins in the catalog")
    for verb, text in (
        ("install", "copy a plugin from the catalog into your plugins folder"),
        ("update", "install it again, replacing what is there"),
    ):
        plugin_commands.add_parser(verb, parents=[source], help=text).add_argument("name")
    for verb, text in (
        ("enable", "load a plugin (rewrites the config file, dropping comments)"),
        ("disable", "stop loading a plugin"),
        ("remove", "disable a plugin and delete it"),
    ):
        plugin_commands.add_parser(verb, help=text).add_argument("name")

    trigger = commands.add_parser(
        "trigger", help="control the running daemon, e.g. from a compositor key binding"
    )
    trigger.add_argument(
        "event",
        nargs="?",
        default="toggle",
        choices=[command for command in COMMANDS if command != "ping"],
        help="toggle (default) opens the menu, and applies the selection when it is open",
    )
    trigger.set_defaults(handler=_trigger)

    doctor = commands.add_parser(
        "doctor", parents=[common], help="check that the session is set up correctly"
    )
    doctor.set_defaults(handler=_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    for old, new in migrate_legacy_paths():
        print(f"ring: moved {old} to {new}", file=sys.stderr)
    try:
        result: int = args.handler(args)
    except (ConfigError, BackendError, ActionError, IpcError, catalog.CatalogError) as error:
        return _fail(str(error))
    return result


if __name__ == "__main__":
    sys.exit(main())
