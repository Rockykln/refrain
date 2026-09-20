"""The layout check finds text that does not fit, cut-off labels, scrollbars and overlaps."""

from __future__ import annotations

import logging
import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFocusFrame,
    QGroupBox,
    QLabel,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.ui.layout_check import (  # noqa: E402
    CLIPPED,
    CUT_OFF,
    OVERLAP,
    SCROLLBAR,
    LayoutFinding,
    check_layout,
    log_findings,
)

LONG = "A rather long demo caption that needs room"


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(app):
    windows: list[QWidget] = []

    def make(*widgets: QWidget, title: str = "Demo", layout: bool = True) -> QWidget:
        root = QWidget()
        root.setWindowTitle(title)
        if layout:
            box = QVBoxLayout(root)
            for w in widgets:
                box.addWidget(w)
        else:
            for w in widgets:
                w.setParent(root)
        windows.append(root)
        return root

    yield make
    for root in windows:
        root.close()
        root.deleteLater()
    app.processEvents()


def _show(root: QWidget, width: int = 400, height: int = 300) -> list[LayoutFinding]:
    root.resize(width, height)
    root.show()
    QApplication.processEvents()
    return check_layout(root)


def _named(w: QWidget, name: str) -> QWidget:
    w.setObjectName(name)
    return w


def _narrow(w: QWidget, width: int = 30) -> QWidget:
    w.setFixedWidth(width)
    return w


def test_a_clean_window_has_no_findings(window):
    root = window(
        QLabel("Name"),
        QPushButton("Apply"),
        QCheckBox("Enabled"),
        QRadioButton("Option"),
        QComboBox(),
        QGroupBox("Group"),
    )
    assert _show(root) == []


def test_a_label_too_narrow_for_its_text(window):
    label = _named(_narrow(QLabel(LONG), 40), "caption")
    [finding] = _show(window(label))
    assert finding.path == "Demo › caption"
    assert finding.kind == CLIPPED
    assert finding.missing == label.minimumSizeHint().width() - 40


@pytest.mark.parametrize("make", [QPushButton, QCheckBox, QRadioButton])
def test_buttons_too_narrow_for_their_text(window, make):
    button = _named(_narrow(make("Apply these settings now")), "apply")
    [finding] = _show(window(button))
    assert (finding.path, finding.kind) == ("Demo › apply", CLIPPED)
    assert finding.missing > button.fontMetrics().horizontalAdvance("Apply these") - 30


def test_a_button_is_measured_by_its_text_not_the_style_minimum(window):
    # Styles give text buttons a minimum width; a short label fits well below it.
    button = QPushButton("OK")
    button.setFixedWidth(button.fontMetrics().horizontalAdvance("OK") + 24)
    assert _show(window(button)) == []


def test_icon_and_menu_indicator_count_as_needed_room(window):
    plain = _narrow(QPushButton("Menu"), 1)
    fancy = _named(_narrow(QPushButton("Menu"), 1), "fancy")
    fancy.setIcon(QPixmap(16, 16))
    fancy.setMenu(QMenu(fancy))
    root = window(plain, fancy)
    [plain_need, fancy_need] = [f.missing + 1 for f in _show(root)]
    assert fancy_need >= plain_need + 16 + 4
    plain.setFixedWidth(plain_need)
    fancy.setFixedWidth(plain_need)
    assert [f.path for f in _show(root)] == ["Demo › fancy"]


def test_a_combo_too_narrow_for_the_current_item(window):
    combo = _named(_narrow(QComboBox(), 60), "language")
    combo.addItem("A short one")
    combo.addItem(LONG)
    root = window(combo)
    assert [f.path for f in _show(root)] == ["Demo › language"]
    combo.setCurrentIndex(1)
    [finding] = check_layout(root)
    assert finding.missing > _show(root)[0].missing - 1


