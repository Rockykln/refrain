"""The Status window for every state it can be in."""

from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent, QFont, QImage, QMouseEvent, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from refrain.cover_art import image_path_for_url  # noqa: E402
from refrain.history import HistoryEntry, HistorySnapshot  # noqa: E402
from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.ui import status_window as sw  # noqa: E402
from refrain.ui.status_window import StatusWindow  # noqa: E402

COVER = "https://is1-ssl.mzstatic.example/image/glass-tides.jpg"
NOW = int(time.time())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(app):
    w = StatusWindow()
    yield w
    w.hide()
    w.deleteLater()


def _playing(**kw) -> TrackInfo:
    base = {
        "source": "mpris",
        "title": "Glass Tides",
        "artist": "Neon Harbor",
        "album": "Low Light",
        "status": PlaybackStatus.PLAYING,
        "player": "Chromium",
    }
    return TrackInfo(**(base | kw))


def _history(now_playing=True, enabled=True, n=4, cover="") -> HistorySnapshot:
    songs = [
        HistoryEntry("Glass Tides", "Neon Harbor", "Low Light", "mpris", "Chromium", NOW, 0, cover),
        HistoryEntry("Paper Satellites", "Marlow Vance", "", "mpris", "Chromium", NOW - 300),
        HistoryEntry("Northbound", "The Quiet Hours", "", "bluetooth", "Phone", NOW - 600),
        HistoryEntry("Silk Road Radio", "Ilse Moreau", "", "mpris", "Brave", NOW - 900),
        HistoryEntry("Ferrous", "Oskar Lind", "", "mpris", "Zen", NOW - 1200, scrobbled=True),
    ][:n]
    return HistorySnapshot(entries=tuple(songs), now_playing=now_playing, enabled=enabled)


def test_nothing_playing_says_how_to_start(window):
    assert window.title.text() == "Nothing playing"
    assert "Apple Music" in window.hint.text()
    assert not window.hint.isHidden()
    assert window.artist.isHidden()
    assert not window.cover.pixmap().isNull()


def test_a_browser_playing_with_no_url_gets_a_troubleshooting_hint(window):
    window.set_track(TrackInfo(source="none", player="Vivaldi"))
    assert window.title.text() == "Nothing playing"
    assert not window.hint.isHidden()
    assert "Vivaldi" in window.hint.text()
    assert "Plasma" in window.hint.text()

    window.set_track(TrackInfo.empty())
    assert "Vivaldi" not in window.hint.text()
    assert "Apple Music" in window.hint.text()


def test_the_current_song_with_its_source(window):
    window.set_track(_playing())
    assert window.title.text() == "Glass Tides"
    assert window.artist.text() == "Neon Harbor — Low Light"
    assert window.source.text() == "Apple Music Web · Chromium"
    assert window.hint.isHidden()
    window.set_playback(PlaybackStatus.PAUSED)
    assert window.source.text() == "Paused · Apple Music Web · Chromium"
    window.set_playback(PlaybackStatus.PAUSED)
    window.set_track(_playing(source="bluetooth", player="", artist="", album=""))
    assert window.source.text() == "Bluetooth"
    assert window.artist.isHidden()


