"""The Plugins page of the settings window."""

import contextlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ring import catalog
from ring.config import (
    Config,
    ConfigError,
    dump_config,
    load_config,
)
from ring.ipc import IpcError, send_command
from ring.plugins import REGISTRY, discover, plugin_directory
from ring.settings.widgets import Card, scroll_page


class PluginsPage(QWidget):
    """Installed and offered plugins, with what can be done to each.

    Unlike the other pages this one acts at once instead of waiting for
    Apply; the window only asks it which plugins are `enabled`.
    """

    def __init__(self, path: Path, config: Config) -> None:
        super().__init__()
        self._path = path
        self._plugin_directory = plugin_directory(path.parent)
        directory = self._plugin_directory
        self.enabled = list(config.enabled_plugins)
        # What the plugin repository offers; None until it has been fetched.
        self._catalog: dict[str, catalog.CatalogPlugin] | None = None
        self._catalog_loaded = False
        self._network = QNetworkAccessManager(self)
        # Plugin name -> the buttons in its row, by their text.
        self._plugin_buttons: dict[str, dict[str, QPushButton]] = {}

        card = Card("Plugins")
        layout = QVBoxLayout(card.body)
        self._plugin_rows = QVBoxLayout()
        self._catalog_status = QLabel()
        self._catalog_status.setObjectName("hint")
        self._catalog_status.setWordWrap(True)
        self._catalog_refresh = QPushButton("Look for plugins again")
        self._catalog_refresh.clicked.connect(self.load_catalog)
        layout.addLayout(self._plugin_rows)
        layout.addWidget(self._catalog_status)
        layout.addWidget(self._catalog_refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self._fill_plugins()

        hint = QLabel(
            f"Plugins add actions to Ring and can react to what it does. The list comes from "
            f"{catalog.REPOSITORY} and from your plugins folder. A plugin runs with your "
            "full permissions, so only enable what you trust. Enabling and disabling take "
            "effect at once; a plugin's actions show up in the Ring and Keys pages after you "
            "reopen this window."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        folder = QPushButton("Open the plugins folder")

        def open_folder() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

        folder.clicked.connect(open_folder)
        about = Card("About plugins")
        about_layout = QVBoxLayout(about.body)
        about_layout.addWidget(hint)
        about_layout.addWidget(folder, 0, Qt.AlignmentFlag.AlignLeft)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll_page("Plugins", "Extend Ring with your own actions.", card, about))

    def _fill_plugins(self) -> None:
        """List every plugin, installed or on offer, with what can be done to it.

        Not installed: Install. Installed: Enable, or Disable once it is
        enabled, and Uninstall.
        """
        while (item := self._plugin_rows.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self._plugin_buttons = {}
        installed = {plugin.name: plugin for plugin in discover(self._plugin_directory)}
        offered = self._catalog or {}
        for name in sorted({*installed, *offered, *self.enabled}):
            local, remote = installed.get(name), offered.get(name)
            title = (local.title if local else "") or (remote.title if remote else "") or name
            detail = (local.description if local else "") or (remote.description if remote else "")
            enabled = name in self.enabled
            actions: list[tuple[str, Callable[[str], None]]] = []
            if local is None:
                state = "Not installed"
                if enabled:
                    state = "Enabled, but not installed"
                    actions.append(("Disable", lambda chosen: self._enable(chosen, False)))
                if remote is not None:
                    actions.append(("Install", self._install))
            else:
                state = "Enabled" if enabled else "Installed"
                if enabled:
                    actions.append(("Disable", lambda chosen: self._enable(chosen, False)))
                else:
                    actions.append(("Enable", lambda chosen: self._enable(chosen, True)))
                # A plugin that came as a Python package is not ours to delete.
                if not local.source.startswith("installed package"):
                    actions.append(("Uninstall", self._uninstall))
                if enabled and name in REGISTRY.errors:
                    detail = f"Could not be loaded: {REGISTRY.errors[name]}"

            heading = QLabel(f"<b>{title}</b> ({name}) \u2013 {state}")
            about = QLabel(detail)
            about.setObjectName("hint")
            about.setWordWrap(True)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(heading)
            text.addWidget(about)
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 8)
            line.addLayout(text, 1)
            self._plugin_buttons[name] = {}
            for label, handler in actions:
                button = QPushButton(label)
                button.clicked.connect(lambda _=False, run=handler, chosen=name: run(chosen))
                self._plugin_buttons[name][label] = button
                line.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            self._plugin_rows.addWidget(row)
        if not self._plugin_buttons:
            empty = QLabel("No plugins installed.")
            empty.setObjectName("hint")
            self._plugin_rows.addWidget(empty)

    def shown(self) -> None:
        """The page came into view: fetch the list, the first time."""
        if not self._catalog_loaded:
            self.load_catalog()

    def load_catalog(self) -> None:
        """Fetch the list of available plugins without holding up the window."""
        if not self._catalog_refresh.isEnabled():
            return
        self._catalog_loaded = True
        self._catalog_refresh.setEnabled(False)
        self._catalog_status.setText(f"Looking at {catalog.REPOSITORY} \u2026")
        self._fetch_catalog()

    def _fetch_catalog(self) -> None:
        """Download the plugin repository; `_show_catalog` gets the outcome."""
        request = QNetworkRequest(QUrl(catalog.ARCHIVE_URL))
        request.setTransferTimeout(20_000)
        reply = self._network.get(request)

        def fetched() -> None:
            reply.deleteLater()
            if reply.error() != QNetworkReply.NetworkError.NoError:
                problem = reply.errorString()
                self._show_catalog(
                    None, f"Cannot download the plugins from {catalog.REPOSITORY}: {problem}"
                )
                return
            try:
                plugins = catalog.from_archive(bytes(reply.readAll().data()))
            except catalog.CatalogError as error:
                self._show_catalog(None, str(error))
            else:
                self._show_catalog(plugins, "")

        reply.finished.connect(fetched)

    def _show_catalog(self, plugins: dict[str, catalog.CatalogPlugin] | None, error: str) -> None:
        """Take in what the plugin repository offers, or why it could not be read."""
        self._catalog_refresh.setEnabled(True)
        if plugins is None:
            self._catalog_status.setText(error)
            return
        self._catalog = plugins
        self._catalog_status.setText(
            "" if plugins else f"There are no plugins at {catalog.REPOSITORY} yet."
        )
        self._fill_plugins()

    def _install(self, name: str) -> None:
        plugin = (self._catalog or {}).get(name)
        if plugin is None:
            return
        try:
            catalog.install(plugin, self._plugin_directory, replace=True)
        except catalog.CatalogError as error:
            self._catalog_status.setText(str(error))
            return
        self._fill_plugins()
        self._catalog_status.setText(f"{plugin.title} is installed. Enable it to use it.")

    def _enable(self, name: str, enabled: bool) -> None:
        """Switch a plugin on or off right away, in the saved settings and the service.

        Only the list of enabled plugins is written; everything else in this
        window still waits for Apply.
        """
        try:
            saved = load_config(self._path) if self._path.exists() else Config()
            names = [plugin for plugin in saved.enabled_plugins if plugin != name]
            if enabled:
                names.append(name)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                dump_config(replace(saved, enabled_plugins=tuple(names))), encoding="utf-8"
            )
        except (ConfigError, OSError) as error:
            self._catalog_status.setText(f"Cannot save the settings: {error}")
            return
        self.enabled = names
        # A service that is not running picks the change up when it starts.
        with contextlib.suppress(IpcError):
            send_command("reload")
        self._fill_plugins()
        self._catalog_status.setText(f"{name} is {'enabled' if enabled else 'disabled'}.")

    def _uninstall(self, name: str) -> None:
        answer = QMessageBox.question(
            self, "Ring", f"Delete the plugin '{name}' from your plugins folder?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if name in self.enabled:
            self._enable(name, False)
            if name in self.enabled:
                return
        try:
            catalog.remove(name, self._plugin_directory)
        except OSError as error:
            self._catalog_status.setText(f"Cannot delete '{name}': {error}")
            return
        self._fill_plugins()
        self._catalog_status.setText(f"{name} is uninstalled.")
