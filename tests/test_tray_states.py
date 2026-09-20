"""Tray menu, tooltip and icon states the user sees for each daemon update."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.ui import tray as tray_mod  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def tray(app):
    return TrayIcon()


def _track(**kw):
    base = {"source": "mpris", "title": "Glass Tides", "status": PlaybackStatus.PLAYING}
    base.update(kw)
    return TrackInfo(**base)


class _Hints:
    def __init__(self, value):
        self._value = value

    def colorScheme(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


@pytest.mark.parametrize(
    "value,expected",
    [(Qt.ColorScheme.Dark, "dark"), (Qt.ColorScheme.Light, "light")],
)
def test_color_scheme_follows_the_style_hint(app, monkeypatch, value, expected):
    monkeypatch.setattr(tray_mod.QGuiApplication, "styleHints", lambda: _Hints(value))
    assert tray_mod._detect_color_scheme() == expected


@pytest.mark.parametrize("text,expected", [("#f0f0f0", "dark"), ("#202020", "light")])
def test_color_scheme_falls_back_to_the_text_colour_when_the_hint_fails(
    app, monkeypatch, text, expected
):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
    monkeypatch.setattr(
        tray_mod.QGuiApplication, "styleHints", lambda: _Hints(RuntimeError("no hint"))
    )
    monkeypatch.setattr(tray_mod.QGuiApplication, "palette", lambda: palette)
    assert tray_mod._detect_color_scheme() == expected


def test_light_theme_uses_the_dark_glyphs(tray, monkeypatch):
    monkeypatch.setattr(tray_mod, "_detect_color_scheme", lambda: "light")
    monkeypatch.setattr(tray_mod, "QIcon", lambda path: path)
    icons = tray._build_icons_for_current_theme()
    assert icons[PlaybackStatus.PLAYING].endswith("tray-playing-dark.svg")
    assert icons[PlaybackStatus.STOPPED].endswith("tray-stopped-dark.svg")


def test_theme_change_redraws_the_icon_for_the_current_state(tray, monkeypatch):
    tray.set_status(PlaybackStatus.PLAYING)
    shown = []
    monkeypatch.setattr(tray._tray, "setIcon", shown.append)
    monkeypatch.setattr(tray_mod, "_detect_color_scheme", lambda: "dark")
    tray._on_color_scheme_changed()
    assert shown == [tray._icons[PlaybackStatus.PLAYING]]


def test_left_click_opens_the_status_window_and_middle_click_toggles_playback(tray):
    opened, toggled = [], []
    tray.statusRequested.connect(lambda: opened.append(1))
    tray.playPauseRequested.connect(lambda: toggled.append(1))

    tray._on_activated(QSystemTrayIcon.Trigger)
    tray._on_activated(QSystemTrayIcon.MiddleClick)
    tray._on_activated(QSystemTrayIcon.Context)

    assert opened == [1]
    assert toggled == [1]


def test_menu_entries_emit_their_signals(tray):
    got = []
    tray.nextRequested.connect(lambda: got.append("next"))
    tray.previousRequested.connect(lambda: got.append("previous"))
    tray.updateRequested.connect(lambda: got.append("update"))
    tray._next_action.trigger()
    tray._previous_action.trigger()
    tray._update_action.trigger()
    assert got == ["next", "previous", "update"]


def test_update_entry_without_a_version_and_hidden_again(tray):
    tray.set_update_available(True)
    assert tray._update_action.text() == "Update available"
    assert tray._update_action.isVisible()
    tray.set_update_available(False, "2.0.0")
    assert not tray._update_action.isVisible()
    assert "2.0.0" not in tray._update_action.text()


def test_discord_row_waits_for_the_first_state_instead_of_crying_wolf(tray):
    assert tray._discord_action.text() == "Discord: checking…"
    assert not tray._lastfm_action.isVisible()


def test_stopping_from_idle_applies_at_once(tray):
    tray.set_status(PlaybackStatus.PAUSED)
    assert tray._current_status == PlaybackStatus.PAUSED
    assert not tray._pause_timer.isActive()
    assert tray._play_pause_action.text() == "Play"


@pytest.mark.parametrize(
    ("state", "text"),
    [
        (DiscordStatus.NOT_SET_UP, "Discord: not set up — add your Application ID"),
        (DiscordStatus.NO_CLIENT, "Discord: app isn't running"),
        (DiscordStatus.REJECTED, "Discord: Application ID rejected — check it"),
        (DiscordStatus.ERROR, "Discord: not answering"),
        (DiscordStatus.READY, "Discord: ready — waiting for music"),
        (DiscordStatus.SHOWING, "Discord: visible on your profile"),
        (DiscordStatus.SHOWING_MINIMAL, "Discord: showing “Listening to music”"),
        (DiscordStatus.PAUSED, "Discord: hidden while paused"),
        (DiscordStatus.PRIVACY_OFF, "Discord: hidden — sharing is off"),
        (DiscordStatus.STARTING, "Discord: checking…"),
    ],
)
def test_every_discord_state_has_its_own_line(tray, state, text):
    tray.set_service_status(StatusSnapshot(state))
    assert tray._discord_action.text() == text


@pytest.mark.parametrize(
    ("state", "detail", "text"),
    [
        (LastfmStatus.SCROBBLING, "marlowvance", "Last.fm: scrobbling as marlowvance"),
        (LastfmStatus.SCROBBLING, "", "Last.fm: scrobbling"),
        (LastfmStatus.WAITING, "3", "Last.fm: 3 scrobble(s) waiting"),
        (LastfmStatus.WAITING, "?", "Last.fm: 0 scrobble(s) waiting"),
        (LastfmStatus.EXPIRED, "", "Last.fm: sign-in expired — reconnect"),
        (LastfmStatus.NOT_CONNECTED, "", "Last.fm: not connected"),
        (LastfmStatus.CONNECTED_OFF, "", "Last.fm: scrobbling is off"),
        (LastfmStatus.PAUSED, "", "Last.fm: paused — sharing is off"),
    ],
)
def test_lastfm_row_follows_the_live_state(tray, state, detail, text):
    tray.set_service_status(StatusSnapshot(DiscordStatus.READY, "", state, detail))
    assert tray._lastfm_action.isVisible()
    assert tray._lastfm_action.text() == text


def test_lastfm_row_hides_again_when_it_was_never_set_up(tray):
    tray.set_service_status(StatusSnapshot(lastfm=LastfmStatus.SCROBBLING, lastfm_detail="x"))
    tray.set_service_status(StatusSnapshot(lastfm=LastfmStatus.OFF))
    assert not tray._lastfm_action.isVisible()


def test_info_rows_open_the_status_window(tray):
    opened = []
    tray.statusRequested.connect(lambda: opened.append(1))
    for action in (
        tray._title_action,
        tray._artist_action,
        tray._progress_action,
        tray._discord_action,
        tray._lastfm_action,
    ):
        action.trigger()
    assert opened == [1] * 5


def test_pausing_sharing_is_not_in_the_menu_but_settings_is(tray):
    # A click on the icon opens the Status window, which holds the pause
    # switch; Settings is where people look for it, so it stays here too.
    top = [a.text() for a in tray._menu.actions() if a.text()]
    assert "Pause sharing" not in top
    assert "Resume sharing" not in top
    assert "Settings…" in top
    tray.set_sharing_paused(True)
    assert tray._sharing_paused is True


def test_the_settings_entry_asks_for_the_settings_window(tray):
    asked = []
    tray.settingsRequested.connect(lambda: asked.append(1))
    tray._settings_action.trigger()
    assert asked == [1]


def test_rarely_used_entries_live_under_troubleshooting(tray):
    top = [a.text() for a in tray._menu.actions() if a.text()]
    assert "Live log…" not in top
    assert "Restart Refrain" not in top
    assert "Troubleshooting" in top
    assert [a.text() for a in tray._more_menu.actions()] == ["Live log…", "Restart Refrain"]
    got = []
    tray.logRequested.connect(lambda: got.append("log"))
    tray.restartRequested.connect(lambda: got.append("restart"))
    tray._log_action.trigger()
    tray._restart_action.trigger()
    assert got == ["log", "restart"]


def test_tooltip_carries_track_and_progress(tray):
    tray.set_track(_track(artist="Neon Harbor", album="Low Light"))
    assert tray._tray.toolTip() == "Glass Tides\nNeon Harbor • Low Light"
    tray.set_progress(65_000, 200_000)
    assert tray._tray.toolTip() == "Glass Tides\nNeon Harbor • Low Light\n1:05 / 3:20 (–2:15)"


def test_nothing_playing_resets_menu_and_tooltip(tray):
    tray.set_track(_track(artist="Neon Harbor", album="Low Light"))
    tray.set_progress(65_000, 200_000)
    tray.set_track(TrackInfo.empty())
    assert tray._title_action.text() == "(nothing playing)"
    assert not tray._artist_action.isVisible()
    assert not tray._progress_action.isVisible()
    assert tray._tray.toolTip() == "Refrain"
    tray.set_progress(-1, 0)
    assert tray._tray.toolTip() == "Refrain"


@pytest.mark.parametrize(
    "artist,album,line",
    [("Marlow Vance", "", "Marlow Vance"), ("", "Northbound", "Northbound"), ("", "", "—")],
)
def test_second_row_uses_what_the_source_knows(tray, artist, album, line):
    tray.set_track(_track(title="Paper Satellites", artist=artist, album=album))
    assert tray._artist_action.text() == line
    assert tray._artist_action.isVisible()


def test_new_track_drops_the_old_progress(tray):
    tray.set_track(_track(title="Ferrous", artist="Oskar Lind"))
    tray.set_progress(90_000, 131_000)
    tray.set_track(_track(title="Late Train Home", artist="Velvet Static"))
    assert tray._tray.toolTip() == "Late Train Home\nVelvet Static"
    assert not tray._progress_action.isVisible()


def test_same_track_again_keeps_the_progress(tray):
    tray.set_track(_track(title="Salt Flats", artist="Wren & Ash"))
    tray.set_progress(30_000, 0)
    tray.set_track(_track(title="Salt Flats", artist="Wren & Ash"))
    assert tray._tray.toolTip() == "Salt Flats\nWren & Ash\n0:30"
    assert tray._progress_action.isVisible()


def test_a_pause_asked_for_from_the_tray_shows_at_once(tray):
    tray.set_status(PlaybackStatus.PLAYING)
    tray._play_pause_action.trigger()
    tray.set_status(PlaybackStatus.PAUSED)
    assert not tray._pause_timer.isActive()
    assert tray._current_status == PlaybackStatus.PAUSED


def test_a_pause_from_elsewhere_waits_out_a_song_change(tray):
    tray.set_status(PlaybackStatus.PLAYING)
    tray.set_status(PlaybackStatus.PAUSED)
    assert tray._pause_timer.isActive()
    assert tray._pause_timer.interval() == 1000
    assert tray._current_status == PlaybackStatus.PLAYING


def test_only_the_hint_that_asked_for_it_reacts_to_a_click(tray):
    clicked = []
    tray.show_hint("Refrain closed unexpectedly last time.", lambda: clicked.append(1))
    tray._tray.messageClicked.emit()
    tray._tray.messageClicked.emit()
    assert clicked == [1]

    tray.show_hint("Refrain keeps running in the tray.")
    tray._tray.messageClicked.emit()
    assert clicked == [1]
