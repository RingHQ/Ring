"""Command-line entry point."""

import argparse
import grp
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from looplinux import __version__
from looplinux.actions import Action, action_name
from looplinux.backends import BackendError, create_backend, detect_backend
from looplinux.config import Config, ConfigError, load_config, user_config_path
from looplinux.executor import ActionError, configure_backend, perform
from looplinux.history import History, default_history_path
from looplinux.ipc import COMMANDS, IpcError, send_command


def _fail(message: str) -> int:
    print(f"looplinux: {message}", file=sys.stderr)
    return 1


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
    backend = create_backend(args.backend)
    configure_backend(backend, config)
    try:
        rect = perform(action, backend, config, History(default_history_path()))
    finally:
        backend.close()
    if args.verbose:
        where = f" -> {rect.width}x{rect.height} at {rect.x},{rect.y}" if rect else ""
        print(f"{action_name(action)}{where}")
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        # Imported late: only the daemon needs Qt.
        from looplinux.app import StartupError, run
    except ImportError as error:
        return _fail(
            f"cannot load the user interface ({error}). Install the distribution's "
            "PySide6 package (Arch: pyside6)."
        )
    try:
        return run(config, args.backend)
    except StartupError as error:
        return _fail(str(error))


def _trigger(args: argparse.Namespace) -> int:
    answer = send_command(args.event)
    return 0 if answer == "ok" else _fail(answer)


def _doctor(args: argparse.Namespace) -> int:
    problems = 0

    def report(ok: bool | None, text: str, *fixes: str) -> None:
        nonlocal problems
        mark = {True: "ok", False: "FAIL", None: "warn"}[ok]
        print(f"[{mark:>4}] {text}")
        for fix in fixes:
            print(f"       {fix}")
        problems += ok is False

    print(f"looplinux {__version__}")
    session = os.environ.get("XDG_SESSION_TYPE") or "unset"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unset"
    report(True, f"Session: XDG_SESSION_TYPE={session}, XDG_CURRENT_DESKTOP={desktop}")

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
        report(None, "Daemon is not running", "Start it: systemctl --user start looplinux")

    try:
        input_gid: int | None = grp.getgrnam("input").gr_gid
    except KeyError:
        input_gid = None
    if input_gid is not None and input_gid in os.getgroups():
        report(True, "User is in the 'input' group (needed for the evdev trigger key)")
    else:
        report(
            None,
            "User is not in the 'input' group, so the evdev trigger key cannot be used.",
            "Fix: sudo usermod -aG input $USER   (then log out and back in)",
            "Not needed on KDE, where the trigger is a global shortcut.",
        )

    print("All good." if problems == 0 else f"{problems} problem(s) found.")
    return 0 if problems == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="looplinux", description="Radial window snapper for Linux."
    )
    parser.add_argument("--version", action="version", version=f"looplinux {__version__}")
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
    try:
        result: int = args.handler(args)
    except (ConfigError, BackendError, ActionError, IpcError) as error:
        return _fail(str(error))
    return result


if __name__ == "__main__":
    sys.exit(main())
