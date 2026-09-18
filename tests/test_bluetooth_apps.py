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
                "Title": "Live Stream",
                "Artist": "Some Channel",
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
    """A stream's title and channel are no song and artist."""
    assert _read(monkeypatch, "Twitch").has_track is False


def test_the_music_app_still_plays(monkeypatch):
    track = _read(monkeypatch, "Music")
    assert (track.title, track.status) == (
        "Live Stream",
        PlaybackStatus.PLAYING,
    )


def _managed(*players):
    objects = {}
    for mac, status, alias in players:
        device = f"/org/bluez/hci0/dev_{mac}"
        objects[device] = {"org.bluez.Device1": {"Alias": alias}}
        objects[f"{device}/avrcp/player0"] = {
            "org.bluez.MediaPlayer1": {"Status": status, "Device": device}
        }
    return objects


def _find(monkeypatch, objects, device_mac=""):
    src = bluetooth.BluetoothSource(device_mac)

    class Bus:
        def get_object(self, *_a, **_k):
            return type("M", (), {"GetManagedObjects": lambda self: objects})()

    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: obj)
    return src._find_player(Bus()), src._player_name


def test_the_playing_device_wins_over_an_idle_one(monkeypatch):
    objects = _managed(("AA", "stopped", "tablet"), ("BB", "playing", "phone"))
    path, name = _find(monkeypatch, objects)
    assert "dev_BB" in path and name == "phone"


def test_a_paused_device_wins_over_a_stopped_one(monkeypatch):
    objects = _managed(("AA", "stopped", "tablet"), ("BB", "paused", "phone"))
    assert "dev_BB" in _find(monkeypatch, objects)[0]


def test_a_chosen_device_is_kept_even_when_another_plays(monkeypatch):
    objects = _managed(("AA", "stopped", "tablet"), ("BB", "playing", "phone"))
    assert "dev_AA" in _find(monkeypatch, objects, "AA")[0]
