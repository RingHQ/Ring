"""The About page of the settings window, with the used counter."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ring import __version__
from ring.settings.widgets import Card, pretty, scroll_page
from ring.stats import Stats


class AboutPage(QWidget):
    """The used counter and what this program is."""

    def __init__(self, path: Path, stats: Stats) -> None:
        super().__init__()
        self._stats = stats
        self._count = QLabel()
        self._count.setObjectName("counter")
        self._count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_caption = QLabel()
        self._count_caption.setObjectName("hint")
        self._count_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._top = QGridLayout()
        self._top.setColumnStretch(1, 1)
        self._top.setHorizontalSpacing(12)
        self._top_rows: list[tuple[str, int]] = []
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._show_stats)
        reset = QPushButton("Reset the counter\u2026")
        reset.clicked.connect(self._reset_stats)
        counter_buttons = QHBoxLayout()
        counter_buttons.addStretch(1)
        counter_buttons.addWidget(refresh)
        counter_buttons.addWidget(reset)
        counter = Card("Used counter")
        counter_layout = QVBoxLayout(counter.body)
        counter_layout.addWidget(self._count)
        counter_layout.addWidget(self._count_caption)
        counter_layout.addSpacing(10)
        counter_layout.addLayout(self._top)
        counter_layout.addLayout(counter_buttons)
        self._show_stats()

        text = QLabel(
            f"<b>Ring</b> {__version__}<br>A radial window snapper for Linux.<br>"
            "Licensed under the GNU GPL v3."
        )
        text.setOpenExternalLinks(True)
        location = QLabel(f"Settings file: {path}")
        location.setObjectName("hint")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about = Card("About")
        about_layout = QVBoxLayout(about.body)
        about_layout.addWidget(text)
        about_layout.addWidget(location)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll_page("About", "How much Ring has done for you.", counter, about))

    def _show_stats(self) -> None:
        self._stats.reload()
        total = self._stats.total
        self._count.setText(f"{total:,}")
        noun = "window" if total == 1 else "windows"
        self._count_caption.setText(f"{noun} moved with Ring since {self._stats.since}")
        while (item := self._top.takeAt(0)) is not None:
            if item.widget() is not None:
                item.widget().deleteLater()
        self._top_rows = self._stats.top(6)
        most = self._top_rows[0][1] if self._top_rows else 1
        for row, (name, count) in enumerate(self._top_rows):
            bar = QProgressBar()
            bar.setRange(0, most)
            bar.setValue(count)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            number = QLabel(f"{count:,}")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._top.addWidget(QLabel(pretty(name)), row, 0)
            self._top.addWidget(bar, row, 1)
            self._top.addWidget(number, row, 2)

    def _reset_stats(self) -> None:
        answer = QMessageBox.question(self, "Ring", "Set the used counter back to zero?")
        if answer == QMessageBox.StandardButton.Yes:
            self._stats.reset()
            self._show_stats()
