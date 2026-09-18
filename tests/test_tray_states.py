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

from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.startup_check import DISABLED, INVALID, OK, UNREACHABLE, CheckResult  # noqa: E402
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


def test_left_click_opens_settings_and_middle_click_toggles_playback(tray):
    opened, toggled = [], []
    tray.settingsRequested.connect(lambda: opened.append(1))
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


def test_discord_row_goes_back_to_not_connected(tray):
    tray.set_discord_connected(True)
    tray.set_discord_connected(False)
    assert tray._discord_action.text() == "Discord: not connected"


def test_stopping_from_idle_applies_at_once(tray):
    tray.set_status(PlaybackStatus.PAUSED)
    assert tray._current_status == PlaybackStatus.PAUSED
    assert not tray._pause_timer.isActive()
    assert tray._play_pause_action.text() == "Play"


def test_rejected_discord_id_is_flagged_and_lastfm_stays_hidden_when_off(tray):
    tray.set_startup_check(CheckResult(DISABLED), CheckResult(INVALID, "bad id"))
    assert tray._discord_action.text() == "Discord: rejected — check Application ID"
    assert not tray._lastfm_action.isVisible()


def test_unreachable_discord_leaves_the_row_alone(tray):
    tray.set_discord_connected(True)
    tray.set_startup_check(CheckResult(DISABLED), CheckResult(UNREACHABLE))
    assert tray._discord_action.text() == "Discord: connected"


@pytest.mark.parametrize(
    "result,text",
    [
        (CheckResult(OK, "marlowvance"), "Last.fm: connected as marlowvance"),
        (CheckResult(OK), "Last.fm: connected"),
        (CheckResult(INVALID, "Invalid session key"), "Last.fm: session expired — reconnect"),
        (CheckResult(UNREACHABLE, "timed out"), "Last.fm: could not be verified"),
    ],
)
def test_lastfm_row_reports_the_check_result(tray, result, text):
    tray.set_startup_check(result, CheckResult(OK))
    assert tray._lastfm_action.isVisible()
    assert tray._lastfm_action.text() == text
    assert tray._discord_action.text() == "Discord: not connected"


def test_lastfm_row_hides_again_when_scrobbling_is_switched_off(tray):
    tray.set_startup_check(CheckResult(OK, "marlowvance"), CheckResult(OK))
    tray.set_startup_check(CheckResult(DISABLED), CheckResult(OK))
    assert not tray._lastfm_action.isVisible()


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
