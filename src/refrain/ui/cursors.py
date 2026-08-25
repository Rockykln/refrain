"""One place that decides what the mouse cursor looks like over a widget.

Refrain's dialogs are built by hand, and setting a cursor per widget at
construction means every new button is one `setCursor` away from being
the odd one out — which is exactly what had happened: two buttons in the
whole UI carried the pointing hand and everything else kept the arrow.
The dialogs call `apply_interactive_cursors(self)` once, after their
layout is built, and every clickable child is covered — including the
ones added later.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractButton, QComboBox, QTabBar, QWidget

# Widgets a click *does* something to. Text fields are deliberately
# absent: an I-beam over an editable field is the correct affordance,
# and a hand there would suggest the text is a link.
_CLICKABLE = (QAbstractButton, QComboBox, QTabBar)


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
