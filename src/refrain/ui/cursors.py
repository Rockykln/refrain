"""One place that decides what the mouse cursor looks like over a widget, so no
button has to remember its own `setCursor`."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QTabBar,
    QWidget,
)

# Widgets a click *does* something to. Text fields are deliberately
# absent: an I-beam over an editable field is the correct affordance,
# and a hand there would suggest the text is a link.
_CLICKABLE = (QAbstractButton, QComboBox)


class _DisabledCursorGuard(QObject):
    """Drops the pointing hand while a widget is disabled.

    Qt keeps whatever cursor a widget was given when it goes disabled,
    so without this a greyed-out *Install update* button still invites
    the click it is going to ignore.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.EnabledChange and isinstance(watched, QWidget):
            if watched.isEnabled():
                watched.setCursor(Qt.PointingHandCursor)
            else:
                # unset rather than force an arrow, so the widget falls
                # back to whatever its parent uses.
                watched.unsetCursor()
        return False


def apply_interactive_cursors(root: QWidget) -> None:
    """Give every clickable widget under ``root`` the pointing-hand cursor.

    Safe to call more than once — setting the same cursor twice is a
    no-op, and the guard is parented to ``root`` so it lives exactly as
    long as the dialog does.
    """
    guard = root.findChild(_DisabledCursorGuard)
    if guard is None:
        guard = _DisabledCursorGuard(root)
    # One class per call: PySide6's findChildren takes a single type,
    # not the tuple its C++ counterpart accepts.
    for cls in _CLICKABLE:
        for widget in root.findChildren(cls):
            if widget.isEnabled():
                widget.setCursor(Qt.PointingHandCursor)
            widget.installEventFilter(guard)

    tab_guard = root.findChild(_TabBarCursorGuard) or _TabBarCursorGuard(root)
    for bar in root.findChildren(QTabBar):
        bar.setMouseTracking(True)
        bar.installEventFilter(tab_guard)


class _TabBarCursorGuard(QObject):
    """A tab bar reaches past its last tab; the hand belongs over the tabs only."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QTabBar):
            return False
        if event.type() in (QEvent.MouseMove, QEvent.Enter):
            where = event.position().toPoint() if hasattr(event, "position") else None
            index = watched.tabAt(
                where if where is not None else watched.mapFromGlobal(QCursor.pos())
            )
            if index >= 0 and watched.isTabEnabled(index):
                watched.setCursor(Qt.PointingHandCursor)
            else:
                watched.unsetCursor()
        elif event.type() == QEvent.Leave:
            watched.unsetCursor()
        return False


class _DialogCursorFilter(QObject):
    """Applies the pointing hand to every dialog as it is shown.

    Refrain's own windows call `apply_interactive_cursors` themselves,
    but the confirmation and error boxes are `QMessageBox`es built at
    the moment they're needed — several of them by the static helpers
    (`QMessageBox.warning(...)`), which never hand us the widget at all.
    By the time a dialog receives its Show event its buttons exist, so
    that is where we catch them.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Show and isinstance(watched, QDialog):
            apply_interactive_cursors(watched)
        return False


def install_global_interactive_cursors(app: QApplication) -> QObject:
    """Cover every dialog the app shows, including ones it doesn't build.

    Returns the filter so the caller can keep it alive — Qt does not
    own it, and a garbage-collected event filter simply stops firing.
    """
    filt = _DialogCursorFilter(app)
    app.installEventFilter(filt)
    return filt
