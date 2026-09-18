"""Bluetooth: only a music app's track is a song."""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

import refrain.sources.bluetooth as bluetooth  # noqa: E402
from refrain.sources.base import PlaybackStatus  # noqa: E402


@pytest.mark.parametrize("app", ["Music", "Musik", "Spotify", "", "  music "])
def test_music_apps_play_music(app):
    assert bluetooth.is_music_app(app, 0)


@pytest.mark.parametrize("app", ["Twitch", "YouTube", "Netflix", "Podcasts"])
def test_streams_and_videos_are_not_music(app):
    assert not bluetooth.is_music_app(app, 215_000)


def test_an_unknown_app_counts_when_its_track_has_a_length():
    assert bluetooth.is_music_app("Some Player", 215_000)
    assert not bluetooth.is_music_app("Some Stream", 0)


class _Player:
    def __init__(self, app):
        self._props = {
            "Track": {
                "Title": "STAGE 02 2026: APL Groups - North Day 4",
                "Artist": "Rainbow6",
                "Album": "Twitch",
                "Duration": 0,
            },
            "Position": 156_929,
            "Status": "playing",
            "Repeat": "off",
            "Name": app,
        }

    def Get(self, _iface, prop):  # noqa: N802 — the D-Bus method's name
        return self._props[prop]


def _read(monkeypatch, app):
    player = _Player(app)
    src = bluetooth.BluetoothSource()
    monkeypatch.setattr(bluetooth, "_bluez_owned", lambda bus: True)
    monkeypatch.setattr(
        src, "_system_bus", lambda: type("Bus", (), {"get_object": lambda *a, **k: player})()
    )
    monkeypatch.setattr(src, "_find_player", lambda bus: "/org/bluez/hci0/dev_X/avrcp/player2")
    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: obj)
    return src.read()


def test_a_twitch_stream_is_not_a_song(monkeypatch):
    """Measured: an iPad playing Twitch put the stream on Discord and, four
    minutes in, into the history as a song by "Rainbow6"."""
    assert _read(monkeypatch, "Twitch").has_track is False


def test_the_music_app_still_plays(monkeypatch):
    track = _read(monkeypatch, "Music")
    assert (track.title, track.status) == (
        "STAGE 02 2026: APL Groups - North Day 4",
        PlaybackStatus.PLAYING,
    )
