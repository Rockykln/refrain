"""Live-log window — shows every log record as it happens.

Plugged in by ``app.py`` via ``attach_qt_log_bridge()`` from
``logging_setup``. The bridge emits a Qt signal for each ``logging`` call;
this window's slot appends the record to a capped text view.
"""

from __future__ import annotations

import html
import logging
from collections import deque

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from refrain.paths import assets_dir
from refrain.ui.cursors import apply_interactive_cursors

_MAX_LINES = 5000
# (on a light background, on a dark one)
_ALERT_COLORS = {
    logging.WARNING: ("#8a6400", "#e8b800"),
    logging.ERROR: ("#b3261e", "#ff7777"),
    logging.CRITICAL: ("#8c0000", "#ff6b6b"),
}


def level_colors(palette: QPalette) -> dict[int, str]:
    active = QPalette.ColorGroup.Active
    text = palette.color(active, QPalette.ColorRole.Text)
    base = palette.color(active, QPalette.ColorRole.Base)
    k = 0.7
    muted = QColor(
        round(text.red() * k + base.red() * (1 - k)),
        round(text.green() * k + base.green() * (1 - k)),
        round(text.blue() * k + base.blue() * (1 - k)),
    )
    dark = base.lightness() < 128
    colors = {level: pair[dark] for level, pair in _ALERT_COLORS.items()}
    colors[logging.DEBUG] = muted.name()
    colors[logging.INFO] = text.name()
    return colors


class LogWindow(QDialog):
    def __init__(self, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Live Log"))
        self.setMinimumSize(720, 480)
        # Don't override windowFlags here — QDialog defaults are correct.
        # Adding Qt.WindowType.Window broke visibility on some compositors.
        self.setModal(False)
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ---- toolbar -------------------------------------------------------
        bar = QHBoxLayout()

        bar.addWidget(QLabel(self.tr("Level:")))
        self.level_combo = QComboBox()
        # Level names (DEBUG/INFO/…) stay untranslated — they're the
        # logging module's proper-noun constants and match the Advanced
        # tab's log-level combo. Only the "ALL" sentinel is a real word.
        for label, value in (
            (self.tr("ALL"), 0),
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ):
            self.level_combo.addItem(label, value)
        self.level_combo.setCurrentIndex(2)  # INFO is the useful default
        bar.addWidget(self.level_combo)

        self.autoscroll_box = QCheckBox(self.tr("Auto-scroll"))
        self.autoscroll_box.setChecked(True)
        bar.addWidget(self.autoscroll_box)

        bar.addStretch()

        copy_btn = QPushButton(self.tr("Copy all"))
        copy_btn.clicked.connect(self._copy_all)
        bar.addWidget(copy_btn)

        clear_btn = QPushButton(self.tr("Clear"))
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.hide)
        bar.addWidget(close_btn)

        # ---- view ----------------------------------------------------------
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(font)
        # Cap memory: the underlying deque drops old blocks once the limit hits.
        self.view.setMaximumBlockCount(_MAX_LINES)
        self._lines: deque[tuple[str, int]] = deque(maxlen=_MAX_LINES)
        self._colors = level_colors(self.view.palette())

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        bridge.log_record.connect(self._append)

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    # --------------------------------------------------------------- handlers

    def _append(self, msg: str, level: int) -> None:
        threshold = self.level_combo.currentData() or 0
        if level < threshold:
            return
        self._lines.append((msg, level))
        self._write(msg, level)
        if self.autoscroll_box.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _write(self, msg: str, level: int) -> None:
        color = self._colors.get(level, self._colors[logging.INFO])
        weight = "font-weight:bold;" if level >= logging.CRITICAL else ""
        self.view.appendHtml(f'<span style="color:{color};{weight}">{html.escape(msg)}</span>')

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            colors = level_colors(self.view.palette())
            if colors != self._colors:
                self._colors = colors
                self.view.clear()
                for msg, level in self._lines:
                    self._write(msg, level)

    def _clear(self) -> None:
        self._lines.clear()
        self.view.clear()

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())
