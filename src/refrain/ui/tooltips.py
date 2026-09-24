"""Tooltips for widgets a list deletes while the mouse may still rest on them."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QFontMetrics, QHelpEvent
from PySide6.QtWidgets import QLabel, QToolTip, QWidget

# Qt opens a tooltip as a child window of the widget it belongs to, so a row
# deleted with its tooltip open takes a visible window down with it. On
# Wayland, Qt's frame-callback thread can still be handling that window after
# it is freed, and Refrain crashed a moment later. Owned by the top-level
# window, the tooltip closes the ordinary way instead.


def show_on_window(widget: QWidget, event: QHelpEvent, rect: QRect | None = None) -> None:
    window = widget.window()
    area = QRect(QPoint(0, 0), widget.size()) if rect is None else rect
    mapped = QRect(widget.mapTo(window, area.topLeft()), area.size())
    QToolTip.showText(event.globalPos(), widget.toolTip(), window, mapped)


class _OnWindow(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            show_on_window(watched, event)  # type: ignore[arg-type]
            return True
        return False


def keep_on_window(widget: QWidget) -> None:
    widget.installEventFilter(_OnWindow(widget))


def text_rect(label: QLabel) -> QRect:
    """Where the text sits inside a label that a layout stretched past it."""
    flags = int(label.alignment())
    if label.wordWrap():
        flags |= int(Qt.TextFlag.TextWordWrap)
    return QFontMetrics(label.font()).boundingRect(label.contentsRect(), flags, label.text())


class _OverText(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip or not isinstance(watched, QLabel):
            return False
        rect = text_rect(watched)
        if rect.contains(event.pos()):  # type: ignore[attr-defined]
            show_on_window(watched, event, rect)  # type: ignore[arg-type]
        else:
            QToolTip.hideText()
        return True


def keep_on_text(label: QLabel) -> None:
    """Explain the text, not the empty space a stretched label keeps to its side."""
    label.installEventFilter(_OverText(label))
