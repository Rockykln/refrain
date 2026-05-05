"""TrackInfo dataclass and PlaybackStatus enum."""

from __future__ import annotations

from refrain.sources.base import PlaybackStatus, TrackInfo


def test_empty_track():
    t = TrackInfo.empty()
    assert t.source == "none"
    assert t.title == ""
    assert t.has_track is False
    assert t.status == PlaybackStatus.STOPPED


def test_has_track_requires_title():
    t = TrackInfo(source="mpris", artist="X")
    assert t.has_track is False
    t.title = "Song"
    assert t.has_track is True


def test_fingerprint_changes_on_title_artist_album_status():
    base = TrackInfo(
        source="mpris", title="A", artist="B", album="C", status=PlaybackStatus.PLAYING
    )
    assert (
        base.fingerprint()
        != TrackInfo(
            source="mpris",
            title="A2",
            artist="B",
            album="C",
            status=PlaybackStatus.PLAYING,
        ).fingerprint()
    )
    assert (
        base.fingerprint()
        != TrackInfo(
            source="mpris",
            title="A",
            artist="B",
            album="C",
            status=PlaybackStatus.PAUSED,
        ).fingerprint()
    )
    assert (
        base.fingerprint()
        != TrackInfo(
            source="bluetooth",
            title="A",
            artist="B",
            album="C",
            status=PlaybackStatus.PLAYING,
        ).fingerprint()
    )


def test_fingerprint_stable_for_same_inputs():
    a = TrackInfo(source="mpris", title="X", artist="Y", album="Z")
    b = TrackInfo(source="mpris", title="X", artist="Y", album="Z")
    assert a.fingerprint() == b.fingerprint()


def test_playback_status_values():
    assert PlaybackStatus.PLAYING.value == "playing"
    assert PlaybackStatus.PAUSED.value == "paused"
    assert PlaybackStatus.STOPPED.value == "stopped"