def test_the_cover_comes_from_the_history(window):
    path = image_path_for_url(COVER)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(120, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#3a6ea5"))
    image.save(str(path), "PNG")
    window.set_track(_playing())
    window.set_history(_history(cover=COVER))
    assert window._cover_url == COVER
    window.set_history(_history(cover=COVER))
    assert window._cover_url == COVER
    path.write_bytes(b"")
    window._cover_url = ""
    window.set_history(_history(cover=COVER))
    assert window._cover_url == ""


def test_a_broken_cover_file_falls_back_to_the_placeholder(window):
    path = image_path_for_url(COVER + "?broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not an image")
    window.set_track(_playing())
    window.set_history(_history(cover=COVER + "?broken"))
    assert window._cover_url == ""


DISCORD = [
    (DiscordStatus.STARTING, "Checking…", "", "off"),
    (DiscordStatus.NOT_SET_UP, "Not set up yet", "Set up…", "warn"),
    (DiscordStatus.NO_CLIENT, "The Discord app isn't running", "", "warn"),
    (DiscordStatus.REJECTED, "Application ID rejected", "Fix…", "bad"),
    (
        DiscordStatus.NOT_LOGGED_IN,
        "Discord is open but not logged in. Log in to Discord to show your status.",
        "",
        "warn",
    ),
    (DiscordStatus.ERROR, "Discord isn't answering right now", "", "warn"),
    (DiscordStatus.READY, "Ready — waiting for music", "", "ok"),
    (DiscordStatus.SHOWING, "Visible on your profile", "", "ok"),
    (DiscordStatus.SHOWING_MINIMAL, "Showing “Listening to music”", "", "ok"),
    (DiscordStatus.PAUSED, "Hidden while the music is paused", "", "off"),
    (DiscordStatus.PRIVACY_OFF, "Hidden — sharing is off", "", "off"),
]


@pytest.mark.parametrize(("state", "text", "button", "tone"), DISCORD)
def test_discord_line_and_its_one_action(window, state, text, button, tone):
    window.set_status(StatusSnapshot(state, "Invalid Client ID"))
    row = window.discord
    assert row.text.text() == text
    assert row.tone == tone
    assert row.button.isHidden() is (not button)
    assert row.button.text() == button
    assert row.text.toolTip() == "Invalid Client ID"


LASTFM = [
    (LastfmStatus.OFF, "", "Off", "Set up…", "off"),
    (
        LastfmStatus.CONNECTED_OFF,
        "",
        "Connected, but scrobbling is off",
        "Open Last.fm settings…",
        "off",
    ),
    (LastfmStatus.NOT_CONNECTED, "", "Not connected yet", "Set up…", "warn"),
    (LastfmStatus.SCROBBLING, "refrain_demo", "Scrobbling as refrain_demo", "", "ok"),
    (LastfmStatus.SCROBBLING, "", "Scrobbling", "", "ok"),
    (LastfmStatus.WAITING, "2", "2 scrobble(s) waiting to be sent", "", "warn"),
    (LastfmStatus.WAITING, "x", "0 scrobble(s) waiting to be sent", "", "warn"),
    (LastfmStatus.EXPIRED, "", "Sign-in expired", "Reconnect…", "bad"),
    (LastfmStatus.PAUSED, "", "Paused — sharing is off", "", "off"),
]


@pytest.mark.parametrize(("state", "detail", "text", "button", "tone"), LASTFM)
def test_lastfm_line_and_its_one_action(window, state, detail, text, button, tone):
    window.set_status(StatusSnapshot(DiscordStatus.READY, "", state, detail))
    row = window.lastfm
    assert row.text.text() == text
    assert row.tone == tone
    assert row.button.isHidden() is (not button)


def test_action_buttons_ask_for_the_right_settings_page(window):
    asked = []
    window.settingsRequested.connect(asked.append)
    window.set_status(StatusSnapshot(DiscordStatus.REJECTED, "", LastfmStatus.EXPIRED))
    window.discord.button.click()
    window.lastfm.button.click()
    window.settings_btn.click()
    assert asked == ["discord", "lastfm", ""]


def test_colours_follow_a_dark_palette(window):
    palette = QPalette(window.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor("#202326"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#fcfcfc"))
    window.setPalette(palette)
    window.set_status(StatusSnapshot(DiscordStatus.SHOWING))
    assert window.discord.dot.color.name() == sw._TONES["ok"][1]
    window.setPalette(QApplication.palette())
    window.set_status(StatusSnapshot(DiscordStatus.REJECTED))
    assert window.discord.dot.color.name() == sw._TONES["bad"][0]


def test_recently_played_leaves_out_the_song_on_now(window):
    window.set_history(_history(n=5))
    rows = [window.recent_rows.itemAt(i).widget() for i in range(window.recent_rows.count())]
    assert [r.entry.title for r in rows] == [
        "Paper Satellites",
        "Northbound",
        "Silk Road Radio",
        "Ferrous",
    ]  # five entries, one of them playing now
    assert window.recent_empty.isHidden()
    assert not window.show_all.isHidden()
    window.set_history(_history(now_playing=False, n=5))
    rows = [window.recent_rows.itemAt(i).widget() for i in range(window.recent_rows.count())]
    assert rows[0].entry.title == "Glass Tides"


def test_empty_history_invites_and_a_disabled_one_says_so(window):
    window.set_history(HistorySnapshot())
    assert not window.recent_empty.isHidden()
    assert window.show_all.isHidden()
    assert window.recent_off.isHidden()
    window.set_history(_history(enabled=False))
    assert not window.recent_box.isHidden()
    assert "off" in window.recent_empty.text()
    assert not window.recent_off.isHidden()


def test_a_recent_song_opens_in_its_browser_and_show_all_opens_the_history(window, monkeypatch):
    opened, asked = [], []
    monkeypatch.setattr(
        sw, "confirm_and_open", lambda parent, url, player: opened.append((url, player))
    )
    window.historyRequested.connect(lambda: asked.append(True))
    window.set_history(_history(n=5))
    rows = [window.recent_rows.itemAt(i).widget() for i in range(window.recent_rows.count())]
    for row, button in ((rows[0], Qt.MouseButton.LeftButton), (rows[1], Qt.MouseButton.LeftButton)):
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(5, 5),
            QPointF(5, 5),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )
        row.mouseReleaseEvent(event)
    right = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    rows[0].mouseReleaseEvent(right)
    window.show_all.click()
    assert opened[0][0].startswith("https://music.apple.com/search?term=")
    assert opened[0][1] == "Chromium"
    assert opened[1][1] == ""
    assert asked == [True]


def test_scrobbled_songs_carry_a_tick(window):
    window.set_history(_history(now_playing=False, n=5))
    window.set_history(
        HistorySnapshot(entries=(HistoryEntry("Ferrous", "Oskar Lind", scrobbled=True),))
    )
    row = window.recent_rows.itemAt(0).widget()
    assert any(child.text() == "✓" for child in row.findChildren(type(window.version)))


def test_pause_sharing_is_labelled_for_what_it_does(window):
    asked = []
    window.sharingToggled.connect(asked.append)
    assert window.sharing_btn.text() == "Pause sharing"
    assert "stop scrobbling" in window.sharing_btn.toolTip()
    window.sharing_btn.click()
    window.set_sharing_paused(True)
    assert window.sharing_btn.text() == "Resume sharing"
    window.sharing_btn.click()
    assert asked == [True, False]


def test_an_update_shows_a_hint_with_its_button(window):
    assert window.update_row.isHidden()
    asked = []
    window.updateRequested.connect(lambda: asked.append(True))
    window.set_update_available("0.6.0")
    assert not window.update_row.isHidden()
    assert window.update_text.text() == "Refrain 0.6.0 is available."
    window.update_btn.click()
    assert asked == [True]


def test_after_the_wizard_it_says_youre_all_set_once(window):
    window.set_status(StatusSnapshot(DiscordStatus.READY))
    window.set_welcome(True)
    assert not window.banner.isHidden()
    assert "You're all set." in window.banner.text()
    window.present()
    window.hide()
    assert window.banner.isHidden()


def test_no_youre_all_set_while_discord_is_not_set_up(window):
    window.set_status(StatusSnapshot(DiscordStatus.NOT_SET_UP))
    window.set_welcome(True)
    assert window.banner.isHidden()


def test_size_and_place_are_remembered(app):
    first = StatusWindow()
    first.resize(460, 520)
    first.show()
    first.hide()
    assert sw.geometry_path().exists()
    second = StatusWindow()
    assert (second.width(), second.height()) == (460, 520)


def test_an_unreadable_geometry_file_is_ignored(app):
    sw.geometry_path().parent.mkdir(parents=True, exist_ok=True)
    sw.geometry_path().write_text("{not json", encoding="utf-8")
    StatusWindow()


def test_a_geometry_that_cannot_be_saved_is_only_logged(window, monkeypatch, caplog, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(sw, "geometry_path", lambda: blocker / "status-window.json")
    import logging

    with caplog.at_level(logging.DEBUG, logger="refrain.ui.status_window"):
        window.show()
        window.hide()
    assert "Could not remember" in caplog.text


def test_a_theme_change_repaints_everything(window):
    window.set_track(_playing())
    window._cover_url = "stale"
    window.setPalette(QPalette(QColor("#303030")))
    assert window._cover_url == ""


def test_the_window_paints_its_status_dots(window):
    window.set_status(StatusSnapshot(DiscordStatus.SHOWING))
    window.resize(420, 440)
    assert not window.grab().isNull()


def test_the_elapsed_time_only_shows_up_with_a_real_position_and_duration(window):
    window.set_track(_playing())
    window.set_progress(65_000, 200_000)
    assert window.elapsed.text() == "1:05 / 3:20"
    assert not window.elapsed.isHidden()
    window.set_progress(0, 0)
    assert window.elapsed.isHidden()
    window.set_progress(-1, 200_000)
    assert window.elapsed.isHidden()


def test_a_cover_pushed_in_live_shows_up_even_without_a_history_entry(window):
    path = image_path_for_url(COVER)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(120, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#3a6ea5"))
    image.save(str(path), "PNG")
    window.set_track(_playing())
    window.set_cover(COVER)
    assert window._cover_url == COVER


def test_a_title_too_wide_for_the_window_scrolls_twice_then_settles_elided(window, monkeypatch):
    """The marquee shows the full title twice before falling back to the elided line."""
    window.show()
    fake_now = [1000.0]
    monkeypatch.setattr(sw.time, "monotonic", lambda: fake_now[0])
    window.set_track(_playing(title="Marlow Vance and the Wandering Radio Orchestra"))
    title = window.title
    title.resize(60, title.height())
    assert title._timer.isActive(), "the full title does not fit at this width"

    title._step()  # still inside the opening pause
    assert title._offset == 0.0

    fake_now[0] = title._hold_until
    title._step()
    assert title._offset > 0
    assert not title.grab().isNull()  # the scrolled paint path runs without error

    for _ in range(5000):
        if not title._timer.isActive():
            break
        if fake_now[0] < title._hold_until:
            fake_now[0] = title._hold_until
        title._step()
    assert not title._timer.isActive(), "scrolling stops on its own after two passes"
    assert title._offset == 0.0
    assert title.text().endswith("…")


def test_scrolling_gives_up_early_if_the_title_stops_overflowing_mid_scroll(window, monkeypatch):
    """A live font-size change can shrink the text without a resize event."""
    window.show()
    fake_now = [1000.0]
    monkeypatch.setattr(sw.time, "monotonic", lambda: fake_now[0])
    window.set_track(_playing(title="Marlow Vance and the Wandering Radio Orchestra"))
    title = window.title
    title.resize(60, title.height())
    fake_now[0] = title._hold_until
    title._step()
    assert title._offset > 0

    tiny = QFont(title.font())
    tiny.setPointSize(1)
    title.setFont(tiny)
    fake_now[0] = title._hold_until
    title._step()
    assert not title._timer.isActive()
    assert title._offset == 0.0


def test_a_crash_report_is_offered_in_the_window_and_opens_once(window, monkeypatch, tmp_path):
    report = tmp_path / "crash.log"
    report.write_text("Fatal Python error: Segmentation fault\n")
    opened = []
    monkeypatch.setattr(
        sw.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile())
    )
    window.set_crash_report(report)
    assert not window.banner.isHidden()
    assert "crash.log" in window.banner.text()
    window._on_banner_link("crash")
    assert opened == [str(report)]
    assert window.banner.isHidden()


def test_the_hover_delay_comes_from_the_config(window):
    sw.set_hover_delay(0)  # off
    window.set_history(_history(n=3))
    row = window.recent_rows.itemAt(0).widget()
    row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert row._hover.isActive() is False
    sw.set_hover_delay(2500)
    window.set_history(_history(n=3))
    row = window.recent_rows.itemAt(0).widget()
    row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert row._hover.isActive() is True and row._hover.interval() == 2500
    sw.set_hover_delay(1500)


def test_a_recent_row_only_scrolls_after_a_moment_of_hovering(window):
    window.set_history(_history(n=3))
    row = window.recent_rows.itemAt(0).widget()
    assert row._hover.isActive() is False
    row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert row._hover.isActive() is True
    assert row._hover.interval() == sw._hover_ms
    row.leaveEvent(QEvent(QEvent.Type.Leave))
    assert row._hover.isActive() is False


def test_a_recent_song_is_underlined_only_while_the_mouse_is_on_it(window):
    window.set_history(_history(n=3))
    row, other = (window.recent_rows.itemAt(i).widget() for i in range(2))
    assert row.title.font().underline() is False
    row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert row.title.font().underline() is True
    assert row.when.font().underline() is False
    assert other.title.font().underline() is False
    row.leaveEvent(QEvent(QEvent.Type.Leave))
    assert row.title.font().underline() is False


def test_a_recent_song_is_underlined_even_with_hover_scrolling_off(window):
    sw.set_hover_delay(0)
    try:
        window.set_history(_history(n=3))
        row = window.recent_rows.itemAt(0).widget()
        row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        assert row.title.font().underline() is True
    finally:
        sw.set_hover_delay(1500)


def test_the_playing_title_scrolls_once_when_the_mouse_arrives(window):
    window.title.setText("A title far too long for any window this size to show at once")
    window.title.resize(60, 20)
    window.title.show()
    window.title.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert window.title._passes == 1


def test_no_button_opens_with_the_focus_but_tab_still_reaches_them(window, app):
    window.present()
    for _ in range(3):
        app.processEvents()
    assert window.focusWidget() is window
    window.focusNextChild()
    assert isinstance(window.focusWidget(), QPushButton)


def test_a_window_with_no_room_left_shows_no_songs(window):
    window.set_history(_history(n=5))
    window.recent_box.resize(window.recent_box.width(), 10)  # nothing left over
    assert window._fits() == 0


def test_songs_keep_their_full_height_once_a_track_shows_up(window, app):
    """The cover and controls push the list down; it must drop a row, not squeeze them all."""
    many = HistorySnapshot(
        entries=tuple(
            HistoryEntry(f"Song {i}", "Marlow Vance", "", "mpris", "Chromium", NOW - 60 * i)
            for i in range(12)
        ),
        now_playing=False,
        enabled=True,
    )
    window.resize(520, 520)
    window.show()
    window.set_history(many)
    for _ in range(3):
        app.processEvents()
    window.set_track(_playing())
    for _ in range(3):
        app.processEvents()
    rows = [window.recent_rows.itemAt(i).widget() for i in range(window.recent_rows.count())]
    assert rows
    assert [r.height() for r in rows] == [r.sizeHint().height() for r in rows]


def test_a_short_recent_list_stays_together_at_the_top(window, app):
    """Two songs in a tall window sit one under the other, not spread over the height."""
    window.resize(520, 900)
    window.show()
    window.set_history(_history(n=3))
    for _ in range(3):  # new rows start at Qt's default size until the layout runs
        app.processEvents()
    rows = window.recent_rows
    assert rows.count() == 2
    for i in range(rows.count()):
        row = rows.itemAt(i).widget()
        assert row.height() <= row.sizeHint().height() + 2
