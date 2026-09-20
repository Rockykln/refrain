"""Finds text that does not fit, cut-off wrapped labels, unwanted scrollbars and overlaps in a window.

Findings name widgets by path only, never by their text, which may be a song title or a device name."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QFocusFrame,
    QGroupBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QRubberBand,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionGroupBox,
    QTabBar,
    QTabWidget,
    QWidget,
)

log = logging.getLogger("refrain.ui.layout")

# Set to True on widgets that shorten their text on purpose; they and
# everything inside them are left alone.
ELIDES_PROPERTY = "refrainElides"
# Set to True on scroll areas that are meant to scroll (a list that grows).
SCROLLS_PROPERTY = "refrainScrolls"

CLIPPED = "clipped"
CUT_OFF = "cut-off"
SCROLLBAR = "scrollbar"
OVERLAP = "overlap"

# Rounding in style metrics and fractional font advances; fractional display
# scaling (1.5x) rounds widget edges by a few pixels either way.
_SLACK = 1
_OVERLAP_SLACK = 4
_SEPARATOR = " › "
_VERTICAL_TABS = {
    QTabBar.Shape.RoundedWest,
    QTabBar.Shape.RoundedEast,
    QTabBar.Shape.TriangularWest,
    QTabBar.Shape.TriangularEast,
}
_OVERLAYS = (QFocusFrame, QRubberBand)


@dataclass(frozen=True)
class LayoutFinding:
    path: str
    kind: str
    missing: int
    other: str = ""

    def message(self) -> str:
        detail = f"overlaps {self.other}" if self.kind == OVERLAP else self.kind
        return f"UI: {self.path} needs {self.missing} px more ({detail})"


def check_layout(window: QWidget) -> list[LayoutFinding]:
    """Measure every visible widget of ``window`` at its current size.

    Call it after the window is shown and pending events are processed,
    so layouts and scroll ranges are up to date."""
    return _Checker(window).run()


def log_findings(findings: Iterable[LayoutFinding]) -> None:
    for finding in findings:
        log.warning("%s", finding.message())


class _Checker:
    def __init__(self, root: QWidget):
        self.root = root
        self.findings: list[LayoutFinding] = []

    def run(self) -> list[LayoutFinding]:
        widgets = [w for w in self.root.findChildren(QWidget) if self._checked(w)]
        for w in widgets:
            self._check_widget(w)
        for parent in [self.root, *widgets]:
            self._check_overlaps(parent)
        return self.findings

    def _checked(self, w: QWidget) -> bool:
        if w.window() is not self.root or not w.isVisibleTo(self.root):
            return False
        node: QWidget | None = w
        while node is not None and node is not self.root:
            if node.property(ELIDES_PROPERTY):
                return False
            node = node.parentWidget()
        return True

    def _add(self, w: QWidget, kind: str, missing: int, suffix: str = "") -> None:
        if missing > _SLACK:
            path = self._path(w) + (f"{_SEPARATOR}{suffix}" if suffix else "")
            self.findings.append(LayoutFinding(path, kind, missing))

    def _check_widget(self, w: QWidget) -> None:
        if isinstance(w, QLabel):
            self._check_label(w)
        elif isinstance(w, (QPushButton, QCheckBox, QRadioButton)):
            self._check_button(w)
        elif isinstance(w, QComboBox):
            self._check_combo(w)
        elif isinstance(w, QTabBar):
            self._check_tabs(w)
        elif isinstance(w, QGroupBox):
            self._check_group_title(w)
        elif isinstance(w, QScrollArea):
            self._check_scroll(w)

    def _check_label(self, w: QLabel) -> None:
        if not w.text() or not w.pixmap().isNull():
            return
        if w.wordWrap():
            self._add(w, CUT_OFF, w.heightForWidth(w.width()) - w.height())
        else:
            # The base implementation: a subclass may report less than its text needs.
            need = QLabel.minimumSizeHint(w).width()
            self._add(w, CLIPPED, need - self._visible_width(w))

    def _check_button(self, w: QAbstractButton) -> None:
        if not w.text():
            return
        opt = QStyleOptionButton()
        w.initStyleOption(opt)
        content = w.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, w.text())
        width = content.width()
        if not w.icon().isNull():
            width += w.iconSize().width() + 4
        if isinstance(w, QPushButton):
            contents_type = QStyle.ContentsType.CT_PushButton
            if w.menu() is not None:
                width += w.style().pixelMetric(QStyle.PixelMetric.PM_MenuButtonIndicator, opt, w)
        elif isinstance(w, QCheckBox):
            contents_type = QStyle.ContentsType.CT_CheckBox
        else:
            contents_type = QStyle.ContentsType.CT_RadioButton
        # Measured with plenty of text, so a style's minimum button width
        # does not count as room the text needs.
        big = 10_000
        padded = w.style().sizeFromContents(
            contents_type, opt, QSize(width + big, content.height()), w
        )
        self._add(w, CLIPPED, padded.width() - big - self._visible_width(w))

    def _check_combo(self, w: QComboBox) -> None:
        if w.isEditable() or not w.currentText():
            return
        opt = QStyleOptionComboBox()
        w.initStyleOption(opt)
        field = w.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, w
        )
        need = w.fontMetrics().horizontalAdvance(w.currentText())
        if not w.itemIcon(w.currentIndex()).isNull():
            need += w.iconSize().width() + 4
        room = field.width() - (w.width() - self._visible_width(w))
        self._add(w, CLIPPED, need - room)

    def _check_tabs(self, w: QTabBar) -> None:
        vertical = w.shape() in _VERTICAL_TABS

        def length(size: QSize | QRect) -> int:
            return size.height() if vertical else size.width()

        before = len(self.findings)
        for i in range(w.count()):
            if w.isTabVisible(i):
                self._add(
                    w, CLIPPED, length(w.tabSizeHint(i)) - length(w.tabRect(i)), f"tab {i + 1}"
                )
        if len(self.findings) == before:
            # Nothing elided, but the bar may still hide tabs behind scroll buttons.
            self._add(w, CLIPPED, length(w.sizeHint()) - length(w.size()))

    def _check_group_title(self, w: QGroupBox) -> None:
        if not w.title():
            return
        opt = QStyleOptionGroupBox()
        w.initStyleOption(opt)
        label = w.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxLabel, w
        )
        text = w.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, w.title()).width()
        need = max(label.left(), 0) + max(label.width(), text)
        self._add(w, CLIPPED, need - self._visible_width(w))

    def _check_scroll(self, w: QScrollArea) -> None:
        if w.property(SCROLLS_PROPERTY):
            return
        self._add(w, SCROLLBAR, w.verticalScrollBar().maximum(), "vertical")
        self._add(w, SCROLLBAR, w.horizontalScrollBar().maximum(), "horizontal")

    def _check_overlaps(self, parent: QWidget) -> None:
        kids = [
            c
            for c in parent.children()
            if isinstance(c, QWidget)
            and not isinstance(c, _OVERLAYS)
            and not c.isWindow()
            and self._checked(c)
        ]
        for i, a in enumerate(kids):
            for b in kids[i + 1 :]:
                inter = a.geometry().intersected(b.geometry())
                if inter.width() > _OVERLAP_SLACK and inter.height() > _OVERLAP_SLACK:
                    self.findings.append(
                        LayoutFinding(
                            self._path(a),
                            OVERLAP,
                            min(inter.width(), inter.height()),
                            self._path(b),
                        )
                    )

    def _visible_width(self, w: QWidget) -> int:
        """Width left after the ancestors clip it; a scroll area's content may be cut on purpose."""
        rect = QRect(w.mapTo(self.root, QPoint(0, 0)), w.size())
        parent = w.parentWidget()
        while parent is not None and parent is not self.root:
            area = parent.parentWidget()
            if isinstance(area, QAbstractScrollArea) and area.viewport() is parent:
                break
            rect = rect.intersected(QRect(parent.mapTo(self.root, QPoint(0, 0)), parent.size()))
            parent = area
        else:
            rect = rect.intersected(self.root.rect())
        return max(rect.width(), 0)

    def _path(self, w: QWidget) -> str:
        chain: list[QWidget] = []
        node: QWidget | None = w
        while node is not None and node is not self.root:
            chain.append(node)
            node = node.parentWidget()
        chain.reverse()
        root = self.root
        parts = [root.windowTitle() or _object_name(root) or type(root).__name__]
        anchor = root
        for node in chain[:-1]:
            name = _tab_label(node) or _object_name(node)
            if name:
                parts.append(name)
                anchor = node
        parts.append(_object_name(w) or _indexed_name(w, anchor))
        return _SEPARATOR.join(parts)


def _object_name(w: QWidget) -> str:
    name = w.objectName()
    return "" if name.startswith("qt_") else name


def _tab_label(w: QWidget) -> str:
    stack = w.parentWidget()
    tabs = stack.parentWidget() if isinstance(stack, QStackedWidget) else None
    if not isinstance(tabs, QTabWidget):
        return ""
    return tabs.tabText(tabs.indexOf(w)).replace("&&", "\0").replace("&", "").replace("\0", "&")


def _indexed_name(w: QWidget, anchor: QWidget) -> str:
    same = [c for c in anchor.findChildren(QWidget) if type(c) is type(w)]
    return f"{type(w).__name__}[{same.index(w)}]"
