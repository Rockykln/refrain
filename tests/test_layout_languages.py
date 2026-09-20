"""Every window in every shipped language: no clipped text, no overlap."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, QObject, Signal  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

import refrain.app as rapp  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.sources.bluetooth import BluetoothSource  # noqa: E402
from refrain.ui.history_window import HistoryWindow  # noqa: E402
from refrain.ui.layout_check import check_layout  # noqa: E402
from refrain.ui.log_window import LogWindow  # noqa: E402
from refrain.ui.settings_window import SettingsWindow  # noqa: E402
from refrain.ui.status_window import StatusWindow  # noqa: E402
from refrain.ui.update_dialog import UpdateDialog  # noqa: E402
from refrain.ui.welcome_dialog import WelcomeDialog  # noqa: E402
from refrain.updater import ReleaseInfo  # noqa: E402

LANGUAGES = sorted(
    p.stem.removeprefix("refrain_")
    for p in (Path(rapp.__file__).parent / "i18n").glob("refrain_*.qm")
)
# Font metrics differ a little between machines; below this it is noise, not a cut-off word.
TOLERANCE_PX = 4


class _Bridge(QObject):
    log_record = Signal(str, int)


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain v9.9.9",
        body="### Added\n\n- Notes",
        html_url="https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
    )


def _windows(config: Config, locale: QLocale):
    settings = SettingsWindow(config)
    tabs = settings.findChild(QTabWidget)
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        yield f"Settings/{index}", settings
    yield "Status", StatusWindow(locale)
    yield "History", HistoryWindow(locale)
    yield "Welcome", WelcomeDialog()
    yield "Update", UpdateDialog(_release())
    log_window = LogWindow(_Bridge())
    log_window.set_developer_mode(True)
    yield "Live log", log_window


# Scripts this machine may have no font for at all — a CI runner often has none.
SCRIPTS = {
    "ja": QFontDatabase.WritingSystem.Japanese,
    "ko": QFontDatabase.WritingSystem.Korean,
    "zh_CN": QFontDatabase.WritingSystem.SimplifiedChinese,
    "ru": QFontDatabase.WritingSystem.Cyrillic,
    "uk": QFontDatabase.WritingSystem.Cyrillic,
}


@pytest.fixture
def translated(request, monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    script = SCRIPTS.get(request.param)
    if script is not None and script not in QFontDatabase.writingSystems():
        pytest.skip(f"no font for {request.param} on this machine")
    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(SettingsWindow, "_lookup_application_name", lambda self: None)
    translators = rapp._install_translators(app, request.param)
    before = QLocale()
    QLocale.setDefault(rapp.ui_locale(request.param))
    yield app, rapp.ui_locale(request.param)
    for translator in translators:
        app.removeTranslator(translator)
    QLocale.setDefault(before)


@pytest.mark.parametrize("translated", LANGUAGES, indirect=True)
def test_every_window_fits_its_text(translated):
    app, locale = translated
    config = Config()
    config.discord.client_id = "1234567890123456789"
    problems = []
    shown = []
    for name, window in _windows(config, locale):
        if not window.isVisible():
            window.show()
            shown.append(window)
        app.processEvents()
        problems += [
            f"{name}: {f.message()}" for f in check_layout(window) if f.missing > TOLERANCE_PX
        ]
    for window in shown:
        window.hide()
        window.deleteLater()
    app.processEvents()
    assert problems == []
