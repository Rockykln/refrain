"""Tooltips for widgets a list deletes while the mouse may still rest on them."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QToolTip, QWidget

# Qt opens a tooltip as a child window of the widget it belongs to, so a row
# deleted with its tooltip open takes a visible window down with it. On
# Wayland, Qt's frame-callback thread can still be handling that window after
# it is freed, and Refrain crashed a moment later. Owned by the top-level
# window, the tooltip closes the ordinary way instead.


def show_on_window(widget: QWidget, event: QHelpEvent) -> None:
    window = widget.window()
    rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
    QToolTip.showText(event.globalPos(), widget.toolTip(), window, rect)


class _OnWindow(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            show_on_window(watched, event)  # type: ignore[arg-type]
            return True
        return False


def keep_on_window(widget: QWidget) -> None:
    widget.installEventFilter(_OnWindow(widget))
