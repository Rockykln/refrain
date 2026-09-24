"""TrackInfo dataclass and PlaybackStatus enum."""

from __future__ import annotations

from refrain.history import HistoryEntry, _content_key, _entry_key
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


def test_a_pipe_in_the_title_cannot_impersonate_another_song():
    # Joined without escaping, both give "mpris|A|B|C|D".
    one = TrackInfo(source="mpris", title="A|B", artist="C", album="D")
    other = TrackInfo(source="mpris", title="A", artist="B|C", album="D")
    assert one.content_key() != other.content_key()
    assert one.fingerprint() != other.fingerprint()


def test_a_pipe_in_the_album_keeps_the_album_boundary():
    # timing._only_album_differs cuts the album off at the last "|".
    same_album = TrackInfo(source="mpris", title="T", artist="A", album="Live | Session")
    other_album = TrackInfo(source="mpris", title="T", artist="A", album="Studio")
    assert same_album.content_key().rsplit("|", 1)[0] == other_album.content_key().rsplit("|", 1)[0]


def test_a_percent_sign_is_not_confused_with_an_escaped_pipe():
    escaped = TrackInfo(source="mpris", title="A%7CB", artist="C", album="D")
    piped = TrackInfo(source="mpris", title="A|B", artist="C", album="D")
    assert escaped.content_key() != piped.content_key()


def test_the_history_keys_a_track_and_its_entry_the_same_way():
    track = TrackInfo(source="mpris", title="A|B", artist="C", album="D")
    entry = HistoryEntry(source="mpris", title="A|B", artist="C", album="D")
    assert _content_key(track) == _entry_key(entry)