def test_a_combo_icon_needs_room_too(window):
    combo = QComboBox()
    combo.addItem("Mid length item")
    combo.setFixedWidth(combo.sizeHint().width())
    root = window(combo)
    assert _show(root) == []
    combo.setItemIcon(0, QPixmap(16, 16))
    assert [f.kind for f in check_layout(root)] == [CLIPPED]


def test_editable_and_empty_combos_are_skipped(window):
    editable = _narrow(QComboBox(), 40)
    editable.setEditable(True)
    editable.addItem(LONG)
    assert _show(window(editable, _narrow(QComboBox(), 40))) == []


def test_elided_tabs_are_reported_per_tab(window):
    tabs = QTabWidget()
    tabs.tabBar().setObjectName("tabs")
    tabs.tabBar().setElideMode(tabs.tabBar().elideMode().ElideRight)
    tabs.tabBar().setUsesScrollButtons(False)
    for title in ("General settings", "Advanced settings", "Hidden settings"):
        tabs.addTab(QWidget(), title)
    tabs.tabBar().setTabVisible(2, False)
    findings = _show(window(tabs), width=120)
    assert [f.path for f in findings] == ["Demo › tabs › tab 1", "Demo › tabs › tab 2"]


def test_tabs_behind_scroll_buttons_are_reported_for_the_bar(window):
    bar = _named(QTabBar(), "bar")
    bar.setElideMode(bar.elideMode().ElideNone)
    bar.setUsesScrollButtons(True)
    for title in ("General settings", "Advanced settings", "Developer settings"):
        bar.addTab(title)
    [finding] = _show(window(bar), width=120)
    assert (finding.path, finding.kind) == ("Demo › bar", CLIPPED)


def test_vertical_tabs_are_measured_by_height(window):
    bar = _named(QTabBar(), "side")
    bar.setShape(QTabBar.Shape.RoundedWest)
    bar.setElideMode(bar.elideMode().ElideRight)
    bar.setUsesScrollButtons(False)
    for title in ("General settings", "Advanced settings"):
        bar.addTab(title)
    bar.setFixedHeight(60)
    findings = _show(window(bar))
    assert findings
    assert {f.kind for f in findings} == {CLIPPED}
    assert all(f.path.startswith("Demo › side") for f in findings)


def test_a_group_title_too_wide(window):
    group = _named(_narrow(QGroupBox(LONG), 60), "group")
    [finding] = _show(window(group, QGroupBox()))
    assert (finding.path, finding.kind) == ("Demo › group", CLIPPED)
    assert finding.missing >= group.fontMetrics().horizontalAdvance(LONG) - 60


def test_a_wrapped_label_cut_in_height(window):
    label = _named(QLabel(" ".join([LONG] * 4)), "intro")
    label.setWordWrap(True)
    label.setFixedSize(120, 20)
    [finding] = _show(window(label))
    assert (finding.path, finding.kind) == ("Demo › intro", CUT_OFF)
    assert finding.missing == label.heightForWidth(120) - 20


def test_a_scroll_area_that_scrolls_at_the_default_size(window):
    content = QWidget()
    content.setMinimumSize(600, 800)
    area = _named(QScrollArea(), "page")
    area.setWidget(content)
    findings = _show(window(area), width=300, height=200)
    assert [(f.path, f.kind) for f in findings] == [
        ("Demo › page › vertical", SCROLLBAR),
        ("Demo › page › horizontal", SCROLLBAR),
    ]
    assert findings[0].missing == area.verticalScrollBar().maximum()


def test_a_scroll_area_meant_to_scroll_is_left_alone(window):
    content = QWidget()
    content.setMinimumSize(600, 800)
    area = QScrollArea()
    area.setWidget(content)
    area.setProperty("refrainScrolls", True)
    assert _show(window(area), width=300, height=200) == []


def test_overlapping_siblings(window):
    a = _named(QLabel("One"), "first")
    b = _named(QLabel("Two"), "second")
    a.setGeometry(10, 10, 80, 30)
    b.setGeometry(50, 20, 80, 30)
    [finding] = _show(window(a, b, layout=False))
    assert finding == LayoutFinding("Demo › first", OVERLAP, 20, "Demo › second")


