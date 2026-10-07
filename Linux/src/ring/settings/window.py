"""The settings window and the pages that edit the configuration."""

import sys
import tomllib
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ring import __version__
from ring.actions import (
    Action,
    ActionSpec,
    Direction,
    Gaps,
)
from ring.config import (
    ANIMATION_STYLES,
    MAX_TRIGGER_DELAY_MS,
    PREVIEW_STARTS,
    PREVIEW_STYLES,
    ActionsConfig,
    AnimationConfig,
    Chord,
    Config,
    ConfigError,
    PreviewConfig,
    RadialConfig,
    StashConfig,
    ThemeConfig,
    TrayConfig,
    TriggerConfig,
    dump_config,
    load_config,
    parse_config,
    user_config_path,
)
from ring.input.keys import EVDEV_CODES
from ring.ipc import IpcError, send_command
from ring.plugins import load_plugins, plugin_directory
from ring.settings import service
from ring.settings.about_page import AboutPage
from ring.settings.pictures import LookPreview, SectorPicker
from ring.settings.plugins_page import PluginsPage
from ring.settings.widgets import (
    BLUR_NOTE,
    SECTOR_LABELS,
    ActionEdit,
    Card,
    ColorButton,
    KeyButton,
    beta,
    combo,
    percent,
    pretty,
    scroll_page,
    spin,
    style_sheet,
)
from ring.stats import Stats, default_stats_path


