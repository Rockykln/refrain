"""Clickable widgets carry the pointing-hand cursor, everywhere.

The cursor used to be set per widget at construction, which meant it was
set almost nowhere: two buttons in the entire UI had it. `apply_
interactive_cursors` covers a whole dialog in one call, so the assertion
worth making is the end-to-end one — build each real dialog and check
that nothing clickable inside it was missed.

Runs against ``QT_QPA_PLATFORM=offscreen`` so it works in headless CI.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.ui.cursors import apply_interactive_cursors  # noqa: E402

CLICKABLE = (QAbstractButton, QComboBox, QTabBar)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def missed(root) -> list[str]:
    """Enabled clickable widgets under `root` that didn't get the hand."""
    out = []
    for cls in CLICKABLE:
        for w in root.findChildren(cls):
            if w.isEnabled() and w.cursor().shape() != Qt.CursorShape.PointingHandCursor:
                out.append(f"{type(w).__name__} {getattr(w, 'text', lambda: '')()!r}")
    return out


def count(root) -> int:
    return sum(len(root.findChildren(cls)) for cls in CLICKABLE)


# ------------------------------------------------------------------ the helper


def test_clickable_widgets_get_the_hand(qapp):
    root = QWidget()
    layout = QVBoxLayout(root)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "One")
    for w in (QPushButton("go"), QCheckBox("on"), QComboBox(), tabs):
        layout.addWidget(w)
    apply_interactive_cursors(root)
    assert missed(root) == []


def test_text_entry_keeps_its_own_cursor(qapp):
    # An I-beam is the right affordance for an editable field, and a
    # hand there would read as a link.
    root = QWidget()
    layout = QVBoxLayout(root)
    line, spin = QLineEdit(), QSpinBox()
    layout.addWidget(line)
    layout.addWidget(spin)
    apply_interactive_cursors(root)
    assert line.cursor().shape() == Qt.CursorShape.IBeamCursor
    assert spin.cursor().shape() != Qt.CursorShape.PointingHandCursor


def test_disabled_widgets_do_not_invite_the_click(qapp):
    root = QWidget()
    layout = QVBoxLayout(root)
    btn = QPushButton("install")
    btn.setEnabled(False)
    layout.addWidget(btn)
    apply_interactive_cursors(root)
    assert btn.cursor().shape() != Qt.CursorShape.PointingHandCursor
    btn.setEnabled(True)
    assert btn.cursor().shape() == Qt.CursorShape.PointingHandCursor
    btn.setEnabled(False)
    assert btn.cursor().shape() != Qt.CursorShape.PointingHandCursor


def test_calling_twice_installs_one_guard(qapp):
    from refrain.ui.cursors import _DisabledCursorGuard

    root = QWidget()
    QVBoxLayout(root).addWidget(QPushButton("go"))
    apply_interactive_cursors(root)
    apply_interactive_cursors(root)
    assert len(root.findChildren(_DisabledCursorGuard)) == 1


# ------------------------------------------------------------- the real dialogs


def test_settings_window_covers_every_control(qapp, xdg_tmp):
    from refrain.config import Config
    from refrain.ui.settings_window import SettingsWindow

    win = SettingsWindow(Config())
    # Guards the assertion itself: if the window ever stops building its
    # controls, an empty "nothing was missed" would pass silently.
    assert count(win) > 20
    assert missed(win) == []


def test_welcome_dialog_covers_every_control(qapp, xdg_tmp):
    from refrain.ui.welcome_dialog import WelcomeDialog

    dlg = WelcomeDialog()
    assert count(dlg) >= 2
    assert missed(dlg) == []


def test_legal_dialog_covers_every_control(qapp, xdg_tmp):
    from refrain.ui.legal_dialog import LegalDialog

    dlg = LegalDialog()
    assert count(dlg) >= 1
    assert missed(dlg) == []


def test_log_window_covers_every_control(qapp, xdg_tmp):
    from refrain.ui.log_window import LogWindow

    class _Bridge(QObject):
        log_record = Signal(str, int)

    win = LogWindow(_Bridge())
    assert count(win) >= 4
    assert missed(win) == []


def test_update_dialog_covers_every_control(qapp, xdg_tmp):
    from refrain.ui.update_dialog import UpdateDialog
    from refrain.updater import ReleaseInfo

    release = ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain 9.9.9",
        body="- something",
        html_url="https://example.invalid/r",
        appimage_url="",
        appimage_size=0,
        assets=[],
    )
    dlg = UpdateDialog(release)
    assert count(dlg) >= 3
    assert missed(dlg) == []


# ------------------------------------- dialogs Refrain doesn't build itself


def test_message_boxes_are_covered_too(qapp):
    """The confirmations were the one place the hand never reached.

    Reset and Uninstall both put a real decision behind a QMessageBox,
    and several other paths use the static helpers
    (`QMessageBox.warning(...)`), which never hand us a widget to walk.
    The global filter catches them on their Show event instead, by which
    point their buttons exist.
    """
    from PySide6.QtWidgets import QMessageBox

    from refrain.ui.cursors import install_global_interactive_cursors

    filt = install_global_interactive_cursors(qapp)
    try:
        box = QMessageBox()
        box.setText("Reset every setting to its default?")
        box.addButton("Reset", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        # Not shown yet — nothing has covered it.
        assert missed(box) != []
        box.show()
        qapp.processEvents()
        assert count(box) >= 2
        assert missed(box) == []
        box.hide()
        box.deleteLater()
    finally:
        qapp.removeEventFilter(filt)
        filt.deleteLater()
    qapp.processEvents()
