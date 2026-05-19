"""Source selection: an actively-playing source beats a paused one.

Regression guard for the bug where a stale *paused* Apple Music tab in
the browser (has_track=True, PAUSED) permanently masked music actively
playing over Bluetooth headphones — idle detection only fires on
PLAYING, so the paused tab never got cleared either.
"""

from __future__ import annotations

from refrain.daemon import select_source_track
from refrain.sources.base import PlaybackStatus, TrackInfo


def _track(source, status, title="Song", duration_ms=180_000):
    return TrackInfo(
        source=source,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=duration_ms,
        position_ms=0,
        status=status,
    )


def test_nothing_when_both_absent():
    track, source = select_source_track(None, None)
    assert source == "none"
    assert not track.has_track


def test_single_source_passes_through():
    bt = _track("bluetooth", PlaybackStatus.PLAYING)
    track, source = select_source_track(None, bt)
    assert source == "bluetooth"
    assert track is bt


def test_playing_bluetooth_beats_paused_mpris():
    """The actual bug: paused browser tab + actively-playing headphones."""
    paused_tab = _track("mpris", PlaybackStatus.PAUSED, title="Old Tab")
    playing_bt = _track("bluetooth", PlaybackStatus.PLAYING, title="Live Music")
    track, source = select_source_track(paused_tab, playing_bt)
    assert source == "bluetooth"
    assert track.title == "Live Music"


def test_playing_mpris_beats_paused_bluetooth():
    playing_tab = _track("mpris", PlaybackStatus.PLAYING, title="Live Tab")
    paused_bt = _track("bluetooth", PlaybackStatus.PAUSED, title="Idle BT")
    track, source = select_source_track(playing_tab, paused_bt)
    assert source == "mpris"
    assert track.title == "Live Tab"


def test_mpris_wins_tie_when_neither_playing():
    """Both paused/loaded → MPRIS keeps priority so the active source
    doesn't flip-flop between two idle sources every poll."""
    paused_tab = _track("mpris", PlaybackStatus.PAUSED)
    paused_bt = _track("bluetooth", PlaybackStatus.PAUSED)
    track, source = select_source_track(paused_tab, paused_bt)
    assert source == "mpris"


def test_mpris_wins_when_both_playing():
    """Two simultaneously-playing sources is unusual; MPRIS (browser)
    is the project's primary target, so it stays the tie-break winner."""
    playing_tab = _track("mpris", PlaybackStatus.PLAYING)
    playing_bt = _track("bluetooth", PlaybackStatus.PLAYING)
    _, source = select_source_track(playing_tab, playing_bt)
    assert source == "mpris"


def test_stopped_source_with_no_track_is_not_a_candidate():
    empty_mpris = TrackInfo.empty()  # source="none", no title, STOPPED
    playing_bt = _track("bluetooth", PlaybackStatus.PLAYING)
    track, source = select_source_track(empty_mpris, playing_bt)
    assert source == "bluetooth"
    assert track is playing_bt
