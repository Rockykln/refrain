"""Times and numbers follow the desktop, even where its digits are not ours."""

from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, QLocale  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.history import HistoryEntry, HistorySnapshot  # noqa: E402
from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.ui.history_window import HistoryWindow  # noqa: E402
from refrain.ui.layout_check import check_layout  # noqa: E402
from refrain.ui.status_window import StatusWindow  # noqa: E402

# Arabic-Indic digits, Devanagari digits, Persian digits, and a 24-hour clock.
LOCALES = ["ar_EG", "hi_IN", "fa_IR", "de_DE", "ja_JP"]
TOLERANCE_PX = 4


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _songs():
    now = int(time.time())
    return (
        HistoryEntry("Glass Tides", "Neon Harbor", "Low Light", "mpris", "Chromium", now - 60),
        HistoryEntry("Salt Flats", "Wren & Ash", "", "mpris", "Firefox", now - 3600),
        HistoryEntry("Late Train Home", "Velvet Static", "", "bluetooth", "Phone", now - 7200),
    )


@pytest.mark.parametrize("name", LOCALES)
def test_the_status_window_renders_in_any_locale(qapp, name):
    locale = QLocale(name)
    window = StatusWindow(locale)
    window.set_track(
        TrackInfo(
            source="mpris",
            title="Glass Tides",
            artist="Neon Harbor",
            album="Low Light",
            duration_ms=214_000,
            position_ms=45_000,
            status=PlaybackStatus.PLAYING,
            player="Chromium",
        )
    )
    window.set_history(HistorySnapshot(entries=_songs(), now_playing=True))
    window.set_status(StatusSnapshot(DiscordStatus.SHOWING, "", LastfmStatus.SCROBBLING, "demo"))
    window.set_progress(45_000, 214_000)
    window.show()
    qapp.processEvents()
    assert [f.message() for f in check_layout(window) if f.missing > TOLERANCE_PX] == []
    # The clock is the desktop's, down to its digits.
    rows = window.recent_rows
    assert rows.count() == 2  # the song playing now is the header, not a row
    row = rows.itemAt(0).widget()
    started = QDateTime.fromSecsSinceEpoch(row.entry.started_at)
    assert row.when.text() == locale.toString(started.time(), QLocale.FormatType.ShortFormat)
    window.hide()


@pytest.mark.parametrize("name", LOCALES)
def test_the_history_window_renders_in_any_locale(qapp, name, xdg_tmp):
    window = HistoryWindow(QLocale(name))
    window.set_snapshot(HistorySnapshot(entries=_songs(), now_playing=True))
    window.show()
    qapp.processEvents()
    assert [f.message() for f in check_layout(window) if f.missing > TOLERANCE_PX] == []
    window.hide()
