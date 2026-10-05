import pytest

from looplinux.backends import UnsupportedSessionError, create_backend, detect_backend


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}, "kwin"),
        ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}, "kwin"),
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "Hyprland"}, "hyprland"),
        ({"XDG_SESSION_TYPE": "wayland", "HYPRLAND_INSTANCE_SIGNATURE": "abc"}, "hyprland"),
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway"}, "sway"),
        ({"XDG_SESSION_TYPE": "wayland", "SWAYSOCK": "/run/user/1000/sway.sock"}, "sway"),
        ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE"}, "x11"),
        ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "x11"),
        ({"XDG_SESSION_TYPE": "x11"}, "x11"),
        ({"DISPLAY": ":0"}, "x11"),
    ],
)
def test_detect_backend(env: dict[str, str], expected: str) -> None:
    assert detect_backend(env) == expected


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
            "GNOME on Wayland",
        ),
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "COSMIC"},
            "COSMIC.* is not supported",
        ),
        ({"XDG_SESSION_TYPE": "tty"}, "Could not detect a graphical session"),
        ({}, "Could not detect a graphical session"),
    ],
)
def test_unsupported_sessions_explain_themselves(env: dict[str, str], message: str) -> None:
    with pytest.raises(UnsupportedSessionError, match=message):
        detect_backend(env)


def test_planned_backends_say_so() -> None:
    with pytest.raises(UnsupportedSessionError, match="not implemented yet"):
        create_backend("sway")
    with pytest.raises(UnsupportedSessionError, match="Unknown backend 'nope'"):
        create_backend("nope")
