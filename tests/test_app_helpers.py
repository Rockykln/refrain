"""app.py helpers: translators, Qt log bridge, signals, autostart sync, log level, update dialog."""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QtMsgType  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain import app  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.updater import ReleaseInfo  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def translators(qapp):
    installed = []
    yield installed
    for t in installed:
        qapp.removeTranslator(t)


NO_INFO_YET = "No update information available yet. Try again in a moment."


def _tr(text: str) -> str:
    return QCoreApplication.translate("app", text)


def test_explicit_language_translates_refrain_strings(qapp, translators):
    translators.extend(app._install_translators(qapp, "de"))
    assert (
        _tr(NO_INFO_YET)
        == "Noch keine Update-Information vorhanden. Bitte gleich erneut versuchen."
    )


def test_explicit_language_also_translates_qt_stock_buttons(qapp, translators):
    qt_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    if not (qt_dir / "qtbase_de.qm").exists():
        pytest.skip("no qtbase_de.qm in this Qt")
    translators.extend(app._install_translators(qapp, "de"))
    assert len(translators) == 2
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Abbrechen"


def test_region_variant_falls_back_to_the_language_catalog(qapp, translators):
    translators.extend(app._install_translators(qapp, "de_AT"))
    assert (
        _tr(NO_INFO_YET)
        == "Noch keine Update-Information vorhanden. Bitte gleich erneut versuchen."
    )


def test_unshipped_language_uses_the_english_plural_catalog(qapp, translators):
    i18n = Path(app.__file__).parent / "i18n"
    code = next(c for c in ("is", "ga", "eo", "mt") if not any(i18n.glob(f"refrain_{c}*")))
    translators.extend(app._install_translators(qapp, code))
    assert _tr("Updates") == "Updates"
    n = 12
    text = QCoreApplication.translate("HistoryWindow", "Last %n song(s)", None, n)
    assert text == "Last 12 songs"


def test_qt_warnings_land_in_the_log_at_their_level(caplog):
    with caplog.at_level(logging.DEBUG, logger="refrain.qt"):
        app._qt_message_handler(QtMsgType.QtWarningMsg, None, "qt says hi")
        app._qt_message_handler(QtMsgType.QtCriticalMsg, None, "qt is sad")
        app._qt_message_handler(None, None, "unknown kind")
    levels = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "refrain.qt"]
    assert levels == [
        (logging.WARNING, "qt says hi"),
        (logging.ERROR, "qt is sad"),
        (logging.INFO, "unknown kind"),
    ]


def test_portal_noise_is_dropped(caplog):
    with caplog.at_level(logging.DEBUG, logger="refrain.qt"):
        app._qt_message_handler(
            QtMsgType.QtWarningMsg, None, "qt.qpa: Failed to register with host portal"
        )
    assert [r for r in caplog.records if r.name == "refrain.qt"] == []


def test_sigint_and_sigterm_quit_the_event_loop(qapp, monkeypatch):
    handlers = {}
    monkeypatch.setattr(app.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))

    class FakeApp:
        quits = 0

        def quit(self):
            self.quits += 1

    fake = FakeApp()
    app._install_signal_handlers(fake)
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    handlers[signal.SIGINT](signal.SIGINT, None)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert fake.quits == 2
    assert fake._refrain_signal_timer.isActive()
    fake._refrain_signal_timer.stop()


@pytest.fixture
def autostart(monkeypatch):
    state = {"enabled": False, "calls": []}
    monkeypatch.setattr(app, "autostart_is_enabled", lambda: state["enabled"])
    monkeypatch.setattr(app, "autostart_enable", lambda: state["calls"].append("enable"))
    monkeypatch.setattr(app, "autostart_disable", lambda: state["calls"].append("disable"))
    monkeypatch.setattr(app, "autostart_refresh", lambda: state["calls"].append("refresh"))
    return state


@pytest.mark.parametrize(
    ("wanted", "enabled", "expected"),
    [
        (True, False, ["enable"]),
        (False, True, ["disable"]),
        (True, True, ["refresh"]),
        (False, False, []),
    ],
)
def test_autostart_follows_the_setting(autostart, wanted, enabled, expected):
    config = Config()
    config.behavior.autostart = wanted
    autostart["enabled"] = enabled
    app._sync_autostart(config)
    assert autostart["calls"] == expected


@pytest.fixture
def root_level(monkeypatch):
    root = logging.getLogger()
    before = root.level
    monkeypatch.setattr(app, "_forced_debug", False)
    root.setLevel(logging.INFO)
    yield root
    root.setLevel(before)


def _with_level(value) -> Config:
    config = Config()
    config.advanced.log_level = value
    return config


def test_configured_level_is_applied(root_level):
    app._apply_log_level(_with_level("debug"))
    assert root_level.level == logging.DEBUG


@pytest.mark.parametrize("value", ["", "chatty", 5])
def test_empty_or_unknown_level_means_info(root_level, value):
    root_level.setLevel(logging.WARNING)
    app._apply_log_level(_with_level(value))
    assert root_level.level == logging.INFO


def test_debug_flag_outranks_the_config(root_level, monkeypatch):
    monkeypatch.setattr(app, "_forced_debug", True)
    root_level.setLevel(logging.DEBUG)
    app._apply_log_level(_with_level("WARNING"))
    assert root_level.level == logging.DEBUG


def test_a_non_level_logging_constant_is_treated_as_unknown(root_level):
    app._apply_log_level(_with_level("basic_format"))
    assert root_level.level == logging.INFO


def _release(version="99.0.0") -> ReleaseInfo:
    return ReleaseInfo(
        tag=f"v{version}",
        version=version,
        name=f"Refrain v{version}",
        body="notes",
        html_url=f"https://github.com/Rockykln/refrain/releases/tag/v{version}",
    )


@pytest.fixture
def dialogs(monkeypatch):
    seen = {"dialogs": [], "boxes": []}

    class FakeDialog:
        def __init__(self, release, parent=None):
            self.release = release
            self.parent = parent

        def exec(self):
            seen["dialogs"].append((self.release, self.parent))

    class FakeBox:
        @staticmethod
        def information(parent, title, text):
            seen["boxes"].append((parent, title, text))

    monkeypatch.setattr(app, "UpdateDialog", FakeDialog)
    monkeypatch.setattr(app, "QMessageBox", FakeBox)
    return seen


class _Updater:
    def __init__(self, latest=None):
        self.latest = latest


def test_update_dialog_opens_for_the_signalled_release(dialogs):
    parent = object()
    signalled = _release("99.0.0")
    app._open_update_dialog_factory(_Updater(_release("98.0.0")), parent)(signalled)
    assert dialogs["dialogs"] == [(signalled, parent)]


def test_tray_click_uses_the_last_known_release(dialogs):
    latest = _release()
    app._open_update_dialog_factory(_Updater(latest), None)()
    assert dialogs["dialogs"] == [(latest, None)]


def test_without_release_info_the_user_is_told_to_wait(dialogs):
    app._open_update_dialog_factory(_Updater(None), None)()
    assert dialogs["dialogs"] == []
    assert "No update information" in dialogs["boxes"][0][2]
