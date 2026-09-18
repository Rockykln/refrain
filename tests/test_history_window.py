"""The recently-played window shows what a snapshot says, and stays tidy."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QDateTime, QLocale, Qt, QTime, QTranslator  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

import refrain  # noqa: E402
from refrain.history import HistoryEntry, HistorySnapshot  # noqa: E402
from refrain.ui.history_window import (  # noqa: E402
    HistoryWindow,
    _ElidedLabel,
    _SongRow,
    highlight_ranges,
    song_link,
)


@pytest.fixture(scope="module")
def _window():
    # One window for the module, as in test_settings_lastfm; each test
    # starts it from an empty snapshot.
    app = QApplication.instance() or QApplication(sys.argv)
    # English plurals ("Last song" / "Last 12 songs") come from the
    # shipped refrain_en.qm, exactly as in the app.
    english = QTranslator(app)
    assert english.load(str(Path(refrain.__file__).parent / "i18n" / "refrain_en.qm"))
    app.installTranslator(english)
    w = HistoryWindow(QLocale("en_US"))
    try:
        yield w
    finally:
        w.close()
        w.deleteLater()
        app.removeTranslator(english)
        app.processEvents()


@pytest.fixture
def win(_window, xdg_tmp):
    # Shown (offscreen): a hidden window only stores snapshots and
    # rebuilds when it opens.
    _window.show()
    _window.set_snapshot(HistorySnapshot())
    _window.reset_filters()
    return _window


def test_a_closed_window_rebuilds_only_when_it_opens(win):
    win.hide()
    win.set_snapshot(HistorySnapshot(entries=_entries(3)))
    assert win.findChildren(_SongRow) == []
    win.show()
    assert len(win.findChildren(_SongRow)) == 3


def _at(day: QDate) -> int:
    return QDateTime(day, QTime(12, 0)).toSecsSinceEpoch()


def _entries(n: int, **kw) -> tuple[HistoryEntry, ...]:
    base = _at(QDate.currentDate())
    return tuple(
        HistoryEntry(
            title=kw.get("title", f"Song {i}"),
            artist="Artist",
            album="Album",
            source="mpris",
            player="Chromium",
            started_at=base - i * 240,
            duration_ms=200_000,
        )
        for i in range(n)
    )


def _texts(w) -> list[str]:
    return [label.text() for label in w.findChildren(QLabel)]


def test_empty_state(win):
    assert win._stack.currentIndex() == 1
    assert "No songs yet" in _texts(win)
    assert not win.clear_btn.isEnabled()


def test_rows_and_count(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(12), limit=30))
    assert win._stack.currentIndex() == 0
    assert len(win.findChildren(_SongRow)) == 12
    assert win.count_label.text() == "Last 12 songs"
    assert win.clear_btn.isEnabled()


def test_one_song_is_singular_and_none_hides_the_count(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(1)))
    assert win.count_label.text() == "Last song"
    win.set_snapshot(HistorySnapshot())
    assert win.count_label.isHidden()


def test_now_playing_and_paused(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(3), now_playing=True, playing=True))
    assert _texts(win).count("Now playing") == 1
    win.set_snapshot(HistorySnapshot(entries=_entries(3), now_playing=True, playing=False))
    assert "Paused" in _texts(win)
    assert "Now playing" not in _texts(win)


def test_turned_off(win):
    win.set_snapshot(HistorySnapshot(enabled=False))
    assert "History is turned off" in _texts(win)
    assert win.count_label.isHidden()
    assert not win.clear_btn.isEnabled()


def test_days_get_their_own_headers(win):
    today = QDate.currentDate()
    old = QDate(2025, 1, 1)
    entries = (
        HistoryEntry(title="A", started_at=_at(today)),
        HistoryEntry(title="B", started_at=_at(today.addDays(-1))),
        HistoryEntry(title="C", started_at=_at(old)),
    )
    win.set_snapshot(HistorySnapshot(entries=entries))
    texts = _texts(win)
    assert "Today" in texts
    assert "Yesterday" in texts
    assert QLocale("en_US").toString(old, QLocale.FormatType.LongFormat) in texts


def test_a_long_title_elides_instead_of_widening_the_window(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(1, title="Very long title " * 40)))
    assert win.minimumSizeHint().width() < 700


def test_rows_carry_the_details_in_their_tooltip(win):
    entry = HistoryEntry(
        title="Title",
        artist="Artist",
        album="Album",
        source="bluetooth",
        player="Desk Speaker",
        started_at=_at(QDate.currentDate()),
        duration_ms=225_000,
        scrobbled=True,
    )
    win.set_snapshot(HistorySnapshot(entries=(entry,)))
    tip = win.findChildren(_SongRow)[0].toolTip()
    started = QDateTime.fromSecsSinceEpoch(entry.started_at)
    for part in (
        "Title",
        "Artist · Album",  # one line, not two
        # The zone abbreviated once, after the time — not spelled out.
        f"{QLocale('en_US').toString(started.time(), QLocale.FormatType.ShortFormat)} "
        f"{started.timeZoneAbbreviation()}",
        "Bluetooth · Desk Speaker",
        "Length: 3:45",
        "Scrobbled to Last.fm",
    ):
        assert part in tip


@pytest.mark.parametrize(
    "seconds,text",
    [
        (20, "just now"),
        (60, "1 minute ago"),
        (125, "2 minutes ago"),
        (3600, "1 hour ago"),
        (5 * 3600 + 59 * 60, "5 hours ago"),
        (24 * 3600, "1 day ago"),
        (3 * 24 * 3600 + 5, "3 days ago"),
    ],
)
def test_relative_time(win, seconds, text):
    assert win.relative_time(seconds) == text


def test_remove_from_the_context_menu(win, monkeypatch):
    removed = []
    win.removeRequested.connect(removed.append)
    win.set_snapshot(HistorySnapshot(entries=_entries(2)))
    row = win.findChildren(_SongRow)[1]
    row._on_remove()
    assert [e.title for e in removed] == ["Song 1"]


# --------------------------------------------------------------------------- #
# Search and source filter                                                     #
# --------------------------------------------------------------------------- #


def _mixed(n: int) -> tuple[HistoryEntry, ...]:
    base = _at(QDate.currentDate())
    out = []
    for i in range(n):
        bt = i % 3 == 2
        out.append(
            HistoryEntry(
                title=f"Song {i}",
                artist="Ilse Moréau" if i == 4 else "Artist",
                album="Album",
                source="bluetooth" if bt else "mpris",
                player="Desk Speaker" if bt else "Chromium",
                started_at=base - i * 240,
                duration_ms=200_000,
            )
        )
    return tuple(out)


def _titles_shown(w) -> list[str]:
    return [row._entry.title for row in w.findChildren(_SongRow)]


def test_search_appears_from_ten_songs(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(9)))
    assert win.search.isHidden()
    win.set_snapshot(HistorySnapshot(entries=_entries(10)))
    assert not win.search.isHidden()


def test_source_filter_appears_with_a_second_source(win):
    win.set_snapshot(HistorySnapshot(entries=_entries(12)))
    assert win.source_filter.isHidden()
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    assert not win.source_filter.isHidden()
    items = [win.source_filter.itemText(i) for i in range(win.source_filter.count())]
    assert items == ["All sources", "Apple Music Web · Chromium", "Bluetooth · Desk Speaker"]


def test_search_ignores_case_and_accents(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.search.setText("MOREAU song")
    win._rebuild()
    assert _titles_shown(win) == ["Song 4"]
    assert win.count_label.text() == "1 of 12 songs"


def test_filter_by_source(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.source_filter.setCurrentIndex(win.source_filter.findData("Bluetooth · Desk Speaker"))
    win._rebuild()
    assert _titles_shown(win) == ["Song 2", "Song 5", "Song 8", "Song 11"]
    assert win.count_label.text() == "4 of 12 songs"


def test_the_playing_song_is_marked_wherever_it_lands(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12), now_playing=True, playing=True))
    win.search.setText("song 0")  # "Song 0" and "Song 10"
    win._rebuild()
    assert [row._now_playing for row in win.findChildren(_SongRow)] == [True, False]
    win.search.setText("song 1")
    win._rebuild()
    assert not any(row._now_playing for row in win.findChildren(_SongRow))


def test_no_matches(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.search.setText("nothing like this")
    win._rebuild()
    assert win._stack.currentIndex() == 1
    assert "No matches" in _texts(win)
    assert not win.search.isHidden()  # the field stays, to fix the search


@pytest.mark.parametrize(
    "text,words,ranges",
    [
        ("Ilse Moréau", ["moreau"], [(5, 11)]),
        ("Straße", ["strasse"], [(0, 6)]),
        ("Song 10", ["song", "10"], [(0, 4), (5, 7)]),
        ("Talk Talk Talk", ["talk"], [(0, 4), (5, 9), (10, 14)]),
        ("Glass Tides", ["ss ti"], [(3, 8)]),
        ("Nothing", ["xyz"], []),
    ],
)
def test_highlight_ranges(text, words, ranges):
    assert highlight_ranges(text, words) == ranges


def test_found_words_get_a_background(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.search.setText("moréau")
    win._rebuild()
    row = win.findChildren(_SongRow)[0]
    subtitle = next(
        lbl for lbl in row.findChildren(_ElidedLabel) if lbl._full.startswith("Ilse Moréau")
    )
    assert subtitle._highlights == [(5, 11)]
    win.search.clear()
    win._rebuild()
    assert not any(lbl._highlights for lbl in win.findChildren(_ElidedLabel))


def test_the_source_filter_stays_narrow(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    assert win.source_filter.width() < win.search.width()
    assert win.source_filter.view().minimumWidth() >= win.source_filter.view().sizeHintForColumn(0)


def test_the_size_is_reported_on_close_and_restored(_window):
    sized = HistoryWindow(QLocale("en_US"), (720, 540))
    assert (sized.width(), sized.height()) == (720, 540)
    reported = []
    sized.sizeRemembered.connect(lambda w, h: reported.append((w, h)))
    sized.show()
    sized.hide()
    assert reported == [(720, 540)]
    sized.deleteLater()
    # Nonsense from a hand-edit falls back to at least the minimum.
    assert HistoryWindow(QLocale("en_US"), (10, 10)).width() >= 440


def test_escape_clears_the_search_first(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.search.setText("song")
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win.search.text() == ""


def test_filters_reset_when_the_window_closes(win):
    win.set_snapshot(HistorySnapshot(entries=_mixed(12)))
    win.search.setText("song 4")
    win.source_filter.setCurrentIndex(1)
    win.show()
    win.hide()
    assert win.search.text() == ""
    assert win.source_filter.currentIndex() == 0
    assert len(win.findChildren(_SongRow)) == 12


def test_the_same_snapshot_does_not_rebuild(win):
    snap = HistorySnapshot(entries=_entries(2))
    win.set_snapshot(snap)
    rows = win.findChildren(_SongRow)
    win.set_snapshot(HistorySnapshot(entries=_entries(2)))  # equal, not identical
    assert win.findChildren(_SongRow) == rows


def test_clear_asks_first(win, monkeypatch):
    win.set_snapshot(HistorySnapshot(entries=_entries(2)))
    fired = []
    win.clearRequested.connect(lambda: fired.append(True))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    # Cancel: the box closes without anything clicked.
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    win._on_clear_clicked()
    assert fired == []

    def _destructive(self):
        return next(
            b
            for b in self.buttons()
            if self.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole
        )

    monkeypatch.setattr(QMessageBox, "clickedButton", _destructive)
    win._on_clear_clicked()
    assert fired == [True]


# --------------------------------------------------------------------------- #
# Every song is a link                                                         #
# --------------------------------------------------------------------------- #


def test_a_song_page_is_used_as_is():
    entry = HistoryEntry(title="T", url="https://music.apple.com/de/song/t/123")
    assert song_link(entry) == "https://music.apple.com/de/song/t/123"


def test_without_a_page_the_link_is_a_search():
    entry = HistoryEntry(title="Glass Tides?", artist="Neon Harbor & Co")
    assert song_link(entry) == (
        "https://music.apple.com/search?term=Neon%20Harbor%20%26%20Co%20Glass%20Tides%3F"
    )


def test_clicking_a_row_opens_it_in_the_browser_that_played_it(win, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "refrain.ui.history_window.open_url", lambda url, player="": opened.append((url, player))
    )
    entries = (
        HistoryEntry(title="A", source="mpris", player="Chromium", url="https://x/song/a"),
        HistoryEntry(title="B", artist="Art", source="bluetooth", player="Desk Speaker"),
    )
    win.set_snapshot(HistorySnapshot(entries=entries))
    rows = win.findChildren(_SongRow)
    for row in rows:
        QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert opened == [
        ("https://x/song/a", "Chromium"),
        # A Bluetooth device is no browser — the default one gets it.
        ("https://music.apple.com/search?term=Art%20B", ""),
    ]
