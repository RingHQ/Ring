"""Unix-socket control channel between `looplinux trigger` and the daemon.

The protocol is one command per connection: the client sends a single line
and the daemon answers with a single line.
"""

import os
import socket
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

COMMANDS = ("toggle", "press", "release", "cancel", "ping", "quit")


class IpcError(RuntimeError):
    """The daemon could not be reached."""


def socket_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "looplinux" / "socket"


def send_command(command: str, timeout: float = 2.0) -> str:
    """Send one command to the running daemon and return its answer."""
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            client.sendall(command.encode() + b"\n")
            return client.makefile("r", encoding="utf-8").readline().strip()
    except OSError as error:
        raise IpcError(
            "looplinux is not running. Start it with `looplinux run` or "
            "`systemctl --user start looplinux`."
        ) from error


class IpcServer:
    """Daemon side of the socket, driven by the Qt event loop."""

    def __init__(self, handler: Callable[[str], str]) -> None:
        """Listen on the socket; `handler` maps a command to its answer."""
        from PySide6.QtNetwork import QLocalServer

        self._handler = handler
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        QLocalServer.removeServer(str(path))
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(str(path)):
            raise IpcError(f"Cannot listen on {path}: {self._server.errorString()}")
        self._server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        while (connection := self._server.nextPendingConnection()) is not None:
            connection.readyRead.connect(lambda c=connection: self._serve(c))
            connection.disconnected.connect(connection.deleteLater)

    def _serve(self, connection: Any) -> None:
        if not connection.canReadLine():
            return
        command = bytes(connection.readLine().data()).decode("utf-8", "replace").strip()
        answer = self._handler(command) if command in COMMANDS else "error: unknown command"
        connection.write(answer.encode() + b"\n")
        connection.flush()
        connection.disconnectFromServer()

    def close(self) -> None:
        self._server.close()
