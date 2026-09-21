"""The Discord name lookup never blocks the window or outlives it into a crash."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import refrain.ui.settings_window as sw  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.ui.layout_check import CLIPPED, check_layout  # noqa: E402


@pytest.fixture
def slow_lookup(monkeypatch):
    release = threading.Event()

    def fetch(client_id):
        release.wait(5)
        return sw.FOUND, f"name for {client_id}"

    monkeypatch.setattr(sw, "fetch_application_name", fetch)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    return release


def _pump(app, until, timeout=3.0):
    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_a_slow_lookup_neither_blocks_nor_aborts(slow_lookup):
    app = QApplication.instance() or QApplication(sys.argv)
    config = Config()
    config.discord.resolve_app_name = True
    win = sw.SettingsWindow(config)
    win.client_id_input.setText("12345678901234567")
    started = time.monotonic()
    win._lookup_application_name()
    win.client_id_input.setText("12345678901234568")
    win._lookup_application_name()
    assert time.monotonic() - started < 0.5
    slow_lookup.set()
    _pump(app, lambda: not win._app_name_workers)
    assert "12345678901234568" in win.app_name_label.text()


def test_the_elided_app_name_label_is_skipped_by_the_layout_checker(monkeypatch):
    """app_name_label shortens its text on purpose (refrainElides); the layout
    checker's normal QLabel heuristic would otherwise flag it as clipped whenever
    that heuristic and the widget's own elision disagree by even a few pixels."""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QLabel

    app = QApplication.instance() or QApplication(sys.argv)
    config = Config()
    win = sw.SettingsWindow(config)
    win.app_name_label.setText("A rather long demo application name")
    win.app_name_label.setVisible(True)
    win.show()
    app.processEvents()

    real_hint = QLabel.minimumSizeHint

    def inflated(self):
        if self is win.app_name_label:
            return QSize(9999, real_hint(self).height())
        return real_hint(self)

    monkeypatch.setattr(QLabel, "minimumSizeHint", inflated)
    findings = check_layout(win)
    assert not any(f.kind == CLIPPED and "_ElidedHint" in f.path for f in findings)
    win.close()
    win.deleteLater()


def test_the_window_can_go_while_a_lookup_runs(slow_lookup):
    app = QApplication.instance() or QApplication(sys.argv)
    config = Config()
    config.discord.resolve_app_name = True
    win = sw.SettingsWindow(config)
    win.client_id_input.setText("12345678901234567")
    win._lookup_application_name()
    win.close()
    win.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(win)
    slow_lookup.set()
    time.sleep(0.1)
    app.processEvents()
