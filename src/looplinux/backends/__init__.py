"""Window-management backends and session auto-detection."""

import os
from collections.abc import Mapping

from looplinux.backends.base import (
    Animation,
    BackendError,
    Monitor,
    StashEntry,
    UnsupportedSessionError,
    Window,
    WindowBackend,
)

__all__ = [
    "Animation",
    "BackendError",
    "Monitor",
    "StashEntry",
    "UnsupportedSessionError",
    "Window",
    "WindowBackend",
    "create_backend",
    "detect_backend",
]

# Backends that exist in the interface but have no implementation yet.
_PLANNED = {
    "x11": "generic X11 window managers",
    "hyprland": "Hyprland",
    "sway": "Sway",
}


def detect_backend(env: Mapping[str, str] | None = None) -> str:
    """Pick the backend name for the running session.

    Raises `UnsupportedSessionError` when the session cannot be supported at
    all. A returned name may still belong to a backend that is not implemented
    yet; `create_backend` reports that.
    """
    env = os.environ if env is None else env
    session = env.get("XDG_SESSION_TYPE", "").lower()
    desktops = {name.lower() for name in env.get("XDG_CURRENT_DESKTOP", "").split(":") if name}

    # KWin scripting works the same on X11 and Wayland, so KDE always uses it.
    if "kde" in desktops:
        return "kwin"
    if "hyprland" in desktops or env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if "sway" in desktops or env.get("SWAYSOCK"):
        return "sway"
    if session == "x11" or (not session and env.get("DISPLAY")):
        return "x11"

    desktop = env.get("XDG_CURRENT_DESKTOP") or "unknown"
    if session == "wayland" and "gnome" in desktops:
        raise UnsupportedSessionError(
            "GNOME on Wayland is not supported: Mutter only lets a shell extension "
            "move other applications' windows. Log into a GNOME on Xorg session instead."
        )
    if session == "wayland":
        raise UnsupportedSessionError(
            f"The Wayland compositor of this session (XDG_CURRENT_DESKTOP={desktop}) is not "
            "supported. Supported sessions: KDE Plasma; planned: Hyprland, Sway and X11."
        )
    raise UnsupportedSessionError(
        "Could not detect a graphical session: XDG_SESSION_TYPE is "
        f"{session or 'unset'} and XDG_CURRENT_DESKTOP is {desktop}. "
        "Run looplinux from inside your desktop session."
    )


def create_backend(name: str | None = None) -> WindowBackend:
    """Instantiate the backend called `name`, or the auto-detected one."""
    name = detect_backend() if name is None else name
    if name == "kwin":
        from looplinux.backends.kwin import KWinBackend

        return KWinBackend()
    if name in _PLANNED:
        raise UnsupportedSessionError(
            f"The '{name}' backend ({_PLANNED[name]}) is not implemented yet. "
            "Only KDE Plasma (KWin) is supported in this version."
        )
    raise UnsupportedSessionError(f"Unknown backend '{name}'. Available backends: kwin.")
