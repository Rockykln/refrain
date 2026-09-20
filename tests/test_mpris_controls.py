"""Plasma's Play / Pause / Stop only toggle a source when that reaches the asked state.
Named to be collected before test_mpris_dispatch.py, which mocks ``dbus`` for the session."""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.sources.mpris_server import MPRISServer  # noqa: E402


def _server(status: PlaybackStatus):
    toggles: list[str] = []
    server = MPRISServer(
        on_play_pause=lambda: toggles.append("toggle"),
        on_next=lambda: None,
        on_previous=lambda: None,
    )
    server._track = TrackInfo(source="mpris", title="T", status=status)
    return server, toggles


@pytest.mark.parametrize(
    "method,status,toggled",
    [
        ("Stop", PlaybackStatus.PAUSED, False),  # Stop must not start the music
        ("Stop", PlaybackStatus.PLAYING, True),
        ("Pause", PlaybackStatus.PAUSED, False),
        ("Pause", PlaybackStatus.PLAYING, True),
        ("Play", PlaybackStatus.PLAYING, False),
        ("Play", PlaybackStatus.PAUSED, True),
        ("Play", PlaybackStatus.STOPPED, True),
        ("PlayPause", PlaybackStatus.PAUSED, True),
        ("PlayPause", PlaybackStatus.PLAYING, True),
    ],
)
def test_toggle_only_towards_the_asked_state(method, status, toggled):
    server, toggles = _server(status)
    getattr(server, method)()
    assert bool(toggles) is toggled


def test_a_title_beyond_ascii_still_makes_a_valid_track_id():
    """Non-ASCII titles like "Wer weiß das schon" still give a readable Metadata."""
    from refrain.sources.mpris_server import _track_id

    for title in (
        "Silk Road Radio",
        "Hanabi Afterglow (花火)",
        "Late Train Home (feat. Sora Minamiツ & Kite Theory's Band)",
        "",
    ):
        path = _track_id(TrackInfo(source="mpris", title=title))
        assert path.startswith("/refrain/track/")


# --------------------------------------------------- what the panel reads


def _published(**kw):
    server, _ = _server(PlaybackStatus.PLAYING)
    server._track = TrackInfo(source="mpris", status=PlaybackStatus.PLAYING, **kw)
    return server


def test_the_metadata_carries_the_song():
    server = _published(
        title="Hanabi Afterglow (花火)", artist="Sora Minami", album="Album", position_ms=61_500
    )
    server._cover_url = "https://example.org/cover.jpg"
    server._effective_duration_ms = 197_873
    props = server.GetAll("org.mpris.MediaPlayer2.Player")
    md = props["Metadata"]
    assert md["xesam:title"] == "Hanabi Afterglow (花火)"
    assert list(md["xesam:artist"]) == ["Sora Minami"]
    assert md["mpris:length"] == 197_873_000, "microseconds, as the spec wants"
    assert md["mpris:artUrl"] == "https://example.org/cover.jpg"
    assert props["Position"] == 61_500_000
    assert props["PlaybackStatus"] == "Playing"


def test_empty_fields_are_left_out():
    md = _published(title="T").GetAll("org.mpris.MediaPlayer2.Player")["Metadata"]
    assert set(md) == {"mpris:trackid", "xesam:title"}


def test_an_unknown_property_is_an_error():
    import dbus

    with pytest.raises(dbus.exceptions.DBusException):
        _published(title="T").Get("org.mpris.MediaPlayer2.Player", "Nonsense")


def test_the_player_names_itself_refrain():
    root = _published(title="T").GetAll("org.mpris.MediaPlayer2")
    assert (root["Identity"], root["DesktopEntry"]) == ("Refrain", "refrain")


def test_inside_the_flatpak_the_desktop_entry_is_the_app_id(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.Rockykln.Refrain")
    root = _published(title="T").GetAll("org.mpris.MediaPlayer2")
    assert root["DesktopEntry"] == "io.github.Rockykln.Refrain"


def test_a_length_arriving_late_reaches_the_panel():
    """The length often comes a poll after the title."""
    server = _published(title="T")
    server._bus_name = object()
    sent = []
    server.PropertiesChanged = lambda iface, changed, inv: sent.append(changed)
    track = TrackInfo(source="mpris", title="T", status=PlaybackStatus.PLAYING)
    server._apply(track, None, None)
    server._apply(track, None, None)
    assert sent == [], "nothing moved, nothing sent"
    server._apply(track, None, 157_000)
    assert len(sent) == 1
    assert sent[0]["Metadata"]["mpris:length"] == 157_000_000


@pytest.mark.parametrize(
    ("method", "expected"), [("Play", "play"), ("Pause", "pause"), ("Stop", "pause")]
)
@pytest.mark.parametrize("status", [PlaybackStatus.PLAYING, PlaybackStatus.PAUSED])
def test_play_and_pause_reach_the_source_as_themselves(method, expected, status):
    calls: list[str] = []
    server = MPRISServer(
        on_play_pause=lambda: calls.append("toggle"),
        on_next=lambda: None,
        on_previous=lambda: None,
        on_play=lambda: calls.append("play"),
        on_pause=lambda: calls.append("pause"),
    )
    server._track = TrackInfo(source="mpris", title="Glass Tides", status=status)
    getattr(server, method)()
    assert calls == [expected]
