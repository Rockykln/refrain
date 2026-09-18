"""The level filter applies to lines already in the window, not just new ones."""

from __future__ import annotations

import logging
import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.ui.log_window import LogWindow  # noqa: E402


class _Bridge(QObject):
    log_record = Signal(str, int)


def test_lowering_the_filter_shows_earlier_debug_lines():
    QApplication.instance() or QApplication(sys.argv)
    bridge = _Bridge()
    window = LogWindow(bridge)
    bridge.log_record.emit("debug line", logging.DEBUG)
    bridge.log_record.emit("info line", logging.INFO)
    assert "debug line" not in window.view.toPlainText()
    window.level_combo.setCurrentIndex(window.level_combo.findData(logging.DEBUG))
    assert "debug line" in window.view.toPlainText()
    window.level_combo.setCurrentIndex(window.level_combo.findData(logging.WARNING))
    assert window.view.toPlainText() == ""
