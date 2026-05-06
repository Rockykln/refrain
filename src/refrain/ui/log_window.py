"""Live-log window — shows every log record as it happens.

Plugged in by ``app.py`` via ``attach_qt_log_bridge()`` from
``logging_setup``. The bridge emits a Qt signal for each ``logging`` call;
this window's slot appends the record to a capped text view.
"""

from __future__ import annotations

import html
import logging

from PySide6.QtGui import QFont, QGuiApplication
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

_LEVEL_COLORS = {
    logging.DEBUG: "#888888",
    logging.INFO: "#cccccc",
    logging.WARNING: "#e8b800",
    logging.ERROR: "#ff7777",
    logging.CRITICAL: "#ff3030",
}


class LogWindow(QDialog):
    def __init__(self, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Live Log"))
        self.setMinimumSize(720, 480)
        # Don't override windowFlags here — QDialog defaults are correct.
        # Adding Qt.WindowType.Window broke visibility on some compositors.
        self.setModal(False)

        # ---- toolbar -------------------------------------------------------
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Level:"))
        self.level_combo = QComboBox()
        for label, value in (
            ("ALL", 0),
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ):
            self.level_combo.addItem(label, value)
        self.level_combo.setCurrentIndex(2)  # INFO is the useful default
        bar.addWidget(self.level_combo)

        self.autoscroll_box = QCheckBox("Auto-scroll")
        self.autoscroll_box.setChecked(True)
        bar.addWidget(self.autoscroll_box)

        bar.addStretch()

        copy_btn = QPushButton("Copy all")
        copy_btn.clicked.connect(self._copy_all)
        bar.addWidget(copy_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.hide)
        bar.addWidget(close_btn)

        # ---- view ----------------------------------------------------------
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(font)
        # Cap memory: the underlying deque drops old blocks once the limit hits.
        self.view.setMaximumBlockCount(5000)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        bridge.log_record.connect(self._append)

    # --------------------------------------------------------------- handlers

    def _append(self, msg: str, level: int) -> None:
        threshold = self.level_combo.currentData() or 0
        if level < threshold:
            return
        color = _LEVEL_COLORS.get(level, "#cccccc")
        self.view.appendHtml(f'<span style="color:{color};">{html.escape(msg)}</span>')
        if self.autoscroll_box.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self) -> None:
        self.view.clear()

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())
