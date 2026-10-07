"""The installer window: a graphical face for `install.sh`.

It is what the AppImage shows when it is opened without having been
installed. The AppImage's launcher (`packaging/appimage/AppRun`) says where
the script and the files it copies are (`RING_SHARE`) and which file is being
run (`APPIMAGE`); without those there is nothing to install from, and the
window is not offered.

All the installing is done by the script, so the terminal and the window
cannot drift apart. The window only runs it and shows what it says.
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ring import __version__

INSTALL = "install"
UNINSTALL = "uninstall"


class InstallerError(RuntimeError):
    """The installer window cannot be shown; the message says why."""


def installed_copy() -> Path:
    """Return where `install.sh` keeps the AppImage."""
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "ring" / "Ring.AppImage"


def installer_command(share: Path, action: str, autostart: bool = True) -> list[str]:
    """Return the command line that installs or removes Ring."""
    command = ["sh", str(share / "install.sh")]
    if action == UNINSTALL:
        command.append("--uninstall")
    elif not autostart:
        command.append("--no-service")
    return command


class InstallerWindow(QWidget):
    """Install, remove or just try Ring."""

    def __init__(self, share: Path, appimage: Path) -> None:
        super().__init__()
        self._share = share
        self._appimage = appimage
        self._action = INSTALL
        self.setWindowTitle("Install Ring")
        self.setMinimumWidth(560)

        logo = QLabel()
        logo.setPixmap(QIcon(str(share / "Linux" / "assets" / "ring-icon.svg")).pixmap(72, 72))
        title = QLabel(f"<span style='font-size:17pt; font-weight:700'>Ring</span> {__version__}")
        about = QLabel(
            "A radial window snapper: hold Right Ctrl, point or press a key, release.<br><br>"
            "Installing puts Ring in your own folders: the <tt>ring</tt> command, "
            "<i>Ring Settings</i> in the application launcher, and a copy of this file. "
            "If the two packages Ring needs from your distribution are missing, "
            "you are asked for your password to install them."
        )
        about.setWordWrap(True)
        # A wrapped label asks for too little room next to the logo otherwise.
        about.setMinimumHeight(110)
        about.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.addWidget(title)
        heading.addWidget(about)
        top = QHBoxLayout()
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        top.addSpacing(8)
        top.addLayout(heading, 1)

        self._autostart = QCheckBox("Start Ring now and every time I log in")
        self._autostart.setChecked(True)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._log.setMinimumHeight(150)
        self._log.hide()

        self._install = QPushButton("Install")
        self._install.setDefault(True)
        self._install.clicked.connect(lambda: self.start(INSTALL))
        self._uninstall = QPushButton("Uninstall")
        self._uninstall.clicked.connect(lambda: self.start(UNINSTALL))
        self._try = QPushButton("Run without installing")
        self._try.clicked.connect(self._run_once)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self._uninstall)
        buttons.addStretch(1)
        buttons.addWidget(self._try)
        buttons.addWidget(close)
        buttons.addWidget(self._install)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addWidget(self._autostart)
        layout.addWidget(self._log, 1)
        layout.addWidget(self._status)
        layout.addLayout(buttons)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._failed_to_start)
        self._refresh()

    def start(self, action: str) -> None:
        """Run the script for `action` and show what it prints."""
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self._action = action
        command = installer_command(self._share, action, self._autostart.isChecked())
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("RING_APPIMAGE", str(self._appimage))
        environment.insert("RING_SOURCE", str(self._share))
        # There is no terminal for sudo to ask for the password in.
        environment.insert("RING_SUDO", "pkexec")
        self.process.setProcessEnvironment(environment)
        self._log.clear()
        self._log.show()
        self.resize(max(self.width(), 620), max(self.height(), 520))
        self._status.setText("Installing…" if action == INSTALL else "Removing…")
        self._set_busy(True)
        self.process.start(command[0], command[1:])

    def _read(self) -> None:
        text = bytes(self.process.readAllStandardOutput().data()).decode(errors="replace")
        self._log.appendPlainText(text.rstrip("\n"))

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._set_busy(False)
        if code != 0:
            self._status.setText("That did not work; the reason is above.")
        elif self._action == INSTALL:
            self._status.setText(
                "Ring is installed. Hold Right Ctrl to open the ring. "
                "The file you downloaded is no longer needed."
            )
        else:
            self._status.setText("Ring is removed. Your settings were kept.")
        self._refresh()

    def _failed_to_start(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._set_busy(False)
            self._log.hide()
            self._status.setText("The installer script could not be started.")

    def _run_once(self) -> None:
        if QProcess.startDetached(str(self._appimage), ["run"]):
            self.close()
        else:
            self._status.setText("Ring could not be started.")

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._install, self._uninstall, self._try, self._autostart):
            widget.setEnabled(not busy)

    def _refresh(self) -> None:
        installed = installed_copy().exists()
        self._install.setText("Install again" if installed else "Install")
        self._uninstall.setVisible(installed)


def run_installer() -> int:
    """Show the installer window and run until it is closed."""
    share, appimage = os.environ.get("RING_SHARE"), os.environ.get("APPIMAGE")
    if not share or not appimage or not (Path(share) / "install.sh").is_file():
        raise InstallerError(
            "the installer window belongs to the AppImage. From a checkout, run install.sh."
        )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Ring")
    app.setDesktopFileName("ring-settings")
    window = InstallerWindow(Path(share), Path(appimage))
    window.show()
    return int(app.exec())
