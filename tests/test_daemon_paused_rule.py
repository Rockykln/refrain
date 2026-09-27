"""Paused playback past its limit: hidden from the windows, kept for the
history and Last.fm."""

from __future__ import annotations

from refrain.daemon import _PAUSED_MAX_S, compute_paused_state
from refrain.sources.base import PlaybackStatus, TrackInfo


def _track(status=PlaybackStatus.PAUSED, title="Track"):
    return TrackInfo(
        source="mpris",
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=180_000,
        status=status,
    )


def test_a_pause_starts_the_clock_and_the_track_stays():
    track = _track()
    out, key, since = compute_paused_state(track, "", 0.0, now=1000.0)
    assert out is track
    assert key == track.content_key()
    assert since == 1000.0


def test_a_track_paused_under_the_limit_stays():
    track = _track()
    key0 = track.content_key()
    out, key, since = compute_paused_state(track, key0, 1000.0, now=1000.0 + _PAUSED_MAX_S)
    assert out is track
    assert key == key0
    assert since == 1000.0


def test_half_an_hour_paused_takes_the_track_off_the_screen():
    """A phone on Bluetooth names the song it stopped hours ago."""
    track = _track()
    key0 = track.content_key()
    out, key, since = compute_paused_state(track, key0, 1000.0, now=1001.0 + _PAUSED_MAX_S)
    assert not out.has_track
    assert key.endswith(key0) and key != key0
    assert since == 1000.0


def test_playing_again_resets_the_clock():
    playing = _track(status=PlaybackStatus.PLAYING)
    out, key, since = compute_paused_state(playing, playing.content_key(), 1000.0, now=9999.0)
    assert out is playing
    assert (key, since) == ("", 0.0)


def test_pausing_a_different_track_restarts_the_clock():
    other = _track(title="Another")
    out, key, since = compute_paused_state(other, "mpris|Track|Artist|Album", 1000.0, now=5000.0)
    assert out is other
    assert key == other.content_key()
    assert since == 5000.0


def test_a_dropped_track_stays_dropped_without_logging_again():
    track = _track()
    key0 = track.content_key()
    _, sentinel, _ = compute_paused_state(track, key0, 1000.0, now=1001.0 + _PAUSED_MAX_S)
    out, key, since = compute_paused_state(track, sentinel, 1000.0, now=1002.0 + _PAUSED_MAX_S)
    assert not out.has_track
    assert key == sentinel
    assert since == 1000.0


def test_a_poll_that_read_nothing_does_not_hand_the_track_a_fresh_half_hour():
    """MPRIS times out, the browser restarts, a player sits on the timeout
    blacklist — none of that is the pause ending."""
    track = _track()
    key0 = track.content_key()
    _, sentinel, since = compute_paused_state(track, key0, 1000.0, now=1001.0 + _PAUSED_MAX_S)

    blank = TrackInfo.empty()
    out, key, since = compute_paused_state(blank, sentinel, since, now=1002.0 + _PAUSED_MAX_S)
    assert out is blank
    assert (key, since) == (sentinel, 1000.0)

    # Same source, same paused track, one tick later: still off the screen.
    out2, _, _ = compute_paused_state(track, key, since, now=1003.0 + _PAUSED_MAX_S)
    assert not out2.has_track


def test_a_limit_of_zero_switches_the_rule_off():
    track = _track()
    out, key, since = compute_paused_state(track, track.content_key(), 1.0, now=1e9, limit_s=0)
    assert out is track
    assert (key, since) == ("", 0.0)
