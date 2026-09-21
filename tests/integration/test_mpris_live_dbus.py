"""MPRISSource against a real dbus-daemon and a real fake MPRIS player — no mocks.

Each test drives ``refrain.sources.mpris.MPRISSource`` (the actual reader) against a
subprocess that exports a real ``org.mpris.MediaPlayer2`` service on a private session
bus, started with ``dbus-run-session``. Skips cleanly when dbus-python, PySide6 or the
D-Bus session tools aren't installed (see conftest.py).
"""

from __future__ import annotations

from refrain.sources.base import PlaybackStatus
from refrain.sources.mpris import MPRISSource

APPLE_TRACK = "https://music.apple.com/us/album/paper-satellites/12345"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _apple_metadata(
    title="Paper Satellites",
    artist="Marlow Vance",
    album="Night Frequencies",
    length_us=196_000_000,
    url=APPLE_TRACK,
):
    return {
        "xesam:title": title,
        "xesam:artist": [artist],
        "xesam:album": album,
        "xesam:url": url,
        "mpris:length": length_us,
        "mpris:artUrl": "https://is1-ssl.mzstatic.com/image/thumb/12345/cover.jpg",
    }


def test_apple_music_track_is_recognised_with_exact_fields(spawn_player):
    player = spawn_player("firefox.instance12345", "Firefox")
    player.update(status="Playing", metadata=_apple_metadata(), position_us=5_000_000)

    track = MPRISSource().read()

    assert track.title == "Paper Satellites"
    assert track.artist == "Marlow Vance"
    assert track.album == "Night Frequencies"
    assert track.duration_ms == 196_000
    assert track.position_ms == 5_000
    assert track.status == PlaybackStatus.PLAYING
    assert track.url == APPLE_TRACK


def test_youtube_url_is_not_recognised(spawn_player):
    player = spawn_player("firefox.instance12345", "Firefox")
    player.update(
        status="Playing",
        metadata=_apple_metadata(url=YOUTUBE_URL),
        position_us=1_000_000,
    )

    track = MPRISSource().read()

    assert not track.has_track


def test_chromium_without_url_is_not_recognised_as_apple_music(spawn_player):
    # Chromium outside KDE never exposes xesam:url at all — see mpris.py's
    # own comment on the no-URL case. Another agent is adding a display hint
    # for this; this test only checks recognition, not that hint.
    metadata = _apple_metadata()
    del metadata["xesam:url"]
    player = spawn_player("chromium.instance12345", "Chromium")
    player.update(status="Playing", metadata=metadata, position_us=1_000_000)

    track = MPRISSource().read()

    assert not track.has_track


def test_metadata_change_via_properties_changed_is_picked_up(spawn_player):
    player = spawn_player("firefox.instance12345", "Firefox")
    player.update(status="Playing", metadata=_apple_metadata(title="Paper Satellites"))
    src = MPRISSource()
    first = src.read()
    assert first.title == "Paper Satellites"

    player.update(
        status="Playing",
        metadata=_apple_metadata(title="Skyline Static", album="Night Frequencies"),
    )
    second = src.read()

    assert second.title == "Skyline Static"
    assert second.album == "Night Frequencies"


def test_player_disappearing_removes_the_source(spawn_player):
    player = spawn_player("firefox.instance12345", "Firefox")
    player.update(status="Playing", metadata=_apple_metadata())
    src = MPRISSource()
    assert src.read().has_track

    player.kill()
    assert spawn_player.wait_gone(player.bus_name), "the fake player never left the bus"

    assert not src.read().has_track


def test_two_players_the_playing_apple_one_wins(spawn_player):
    paused = spawn_player("brave.instance12345", "Brave")
    paused.update(status="Paused", metadata=_apple_metadata(title="Paused Track"))

    playing = spawn_player("firefox.instance12345", "Firefox")
    playing.update(status="Playing", metadata=_apple_metadata(title="Paper Satellites"))

    track = MPRISSource().read()

    assert track.title == "Paper Satellites"
    assert track.status == PlaybackStatus.PLAYING


def test_play_pause_next_calls_reach_the_player(spawn_player):
    player = spawn_player("firefox.instance12345", "Firefox")
    player.update(status="Playing", metadata=_apple_metadata())
    src = MPRISSource()
    src.read()  # establishes this player as the primary / last player

    assert src.play() is True
    assert src.pause() is True
    assert src.next() is True

    assert player.calls() == ["Play", "Pause", "Next"]
