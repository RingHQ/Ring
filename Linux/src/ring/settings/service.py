"""The service as the settings window sees it: is it running, start it, autostart."""

import contextlib
import os
import subprocess
import sys

from ring.ipc import IpcError, send_command

_SERVICE = "ring.service"
# How long the service gets to come up before the window says how it went.
STARTUP_MS = 2000


def service_state(verb: str) -> str | None:
    """Run `systemctl --user <verb>` on the service; None if it is not installed."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["systemctl", "--user", verb, _SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = result.stdout.strip()
        if verb != "is-enabled":
            return state
        return state if state in ("enabled", "disabled") else None
    return None


def ring_running() -> bool:
    try:
        send_command("ping", timeout=0.5)
    except IpcError:
        return False
    return True


def start_ring() -> bool:
    """Start the service; False if that could not even be tried.

    Through systemd where the unit is installed, so that it is the same
    service that starts at login. Otherwise as a program of its own that
    outlives this window.
    """
    if service_state("is-enabled") is not None:
        service_state("start")
        return True
    # From an AppImage the interpreter's modules vanish with this process.
    packed = os.environ.get("APPIMAGE")
    command = [packed, "run"] if packed else [sys.executable, "-m", "ring", "run"]
    try:
        subprocess.Popen(
            command,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True
