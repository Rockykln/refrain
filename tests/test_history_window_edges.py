"""Edge cases of the recently-played window: rendering, hover, menus, and shortcuts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QContextMenuEvent,
    QGuiApplication,
    QHelpEvent,
    QIcon,
    QPixmap,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QMenu  # noqa: E402

from refrain.cover_art import image_path_for_url  # noqa: E402
from refrain.history import HistoryEntry, HistorySnapshot  # noqa: E402
from refrain.ui.history_window import _SongRow, highlight_ranges  # noqa: E402
from tests.test_history_window import (  # noqa: E402, F401
    GLASS,
    _at,
    _cover_url,
    _demo,
    _entries,
    _store_cover,
    _window,
    win,
)

# ------------------------------------------------------------------------- #
# Search word highlighting                                                    #
# ------------------------------------------------------------------------- #


def test_an_empty_search_word_adds_no_highlight():
    assert highlight_ranges("Song 10", ["", "song"]) == [(0, 4)]


def test_a_highlight_past_the_elided_text_does_not_break_painting(_window):  # noqa: F811
    from refrain.ui.history_window import _ElidedLabel

    label = _ElidedLabel("Glass Tides Extended Mix")
    label.resize(40, 20)
    label.show()  # a hidden label never gets the resize event that elides it
    QGuiApplication.processEvents()
    label.set_highlights(["mix"])
    assert label.text() != label._full  # confirms the title is actually elided
    pixmap = QPixmap(label.size())
    label.render(pixmap)  # must not raise even though "mix" is cut off
    assert not pixmap.isNull()


# ------------------------------------------------------------------------- #
# Row: theme icon, tooltip, hover                                             #
# ------------------------------------------------------------------------- #


def test_a_matching_theme_icon_is_shown_next_to_the_source(win, monkeypatch):  # noqa: F811
    real_icon = QIcon(QPixmap(14, 14))

    def _fake_from_theme(name):
        return real_icon if name == "internet-web-browser" else QIcon()

    monkeypatch.setattr(QIcon, "fromTheme", staticmethod(_fake_from_theme))
    win.set_snapshot(HistorySnapshot(entries=_entries(1)))
    row = win.findChildren(_SongRow)[0]
    icon_labels = [lbl for lbl in row.findChildren(QLabel) if lbl.width() == 14]
    assert icon_labels
    assert not icon_labels[0].pixmap().isNull()


def test_hovering_a_row_refreshes_its_tooltip(win):  # noqa: F811
    entry = HistoryEntry(
        title="Glass Tides", artist="Neon Harbor", started_at=_at(QDate.currentDate())
    )
    win.set_snapshot(HistorySnapshot(entries=(entry,)))
    row = win.findChildren(_SongRow)[0]
    row.setToolTip("stale")
    row.event(QHelpEvent(QEvent.Type.ToolTip, QPoint(5, 5), row.mapToGlobal(QPoint(5, 5))))
    assert "Glass Tides" in row.toolTip()
    assert row.toolTip() != "stale"


def test_the_hover_highlight_turns_off_when_the_mouse_leaves_a_row(win):  # noqa: F811
    win.set_snapshot(HistorySnapshot(entries=_entries(1)))
    row = win.findChildren(_SongRow)[0]
    row._hover = True
    row.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not row._hover


def test_a_right_click_on_a_row_does_not_open_it(win, monkeypatch):  # noqa: F811
    opened = []
    monkeypatch.setattr(
        "refrain.ui.history_window.confirm_and_open",
        lambda parent, url, player="": opened.append(url),
    )
    win.set_snapshot(HistorySnapshot(entries=_entries(1)))
    row = win.findChildren(_SongRow)[0]
    QTest.mouseClick(row, Qt.MouseButton.RightButton)
    assert opened == []


# ------------------------------------------------------------------------- #
# Row context menu                                                            #
# ------------------------------------------------------------------------- #


def _fire_context_menu(row, monkeypatch, action_text) -> None:
    # PySide resolves QMenu.exec in C++, so the menu class itself is replaced.
    class _Menu(QMenu):
        def exec(self, *_args):
            return next(a for a in self.actions() if a.text() == action_text)

    monkeypatch.setattr("refrain.ui.history_window.QMenu", _Menu)
    point = QPoint(5, 5)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, point)
    row.contextMenuEvent(event)


def test_context_menu_open_opens_the_song_in_the_browser_that_played_it(win, monkeypatch):  # noqa: F811
    opened = []
    monkeypatch.setattr(
        "refrain.ui.history_window.confirm_and_open",
        lambda parent, url, player="": opened.append((url, player)),
    )
    entry = HistoryEntry(
        title="Glass Tides",
        artist="Neon Harbor",
        source="mpris",
        player="Chromium",
        url="https://music.apple.com/de/song/x/2",
    )
    win.set_snapshot(HistorySnapshot(entries=(entry,)))
    row = win.findChildren(_SongRow)[0]
    _fire_context_menu(row, monkeypatch, "Open in Apple Music")
    assert opened == [("https://music.apple.com/de/song/x/2", "Chromium")]


def test_context_menu_copy_puts_artist_and_title_on_the_clipboard(win, monkeypatch):  # noqa: F811
    entry = HistoryEntry(title="Glass Tides", artist="Neon Harbor")
    win.set_snapshot(HistorySnapshot(entries=(entry,)))
    row = win.findChildren(_SongRow)[0]
    _fire_context_menu(row, monkeypatch, "Copy artist and title")
    assert QGuiApplication.clipboard().text() == "Neon Harbor — Glass Tides"


def test_context_menu_remove_asks_the_window_to_remove_the_song(win, monkeypatch):  # noqa: F811
    removed = []
    win.removeRequested.connect(removed.append)
    entry = HistoryEntry(title="Glass Tides", artist="Neon Harbor")
    win.set_snapshot(HistorySnapshot(entries=(entry,)))
    row = win.findChildren(_SongRow)[0]
    _fire_context_menu(row, monkeypatch, "Remove from history")
    assert [e.title for e in removed] == ["Glass Tides"]


# ------------------------------------------------------------------------- #
# Window: shortcuts, palette changes, covers                                  #
# ------------------------------------------------------------------------- #


def test_escape_with_an_empty_search_closes_the_window(win):  # noqa: F811
    win.search.clear()
    assert win.isVisible()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isVisible()


def test_ctrl_f_focuses_and_selects_the_search_field(win):  # noqa: F811
    win.set_snapshot(HistorySnapshot(entries=_entries(12)))
    win.search.setText("kept")
    win.show()
    win._focus_search()
    # Offscreen windows never become active, so ask the window, not the widget.
    assert win.focusWidget() is win.search
    assert win.search.selectedText() == "kept"


def test_a_palette_change_forgets_cached_covers_and_rebuilds_the_rows(win):  # noqa: F811
    win.set_snapshot(HistorySnapshot(entries=_entries(3)))
    win._covers[("https://stale.example/cover.jpg", 1.0)] = QPixmap()
    win.changeEvent(QEvent(QEvent.Type.PaletteChange))
    assert ("https://stale.example/cover.jpg", 1.0) not in win._covers
    assert len(win.findChildren(_SongRow)) == 3


def test_a_cover_file_that_cannot_be_read_falls_back_to_the_placeholder(win, monkeypatch):  # noqa: F811
    _store_cover(_cover_url(GLASS[0]))
    target = image_path_for_url(_cover_url(GLASS[0]))
    original_stat = Path.stat

    def _guarded_stat(self, *a, **k):
        if self == target:
            raise OSError("permission denied")
        return original_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _guarded_stat)
    win.set_snapshot(HistorySnapshot(entries=_demo(GLASS, cover=True)))
    row = win.findChildren(_SongRow)[0]
    assert not row.has_cover


def test_a_corrupted_cover_file_falls_back_to_the_placeholder(win):  # noqa: F811
    path = image_path_for_url(_cover_url(GLASS[0]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not actually an image")
    win.set_snapshot(HistorySnapshot(entries=_demo(GLASS, cover=True)))
    row = win.findChildren(_SongRow)[0]
    assert not row.has_cover
