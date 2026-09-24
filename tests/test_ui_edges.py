"""Edge cases across the smaller UI modules: log window, tray, welcome/legal dialogs,
the MPRIS server's signal declaration, and cover-cache pruning."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Signal  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

import refrain.cover_fetcher as cf  # noqa: E402
from refrain.paths import cover_cache_dir  # noqa: E402
from refrain.ui.log_window import LogWindow  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402

# ------------------------------------------------------------------------- #
# log_window.py: palette redraw, Clear, Copy all                            #
# ------------------------------------------------------------------------- #


class _Bridge(QObject):
    log_record = Signal(str, int)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _button(window, text):
    return next(b for b in window.findChildren(QPushButton) if b.text() == text)


def test_a_palette_change_repaints_existing_lines_in_the_new_colors(qapp):
    bridge = _Bridge()
    window = LogWindow(bridge)
    bridge.log_record.emit("debug line", logging.DEBUG)
    window.level_combo.setCurrentIndex(window.level_combo.findData(logging.DEBUG))
    assert "debug line" in window.view.toPlainText()

    dark = QPalette(window.view.palette())
    dark.setColor(QPalette.ColorRole.Base, QColor("#101010"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
    window.view.setPalette(dark)
    before = dict(window._colors)
    window.changeEvent(QEvent(QEvent.Type.PaletteChange))

    assert window._colors != before
    # Redrawn, not lost: the line the user was looking at is still there.
    assert "debug line" in window.view.toPlainText()


def test_clear_empties_the_view_and_forgets_earlier_lines(qapp):
    bridge = _Bridge()
    window = LogWindow(bridge)
    bridge.log_record.emit("info line", logging.INFO)
    assert "info line" in window.view.toPlainText()

    _button(window, "Clear").click()

    assert window.view.toPlainText() == ""
    # Lowering the filter afterwards must not resurrect the cleared line.
    window.level_combo.setCurrentIndex(window.level_combo.findData(logging.DEBUG))
    assert window.view.toPlainText() == ""


def test_copy_all_puts_the_visible_log_on_the_clipboard(qapp):
    bridge = _Bridge()
    window = LogWindow(bridge)
    bridge.log_record.emit("info line", logging.INFO)

    _button(window, "Copy all").click()

    assert QGuiApplication.clipboard().text() == window.view.toPlainText()


# ------------------------------------------------------------------------- #
# tray.py: hint bubble + its click callback                                 #
# ------------------------------------------------------------------------- #


@pytest.fixture
def tray(qapp):
    return TrayIcon()


def test_show_hint_pops_a_tray_notification_with_the_current_icon(tray):
    calls = []
    tray._tray.showMessage = lambda *args: calls.append(args)
    tray.show_hint("Discord needs your attention")
    assert len(calls) == 1
    title, text, icon, timeout_ms = calls[0]
    assert (title, text, timeout_ms) == ("Refrain", "Discord needs your attention", 6000)
    assert icon.cacheKey() == tray._tray.icon().cacheKey()


def test_clicking_a_hint_notification_runs_its_action_exactly_once(tray):
    clicks = []
    tray.show_hint("Discord needs your attention", on_click=lambda: clicks.append(1))
    tray._tray.messageClicked.emit()
    tray._tray.messageClicked.emit()
    assert clicks == [1]


# ------------------------------------------------------------------------- #
# welcome_dialog.py: growing the dialog to fit the diagnostics text         #
# ------------------------------------------------------------------------- #


def test_a_missing_layout_leaves_the_welcome_dialogs_size_alone(qapp, monkeypatch):
    from refrain.ui.welcome_dialog import WelcomeDialog

    dialog = WelcomeDialog()
    monkeypatch.setattr(dialog, "layout", lambda: None)
    before = dialog.size()
    dialog._grow_to_fit()
    assert dialog.size() == before
    dialog.deleteLater()


def test_a_label_that_is_not_word_wrapped_is_skipped_when_measuring_the_deficit(qapp, monkeypatch):
    """Only the word-wrapped diagnostics rows can lose lines to clipping; a
    fixed-width label is left out of the growth calculation entirely."""
    from refrain.ui.welcome_dialog import WelcomeDialog

    dialog = WelcomeDialog()
    monkeypatch.setattr(dialog._diag_discord, "wordWrap", lambda: False)
    monkeypatch.setattr(dialog._diag_itunes, "wordWrap", lambda: False)
    before = dialog.size()
    dialog._grow_to_fit()
    assert dialog.size() == before
    dialog.deleteLater()


# ------------------------------------------------------------------------- #
# legal_dialog.py: links open the system browser, not an in-app view        #
# ------------------------------------------------------------------------- #


def test_clicking_a_legal_notice_link_opens_it_in_the_system_browser(qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel

    import refrain.ui.legal_dialog as ld

    opened = []
    monkeypatch.setattr(
        "refrain.ui.external_link.confirm_and_open",
        lambda parent, url, player="": opened.append(url) or True,
    )
    dialog = ld.LegalDialog()
    link_label = next(lbl for lbl in dialog.findChildren(QLabel) if "href" in lbl.text())

    link_label.linkActivated.emit(ld.LEGAL_URL)

    assert opened == [ld.LEGAL_URL]
    dialog.deleteLater()


# ------------------------------------------------------------------------- #
# mpris_server.py: the PropertiesChanged signal declaration itself          #
# ------------------------------------------------------------------------- #


def test_properties_changed_runs_without_raising_before_the_server_is_exported():
    pytest.importorskip("dbus")
    from refrain.sources.mpris_server import MPRISServer

    server = MPRISServer(on_play_pause=lambda: None, on_next=lambda: None, on_previous=lambda: None)
    server._locations = []  # constructed, never exported to a bus — no listener yet

    assert (
        server.PropertiesChanged(
            "org.freedesktop.DBus.Properties", {"PlaybackStatus": "Playing"}, []
        )
        is None
    )


# ------------------------------------------------------------------------- #
# cover_fetcher.py: pruning survives a file that refuses to be deleted      #
# ------------------------------------------------------------------------- #


def test_a_cache_file_that_cannot_be_deleted_does_not_stop_the_rest_from_being_pruned(
    xdg_tmp, monkeypatch
):
    cache = cover_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    now = time.time()
    stuck = cache / "stuck.txt"
    old = cache / "old.txt"
    for i, p in enumerate((stuck, old)):
        p.write_bytes(b"x")
        os.utime(p, (now - 1000 + i, now - 1000 + i))

    real_unlink = Path.unlink

    def _guarded(self, *a, **kw):
        if self == stuck:
            raise OSError("file is busy")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _guarded)

    assert cf._prune_lookup_cache(0) == 1
    assert stuck.exists()
    assert not old.exists()


def test_closing_the_wizard_asks_before_throwing_away_a_typed_id(qapp, monkeypatch):
    """Applying an empty field asks; discarding a filled one used to not."""
    from refrain.ui.welcome_dialog import WelcomeDialog

    dialog = WelcomeDialog()
    dialog.client_id_edit.setText("1234567890123456789")
    emitted = []
    dialog.applied.connect(emitted.append)

    asked = []
    monkeypatch.setattr(dialog, "_confirm_discarding_id", lambda: asked.append(1) or False)
    dialog.reject()
    assert asked == [1] and emitted == []

    monkeypatch.setattr(dialog, "_confirm_discarding_id", lambda: asked.append(1) or True)
    dialog.reject()
    assert asked == [1, 1] and emitted == [""]
    dialog.deleteLater()


def test_closing_an_empty_wizard_asks_nothing(qapp, monkeypatch):
    from refrain.ui.welcome_dialog import WelcomeDialog

    dialog = WelcomeDialog()
    emitted = []
    dialog.applied.connect(emitted.append)
    monkeypatch.setattr(
        dialog, "_confirm_discarding_id", lambda: pytest.fail("asked about an empty field")
    )
    dialog.reject()
    assert emitted == [""]
    dialog.deleteLater()