class SettingsWindow(QWidget):
    """The settings window."""

    def __init__(self, path: Path, config: Config, stats: Stats) -> None:
        super().__init__()
        self._path = path
        self._config = config
        self._stats = stats
        self._key_rows: list[tuple[KeyButton, ActionEdit, QWidget]] = []
        self.setWindowTitle("Ring Settings")
        self.resize(1000, 720)

        self._pages = QStackedWidget()
        self._sidebar = QListWidget()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(190)
        self._sidebar.setIconSize(QSize(20, 20))
        self._sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for title, icon, build in (
            ("General", "preferences-system", self._general_tab),
            ("Appearance", "preferences-desktop-color", self._look_tab),
            ("Ring", "draw-circle", self._ring_tab),
            ("Keys", "input-keyboard", self._keys_tab),
            ("Behavior", "preferences-system-windows", self._behavior_tab),
            ("Plugins", "preferences-plugin", self._plugins_tab),
            ("About", "help-about", self._about_tab),
        ):
            self._sidebar.addItem(QListWidgetItem(QIcon.fromTheme(icon), title))
            self._pages.addWidget(build())
        self._sidebar.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._pages.currentChanged.connect(self._page_shown)
        self._sidebar.setCurrentRow(0)

        brand = QLabel("Ring")
        brand.setObjectName("brand")
        version = QLabel(f"Version {__version__}")
        version.setObjectName("hint")
        side = QVBoxLayout()
        side.setContentsMargins(0, 16, 0, 12)
        side.setSpacing(2)
        side.addWidget(brand)
        side.addWidget(version)
        side.addSpacing(12)
        side.addWidget(self._sidebar, 1)
        for label in (brand, version):
            label.setContentsMargins(20, 0, 0, 0)

        self._status = QLabel()
        self._status.setObjectName("hint")
        apply = QPushButton("Apply")
        apply.setObjectName("primary")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        defaults = QPushButton("Reset to defaults\u2026")
        defaults.clicked.connect(self._reset)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        # Only there while the service is not running. Opening this window
        # starts it (see `run_settings`), so that means it failed to start.
        self._start_button = QPushButton("Start Ring")
        self._start_button.clicked.connect(lambda: self.start_service())
        buttons = QHBoxLayout()
        buttons.setContentsMargins(20, 10, 20, 14)
        buttons.addWidget(defaults)
        buttons.addWidget(self._status, 1)
        buttons.addWidget(self._start_button)
        buttons.addWidget(close)
        buttons.addWidget(apply)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._pages, 1)
        main.addLayout(buttons)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(side)
        layout.addLayout(main, 1)
        self.setStyleSheet(style_sheet(self.palette()))
        self._show_running("Ring is not running.")

    def _show_running(self, stopped: str, started: str = "") -> bool:
        """Offer to start the service if it is not running; say which it is."""
        running = service.ring_running()
        self._start_button.setVisible(not running)
        self._status.setText(started if running else stopped)
        return running

    def start_service(self, saved: str = "") -> None:
        """Start the service and report, once it had time to come up, how that went."""
        if not service.start_ring():
            self._status.setText(f"{saved}Ring could not be started.".strip())
            return
        self._start_button.setEnabled(False)
        self._status.setText(f"{saved}Starting Ring\u2026")

        def report() -> None:
            self._start_button.setEnabled(True)
            self._show_running(
                f"{saved}Ring did not start; `ring doctor` in a terminal says why.",
                f"{saved}Ring is running.",
            )

        QTimer.singleShot(service.STARTUP_MS, report)

    # Tabs

    def _general_tab(self) -> QWidget:
        trigger = self._config.trigger
        self._use = combo({"key": "A single key", "shortcut": "A keyboard shortcut"}, trigger.use)
        self._key = combo({name: pretty(name) for name in sorted(EVDEV_CODES)}, trigger.key)
        self._shortcut = QKeySequenceEdit(QKeySequence(trigger.shortcut))
        self._shortcut.setMaximumSequenceLength(1)
        self._use.currentIndexChanged.connect(self._sync_trigger)
        self._sync_trigger()
        self._delay = spin(trigger.delay_ms, 0, MAX_TRIGGER_DELAY_MS, " ms")
        self._delay.setSingleStep(50)
        self._double_tap = QCheckBox("Press twice quickly, and hold the second press")
        self._double_tap.setChecked(trigger.double_tap)
        hint = QLabel(
            "A single key keeps working in applications. Give it a delay, or pick one you "
            "do not use for shortcuts. A keyboard shortcut is taken over completely while "
            "Ring runs."
        )
        hint.setWordWrap(True)

        box = Card("Trigger")
        form = QFormLayout(box.body)
        form.addRow("Hold to open the ring:", self._use)
        form.addRow("Key:", self._key)
        form.addRow("Shortcut:", self._shortcut)
        form.addRow("Hold for:", self._delay)
        form.addRow(self._double_tap)
        form.addRow(hint)

        self._style = combo(
            {s: s.capitalize() for s in ANIMATION_STYLES}, self._config.animation.style
        )
        self._animate_windows = QCheckBox("Glide windows to their new place")
        self._animate_windows.setChecked(self._config.animation.windows)
        motion = Card("Animation")
        motion_form = QFormLayout(motion.body)
        motion_form.addRow("Style:", self._style)
        motion_form.addRow(self._animate_windows)

        self._tray = QCheckBox("Show an icon in the system tray")
        self._tray.setChecked(self._config.tray.visible)
        self._autostart = QCheckBox("Start Ring when I log in")
        self._autostart_known = service.service_state("is-enabled") is not None
        self._autostart.setChecked(service.service_state("is-enabled") == "enabled")
        self._autostart.setEnabled(self._autostart_known)
        if not self._autostart_known:
            self._autostart.setToolTip("The Ring systemd user service is not installed.")
        system = Card("System")
        system_layout = QVBoxLayout(system.body)
        system_layout.addWidget(self._tray)
        system_layout.addWidget(self._autostart)

        return scroll_page(
            "General", "How Ring is triggered and how it moves.", box, motion, system
        )

    def _look_tab(self) -> QWidget:
        theme, look, radial = self._config.theme, self._config.preview, self._config.radial
        self._system_accent = QCheckBox("Follow the desktop accent color")
        self._system_accent.setChecked(theme.accent_color == "system")
        fallback = self.palette().highlight().color().name()
        self._accent = ColorButton(
            fallback if theme.accent_color == "system" else theme.accent_color
        )
        self._use_gradient = QCheckBox("Gradient to a second color")
        self._use_gradient.setChecked(bool(theme.gradient_color))
        self._gradient = ColorButton(theme.gradient_color or "#b45cff")

        colors = Card("Accent")
        grid = QGridLayout(colors.body)
        grid.addWidget(self._system_accent, 0, 0, 1, 2)
        grid.addWidget(QLabel("Accent color:"), 1, 0)
        grid.addWidget(self._accent, 1, 1)
        grid.addWidget(self._use_gradient, 2, 0)
        grid.addWidget(self._gradient, 2, 1)
        grid.setColumnStretch(2, 1)

        self._ring_visible = QCheckBox("Show the ring")
        self._ring_visible.setChecked(radial.visible)
        self._radius = spin(radial.radius, 20, 200)
        self._thickness = spin(radial.thickness, 4, 120)
        self._ring_color = ColorButton(theme.ring_color)
        self._ring_opacity = percent(theme.ring_opacity)
        self._ring_blur = QCheckBox("Blur what is behind it")
        self._ring_blur.setChecked(theme.blur)
        ring = Card("Ring")
        ring_form = QFormLayout(ring.body)
        ring_form.addRow(self._ring_visible)
        ring_form.addRow("Radius:", self._radius)
        ring_form.addRow("Thickness:", self._thickness)
        ring_form.addRow("Color:", self._ring_color)
        ring_form.addRow("Opacity:", self._ring_opacity)
        ring_form.addRow(beta(self._ring_blur, BLUR_NOTE))

        self._preview_visible = QCheckBox("Show the preview")
        self._preview_visible.setChecked(look.visible)
        self._preview_style = combo(
            {"outline": "Outline", "liquid_glass": "Liquid Glass"}, look.style
        )
        styles = {self._preview_style.itemData(i) for i in range(self._preview_style.count())}
        assert set(PREVIEW_STYLES) == styles
        self._own_border = QCheckBox("Border in its own color")
        self._own_border.setChecked(look.border_color != "accent")
        self._border_color = ColorButton(
            "#ffffff" if look.border_color == "accent" else look.border_color
        )
        self._border = spin(look.border_thickness, 0, 40)
        self._corner = spin(look.corner_radius, 0, 80)
        self._padding = spin(look.padding, 0, 100)
        self._fill_color = ColorButton(look.fill_color)
        self._fill_opacity = percent(look.fill_opacity)
        self._preview_blur = QCheckBox("Blur what is behind it")
        self._preview_blur.setChecked(look.blur)
        self._start = combo(
            {
                "action_center": "Its own center",
                "radial_menu": "The ring",
                "screen_center": "The center of the screen",
            },
            look.start,
        )
        assert set(PREVIEW_STARTS) == {self._start.itemData(i) for i in range(self._start.count())}
        border_row = QHBoxLayout()
        border_row.addWidget(self._own_border)
        border_row.addWidget(self._border_color)
        border_row.addStretch(1)
        preview = Card("Preview")
        preview_form = QFormLayout(preview.body)
        preview_form.addRow(self._preview_visible)
        preview_form.addRow("Style:", beta(self._preview_style, BLUR_NOTE))
        preview_form.addRow(border_row)
        preview_form.addRow("Border thickness:", self._border)
        preview_form.addRow("Corner radius:", self._corner)
        preview_form.addRow("Padding:", self._padding)
        preview_form.addRow("Fill color:", self._fill_color)
        preview_form.addRow("Fill opacity:", self._fill_opacity)
        preview_form.addRow("Grows out of:", self._start)
        preview_form.addRow(beta(self._preview_blur, BLUR_NOTE))

        self._look_preview = LookPreview(self._look_config)
        for widget in (self._system_accent, self._use_gradient, self._ring_visible,
                       self._preview_visible, self._own_border):  # fmt: skip
            widget.toggled.connect(self._look_changed)
        for button in (self._accent, self._gradient, self._ring_color,
                       self._border_color, self._fill_color):  # fmt: skip
            button.changed.connect(self._look_changed)
        for box in (self._radius, self._thickness, self._ring_opacity, self._border,
                    self._corner, self._padding, self._fill_opacity):  # fmt: skip
            box.valueChanged.connect(self._look_changed)
        self._preview_style.currentIndexChanged.connect(self._look_changed)
        self._look_changed()

        look = Card("Live preview")
        look_layout = QVBoxLayout(look.body)
        look_layout.addWidget(self._look_preview)
        left = QVBoxLayout()
        left.addWidget(look)
        left.addWidget(colors)
        left.addWidget(ring)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(preview)
        right.addStretch(1)
        columns = QWidget()
        row = QHBoxLayout(columns)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        return scroll_page("Appearance", "Colors and shapes of the ring and the preview.", columns)

    def _ring_tab(self) -> QWidget:
        self._sectors: dict[Direction, ActionEdit] = {}
        self._sector_editors = QStackedWidget()
        for direction in SECTOR_LABELS:
            spec = self._config.radial.sectors.get(direction, Action.NONE)
            edit = ActionEdit(self._config, spec, vertical=True)
            self._sectors[direction] = edit
            self._sector_editors.addWidget(edit)
        self._picker = SectorPicker(lambda direction: self._sectors[direction].first())
        for edit in self._sectors.values():
            edit.changed.connect(self._picker.update)
        self._sector_name = QLabel()
        self._sector_name.setObjectName("cardTitle")
        self._picker.picked.connect(self._show_sector)
        self._show_sector(self._picker.selected)

        hint = QLabel(
            "Click a part of the ring, then choose what it does. Add steps with + to make "
            "a cycle: a left click, or pointing there again once the window is snapped, "
            "moves on to the next step."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        editor = QVBoxLayout()
        editor.addWidget(self._sector_name)
        editor.addWidget(self._sector_editors)
        editor.addSpacing(8)
        editor.addWidget(hint)
        editor.addStretch(1)
        card = Card("Directions")
        row = QHBoxLayout(card.body)
        row.setSpacing(24)
        row.addWidget(self._picker)
        row.addLayout(editor, 1)
        return scroll_page("Ring", "What each direction of the ring does.", card)

    def _show_sector(self, direction: Direction) -> None:
        self._sector_name.setText(SECTOR_LABELS[direction])
        self._sector_editors.setCurrentWidget(self._sectors[direction])

    def _keys_tab(self) -> QWidget:
        self._keys_layout = QVBoxLayout()
        self._keys_layout.setSpacing(6)
        bindings = sorted(
            self._config.keybindings.items(), key=lambda item: (len(item[0]), sorted(item[0]))
        )
        for chord, spec in bindings:
            if spec is not Action.NONE:
                self._add_key_row(chord, spec)
        add = QPushButton("Add a key")
        add.clicked.connect(lambda: self._add_key_row(frozenset(), Action.MAXIMIZE))
        card = Card("While the trigger is held")
        layout = QVBoxLayout(card.body)
        layout.addLayout(self._keys_layout)
        layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        return scroll_page(
            "Keys",
            "Click a key to change it; press two keys together for a combination.",
            card,
        )

    def _plugins_tab(self) -> QWidget:
        self._plugins = PluginsPage(self._path, self._config)
        return self._plugins

    def _about_tab(self) -> QWidget:
        self._about = AboutPage(self._path, self._stats)
        return self._about

    def _page_shown(self, index: int) -> None:
        if self._pages.widget(index) is self._plugins:
            self._plugins.shown()

    def _behavior_tab(self) -> QWidget:
        config = self._config
        self._outer = spin(config.gaps.outer, 0, 200)
        self._inner = spin(config.gaps.inner, 0, 200)
        gaps = Card("Gaps")
        gaps_form = QFormLayout(gaps.body)
        gaps_form.addRow("Around the screen edge:", self._outer)
        gaps_form.addRow("Between windows:", self._inner)

        self._margin = percent(config.actions.almost_maximize_margin, 49)
        self._increment = spin(config.actions.size_increment, 1, 500)
        self._restart = QCheckBox("Always start a cycle at its first action")
        self._restart.setChecked(config.actions.cycle_restart)
        self._backwards = QCheckBox("Shift steps backwards through a cycle")
        self._backwards.setChecked(config.actions.cycle_backwards_on_shift)
        actions = Card("Actions")
        actions_form = QFormLayout(actions.body)
        actions_form.addRow("Almost maximize leaves free, per side:", self._margin)
        actions_form.addRow("Step for grow, shrink and move:", self._increment)
        actions_form.addRow(self._restart)
        actions_form.addRow(self._backwards)

        self._peek = spin(config.stash.peek, 1, 200)
        self._stash_animate = QCheckBox("Slide in and out")
        self._stash_animate.setChecked(config.stash.animate)
        self._stash_focus = QCheckBox("Focus a stashed window when it slides out")
        self._stash_focus.setChecked(config.stash.shift_focus)
        stash = Card("Stashed windows")
        stash_form = QFormLayout(stash.body)
        stash_form.addRow("Strip left visible:", self._peek)
        stash_form.addRow(self._stash_animate)
        stash_form.addRow(self._stash_focus)

        self._excluded = QLineEdit(", ".join(config.excluded_apps))
        self._excluded.setPlaceholderText("for example: steam, org.kde.plasmashell")
        excluded = Card("Applications Ring never touches")
        excluded_layout = QVBoxLayout(excluded.body)
        excluded_layout.addWidget(self._excluded)
        excluded_layout.addWidget(QLabel("Window classes, separated by commas."))

        return scroll_page(
            "Behavior", "Gaps, steps, stashing and exceptions.", gaps, actions, stash, excluded
        )

    # Behavior

    def _sync_trigger(self) -> None:
        single = self._use.currentData() == "key"
        self._key.setEnabled(single)
        self._shortcut.setEnabled(not single)

    def _look_changed(self) -> None:
        self._accent.setEnabled(not self._system_accent.isChecked())
        self._gradient.setEnabled(self._use_gradient.isChecked())
        self._border_color.setEnabled(self._own_border.isChecked())
        # Glass has a rim instead of a border and is always blurred.
        glass = self._preview_style.currentData() == "liquid_glass"
        self._border.setEnabled(not glass)
        self._preview_blur.setEnabled(not glass)
        self._look_preview.update()

    def _add_key_row(self, chord: Chord, spec: ActionSpec) -> None:
        key = KeyButton(chord)
        action = ActionEdit(self._config, spec)
        remove = QToolButton(text="✕", toolTip="Remove this key")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(key)
        layout.addWidget(action, 1)
        layout.addWidget(remove)
        entry = (key, action, row)
        self._key_rows.append(entry)

        def drop() -> None:
            self._key_rows.remove(entry)
            row.deleteLater()

        remove.clicked.connect(drop)
        self._keys_layout.addWidget(row)
        if not chord:
            key.setChecked(True)

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self, "Ring", "Put every setting back to its default? Nothing is saved until you apply."
        )
        if answer == QMessageBox.StandardButton.Yes:
            fresh = SettingsWindow(self._path, Config(), self._stats)
            fresh.setGeometry(self.geometry())
            fresh._status.setText("Defaults loaded; press Apply to keep them.")
            fresh.show()
            # Keep the new window alive after this one closes.
            QApplication.instance().setProperty("ring_window", fresh)
            self.close()

    def _look_config(self) -> Config:
        """Return the configuration with the Appearance tab's current values."""
        theme = ThemeConfig(
            accent_color="system" if self._system_accent.isChecked() else self._accent.color(),
            gradient_color=self._gradient.color() if self._use_gradient.isChecked() else "",
            ring_color=self._ring_color.color(),
            ring_opacity=round(self._ring_opacity.value() / 100, 2),
            blur=self._ring_blur.isChecked(),
        )
        preview = PreviewConfig(
            visible=self._preview_visible.isChecked(),
            style=self._preview_style.currentData(),
            padding=self._padding.value(),
            corner_radius=self._corner.value(),
            border_thickness=self._border.value(),
            border_color=self._border_color.color() if self._own_border.isChecked() else "accent",
            fill_color=self._fill_color.color(),
            fill_opacity=round(self._fill_opacity.value() / 100, 2),
            blur=self._preview_blur.isChecked(),
            start=self._start.currentData(),
        )
        radial = replace(
            self._config.radial,
            visible=self._ring_visible.isChecked(),
            radius=self._radius.value(),
            thickness=self._thickness.value(),
        )
        return replace(self._config, theme=theme, preview=preview, radial=radial)

    def current(self) -> Config:
        """Build the configuration shown in the window."""
        config = self._look_config()
        sectors = {direction: edit.spec() for direction, edit in self._sectors.items()}
        # A default binding that was removed has to be switched off explicitly.
        keybindings: dict[Chord, ActionSpec] = dict.fromkeys(Config().keybindings, Action.NONE)
        for key, action, _ in self._key_rows:
            if key.chord():
                keybindings[key.chord()] = action.spec()
        shortcut = self._shortcut.keySequence().toString() or self._config.trigger.shortcut
        apps = tuple(app.strip() for app in self._excluded.text().split(",") if app.strip())
        return replace(
            config,
            trigger=TriggerConfig(
                use=self._use.currentData(),
                key=self._key.currentData(),
                shortcut=shortcut,
                cancel_key=self._config.trigger.cancel_key,
                delay_ms=self._delay.value(),
                double_tap=self._double_tap.isChecked(),
            ),
            radial=RadialConfig(
                visible=config.radial.visible,
                radius=config.radial.radius,
                thickness=config.radial.thickness,
                sectors=sectors,
            ),
            keybindings=keybindings,
            gaps=Gaps(outer=self._outer.value(), inner=self._inner.value()),
            actions=ActionsConfig(
                almost_maximize_margin=round(self._margin.value() / 100, 2),
                size_increment=self._increment.value(),
                cycle_restart=self._restart.isChecked(),
                cycle_backwards_on_shift=self._backwards.isChecked(),
            ),
            animation=AnimationConfig(
                style=self._style.currentData(), windows=self._animate_windows.isChecked()
            ),
            stash=StashConfig(
                peek=self._peek.value(),
                animate=self._stash_animate.isChecked(),
                shift_focus=self._stash_focus.isChecked(),
            ),
            tray=TrayConfig(visible=self._tray.isChecked()),
            excluded_apps=apps,
            enabled_plugins=tuple(self._plugins.enabled),
        )

    def apply(self) -> None:
        """Save the settings and make the running service pick them up."""
        try:
            text = dump_config(self.current())
            # Whatever is written must load again.
            parse_config(tomllib.loads(text))
        except (ConfigError, ValueError) as error:
            QMessageBox.warning(self, "Ring", f"These settings cannot be saved:\n\n{error}")
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Ring", f"Cannot write {self._path}:\n\n{error.strerror}")
            return
        if self._autostart_known:
            service.service_state("enable" if self._autostart.isChecked() else "disable")
        try:
            answer = send_command("reload")
        except IpcError:
            # Somebody who changes settings wants them in use.
            self.start_service("Saved. ")
            return
        self._start_button.setVisible(False)
        if answer == "ok":
            self._status.setText("Saved. Ring restarted with the new settings.")
        else:
            self._status.setText("Saved. Restart Ring to use the new settings.")


def run_settings(path: Path | None = None) -> int:
    """Show the settings window and run until it is closed."""
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Ring Settings")
    app.setDesktopFileName("ring-settings")
    target = path or user_config_path()
    try:
        config = load_config(target) if target.exists() else Config()
    except ConfigError as error:
        QMessageBox.warning(
            None,
            "Ring",
            f"The settings file has a problem, so the defaults are shown instead:\n\n{error}",
        )
        config = Config()
    # Loaded so that their actions can be offered; same trust as the service.
    load_plugins(config.enabled_plugins, plugin_directory(target.parent))
    window = SettingsWindow(target, config, Stats(default_stats_path()))
    # Opening the settings is how one opens Ring: after "Quit Ring" in the
    # tray, or where it does not start at login, this brings it back.
    if not service.ring_running():
        window.start_service()
    window.show()
    return int(app.exec())
