"""Which kind of window the overlay is.

On Wayland it is a layer-shell surface, which needs LayerShellQt for the Qt
that PySide6 is built on. Everywhere else it is an X11 window that the window
manager leaves alone: in an X11 session, and through Xwayland in a Wayland
session whose Qt 6 has no LayerShellQt (Plasma 5 ships it for Qt 5 only).
"""

import os
from collections.abc import MutableMapping
from pathlib import Path

LAYER_SHELL = "layer-shell"
X11 = "x11"

# Set to "layer-shell" or "x11" to skip the choice, for trying things out.
_OVERRIDE = "RING_OVERLAY"


class OverlayError(RuntimeError):
    """The overlay window could not be created."""


def has_layer_shell() -> bool:
    """Tell whether PySide6's Qt can load the LayerShellQt QML module."""
    from PySide6.QtCore import QLibraryInfo

    imports = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
    return (imports / "org" / "kde" / "layershell").is_dir()


def choose_surface(env: MutableMapping[str, str] | None = None) -> str:
    """Pick how the overlay is shown in the running session."""
    env = os.environ if env is None else env
    forced = env.get(_OVERRIDE, "")
    if forced:
        if forced not in (LAYER_SHELL, X11):
            raise OverlayError(f"{_OVERRIDE} is '{forced}'; it can be {LAYER_SHELL} or {X11}.")
        return forced
    session = env.get("XDG_SESSION_TYPE", "").lower()
    wayland = session == "wayland" or (not session and bool(env.get("WAYLAND_DISPLAY")))
    if wayland and has_layer_shell():
        return LAYER_SHELL
    if env.get("DISPLAY"):
        return X11
    if wayland:
        raise OverlayError(
            "The radial menu cannot be drawn: this Wayland session has neither the LayerShellQt "
            "QML module for Qt 6 (Arch: layer-shell-qt) nor Xwayland."
        )
    raise OverlayError(
        "The radial menu cannot be drawn: no display was found. "
        "Run Ring from inside your desktop session."
    )


def prepare_environment(surface: str, env: MutableMapping[str, str] | None = None) -> None:
    """Make Qt start the way `surface` needs. Call before the application exists.

    This holds for the whole process: every window it opens becomes an
    overlay surface. Programs started from it must not inherit it (see
    `tray.open_settings`).
    """
    env = os.environ if env is None else env
    if surface == LAYER_SHELL:
        env["QT_QPA_PLATFORM"] = "wayland"
        env["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    else:
        env["QT_QPA_PLATFORM"] = "xcb"
        env.pop("QT_WAYLAND_SHELL_INTEGRATION", None)