def test_touching_siblings_and_focus_frames_do_not_overlap(window):
    a = QLabel("One")
    b = QLabel("Two")
    a.setGeometry(10, 10, 80, 30)
    b.setGeometry(88, 10, 80, 30)
    root = window(a, b, layout=False)
    frame = QFocusFrame(root)
    frame.setWidget(a)
    assert _show(root) == []


def test_eliding_widgets_are_skipped_with_their_children(window):
    label = _narrow(QLabel(LONG), 40)
    label.setProperty("refrainElides", True)
    row = QWidget()
    row.setProperty("refrainElides", True)
    inner = QVBoxLayout(row)
    inner.addWidget(_narrow(QLabel(LONG), 40))
    inner.addWidget(_narrow(QPushButton(LONG), 40))
    assert _show(window(label, row)) == []


def test_text_clipped_by_a_narrow_parent(window):
    holder = _named(QWidget(), "holder")
    holder.setFixedSize(60, 30)
    label = _named(QLabel("Short"), "name")
    label.setParent(holder)
    label.setGeometry(0, 0, label.sizeHint().width(), 20)
    label.move(40, 0)
    [finding] = _show(window(holder, layout=False))
    assert (finding.path, finding.kind) == ("Demo › holder › name", CLIPPED)
    assert finding.missing == label.width() - 20


def test_scroll_content_is_not_clipped_by_the_viewport(window):
    content = QWidget()
    content.setFixedSize(400, 50)
    QVBoxLayout(content).addWidget(QLabel(LONG))
    area = QScrollArea()
    area.setWidget(content)
    area.setProperty("refrainScrolls", True)
    assert _show(window(area), width=150, height=200) == []


def test_paths_use_tab_titles_names_and_indexes(window):
    tabs = QTabWidget()
    page = QWidget()
    form = QVBoxLayout(page)
    form.addWidget(_named(_narrow(QLabel(LONG)), "client_id_label"))
    inner = _named(QWidget(), "qt_internal")
    QVBoxLayout(inner).addWidget(QLabel("x"))
    form.addWidget(inner)
    form.addWidget(QLabel("Fits"))
    form.addWidget(_narrow(QLabel(LONG)))
    tabs.addTab(page, "Gen&eral && more")
    root = window(tabs, title="")
    root.setObjectName("settings")
    paths = [f.path for f in _show(root)]
    assert paths == [
        "settings › General & more › client_id_label",
        "settings › General & more › QLabel[3]",
    ]
    root.setObjectName("")
    assert check_layout(root)[0].path.startswith("QWidget › ")


def test_hidden_widgets_pixmaps_and_child_windows_are_skipped(window):
    hidden = _narrow(QLabel(LONG))
    picture = _narrow(QLabel())
    picture.setPixmap(QPixmap(80, 20))
    root = window(hidden, picture, _narrow(QLabel()), _narrow(QPushButton()))
    hidden.hide()
    dialog = QDialog(root)
    QVBoxLayout(dialog).addWidget(_narrow(QLabel(LONG)))
    dialog.show()
    assert _show(root) == []
    dialog.close()


def test_findings_are_logged_without_text(caplog):
    findings = [
        LayoutFinding("Settings › General › client_id_input", CLIPPED, 105),
        LayoutFinding("Demo › first", OVERLAP, 20, "Demo › second"),
    ]
    with caplog.at_level(logging.WARNING, logger="refrain.ui.layout"):
        log_findings(findings)
    assert [r.getMessage() for r in caplog.records] == [
        "UI: Settings › General › client_id_input needs 105 px more (clipped)",
        "UI: Demo › first needs 20 px more (overlaps Demo › second)",
    ]
    assert all(r.levelno == logging.WARNING for r in caplog.records)
