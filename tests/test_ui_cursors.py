"""Clickable widgets carry the pointing-hand cursor, checked on each real dialog."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.ui.cursors import apply_interactive_cursors  # noqa: E402

# Tab bars are checked on their own: the hand belongs over a tab, not beside it.
CLICKABLE = (QAbstractButton, QComboBox)


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


def test_the_hand_follows_the_tabs_not_the_empty_strip(qapp):
    from PySide6.QtGui import QMouseEvent

    root = QWidget()
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "One")
    tabs.addTab(QWidget(), "Two")
    QVBoxLayout(root).addWidget(tabs)
    root.resize(600, 300)
    apply_interactive_cursors(root)
    root.show()
    qapp.processEvents()
    bar = tabs.tabBar()
    bar.resize(400, bar.height())  # a real tab bar is wider than its tabs

    def move_to(point):
        QApplication.sendEvent(
            bar,
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(point),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    move_to(bar.tabRect(0).center())
    assert bar.cursor().shape() == Qt.CursorShape.PointingHandCursor

    move_to(QPoint(bar.tabRect(1).right() + 40, bar.height() // 2))
    assert bar.cursor().shape() != Qt.CursorShape.PointingHandCursor
    root.hide()


def test_the_hand_leaves_the_tab_bar_along_with_the_mouse(qapp):
    from PySide6.QtGui import QMouseEvent

    root = QWidget()
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "One")
    QVBoxLayout(root).addWidget(tabs)
    root.resize(600, 300)
    apply_interactive_cursors(root)
    root.show()
    qapp.processEvents()
    bar = tabs.tabBar()

    QApplication.sendEvent(
        bar,
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(bar.tabRect(0).center()),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert bar.cursor().shape() == Qt.CursorShape.PointingHandCursor

    QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))
    assert bar.cursor().shape() != Qt.CursorShape.PointingHandCursor
    root.hide()


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
    """Message boxes, including the static helpers, get the cursor on their Show event."""
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
