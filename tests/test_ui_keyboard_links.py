"""Every link Refrain shows has to be reachable without a mouse."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from refrain.config import Config  # noqa: E402
from refrain.sources.bluetooth import BluetoothSource  # noqa: E402
from refrain.ui.legal_dialog import LegalDialog  # noqa: E402
from refrain.ui.settings_window import SettingsWindow  # noqa: E402
from refrain.ui.status_window import StatusWindow  # noqa: E402
from tests.test_layout_languages import _windows  # noqa: E402

BY_KEYBOARD = Qt.TextInteractionFlag.LinksAccessibleByKeyboard


@pytest.fixture
def app(monkeypatch):
    instance = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(SettingsWindow, "_lookup_application_name", lambda self: None)
    return instance


def _mouse_only(window) -> list[str]:
    return [
        label.text()[:60]
        for label in window.findChildren(QLabel)
        if "href=" in label.text() and not (label.textInteractionFlags() & BY_KEYBOARD)
    ]


def test_no_window_hides_a_link_behind_the_mouse(app):
    config = Config()
    config.discord.client_id = "1234567890123456789"
    locale = QLocale("en")
    found = {}
    for name, window in _windows(config, locale):
        stuck = _mouse_only(window)
        if stuck:
            found[name] = stuck
    assert found == {}


def test_the_legal_notice_links_answer_to_the_keyboard(app):
    dialog = LegalDialog()
    labels = [lbl for lbl in dialog.findChildren(QLabel) if "href=" in lbl.text()]
    assert labels  # the notice is where most of the links are
    assert _mouse_only(dialog) == []


def test_the_crash_report_link_answers_to_the_keyboard(app, tmp_path):
    # The banner is empty until a crash, so walking the windows never
    # sees this link; it has to be filled in first.
    window = StatusWindow()
    report = tmp_path / "crash.log"
    report.write_text("x", encoding="utf-8")
    window.set_crash_report(report)
    assert "href=" in window.banner.text()
    assert _mouse_only(window) == []
