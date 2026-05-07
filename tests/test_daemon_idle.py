"""Idle-detection: drop "stuck" playback past duration + grace.

When a browser tab gets closed without releasing the MPRIS handle,
PlaybackStatus often stays "Playing" while Position freezes. The
daemon notices that the same track-key has been "playing" for longer
than the track's own duration plus a grace window, and treats the
source as dangling — clearing the Discord status until something else
shows up.
"""

from __future__ import annotations

from refrain.daemon import compute_idle_state
from refrain.sources.base import PlaybackStatus, TrackInfo


def _playing(title="Track", artist="Artist", album="Album", duration_ms=180_000):
    return TrackInfo(
        source="mpris",
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        position_ms=0,
        status=PlaybackStatus.PLAYING,
    )


def test_first_seen_starts_the_clock():
    track = _playing()
    out, key, seen = compute_idle_state(track, "", 0.0, grace_s=30, now=1000.0)
    assert out is track  # passes through on first sight
    assert key == f"mpris|{track.title}|{track.artist}|{track.album}"
    assert seen == 1000.0


def test_clears_after_duration_plus_grace():
    """3 min track + 30 s grace — 240 s after first seen, idle triggers."""
    track = _playing(duration_ms=180_000)
    key0 = f"mpris|{track.title}|{track.artist}|{track.album}"

    out, key, seen = compute_idle_state(track, key0, 1000.0, grace_s=30, now=1240.0)
    assert not out.has_track
    assert out.source == "none"
    # New key carries the sentinel so the next poll's call doesn't
    # re-log "Idle source detected" every tick while the track stays
    # stuck on the broken MPRIS handle.
    assert key.endswith(key0)
    assert "__refrain_idle_logged__" in key
    assert seen == 1000.0  # seen-at is preserved on idle so we stay idle


def test_idle_log_only_once_per_dangling_track():
    """Subsequent polls for the same idle track keep returning empty
    without re-emitting the log, until the track key actually changes."""
    track = _playing(duration_ms=180_000)
    key0 = f"mpris|{track.title}|{track.artist}|{track.album}"

    # First trip past the deadline → logs + sets sentinel key.
    _, sentinel_key, _ = compute_idle_state(track, key0, 1000.0, grace_s=30, now=1240.0)

    # Second poll, same stuck track — re-uses the sentinel without growing it.
    out2, key2, _ = compute_idle_state(track, sentinel_key, 1000.0, grace_s=30, now=1241.0)
    assert not out2.has_track
    assert key2 == sentinel_key


def test_does_not_clear_within_window():
    track = _playing(duration_ms=180_000)
    key0 = f"mpris|{track.title}|{track.artist}|{track.album}"
    out, key, seen = compute_idle_state(track, key0, 1000.0, grace_s=30, now=1100.0)
    assert out is track
    assert key == key0
    assert seen == 1000.0


def test_resets_when_track_changes():
    b = _playing(title="B")
    key_a = "mpris|A|Artist|Album"
    out, key, seen = compute_idle_state(b, key_a, 1000.0, grace_s=30, now=1500.0)
    assert out is b
    assert key == "mpris|B|Artist|Album"
    assert seen == 1500.0


def test_disabled_when_grace_is_zero():
    track = _playing(duration_ms=10_000)
    out, key, seen = compute_idle_state(track, "stale", 0.0, grace_s=0, now=999_999.0)
    assert out is track
    assert key == ""
    assert seen == 0.0


def test_passes_through_when_no_duration():
    """Live streams / podcasts without a length — idle math can't apply."""
    track = _playing(duration_ms=0)
    out, key, seen = compute_idle_state(track, "stale", 1000.0, grace_s=30, now=999_999.0)
    assert out is track
    assert key == ""
    assert seen == 0.0


def test_paused_track_is_never_idle():
    """User-initiated pauses are legitimate — only PLAYING-but-frozen counts."""
    track = TrackInfo(
        source="mpris",
        title="A",
        artist="B",
        album="C",
        duration_ms=10_000,
        status=PlaybackStatus.PAUSED,
    )
    out, key, seen = compute_idle_state(track, "stale", 1000.0, grace_s=30, now=999_999.0)
    assert out is track
    assert key == ""
    assert seen == 0.0


def test_empty_track_is_never_idle():
    out, key, seen = compute_idle_state(TrackInfo.empty(), "x", 1000.0, grace_s=30, now=2000.0)
    assert out.source == "none"
    assert key == ""
    assert seen == 0.0


def test_effective_duration_overrides_mpris_for_deadline():
    """When MPRIS lies about duration (e.g. 7:21 playlist total on a
    2:11 song), passing the iTunes-corrected value should give idle
    detection a sensible deadline instead of waiting 5 extra minutes."""
    real_dur_ms = 131_000  # 2:11 — the truth
    mpris_dur_ms = 441_000  # 7:21 — what MPRIS reports
    track = TrackInfo(
        source="mpris",
        title="A",
        artist="B",
        album="C",
        duration_ms=mpris_dur_ms,
        status=PlaybackStatus.PLAYING,
    )
    track_key = "mpris|A|B|C"
    seen_at = 100.0
    # Just past the *real* deadline (131s + 30s grace = 161s) but
    # well within the MPRIS-reported one (441s + 30s = 471s).
    now = seen_at + 165.0
    out, key, _seen = compute_idle_state(
        track,
        prev_track_key=track_key,
        prev_seen_at=seen_at,
        grace_s=30,
        now=now,
        effective_duration_ms=real_dur_ms,
    )
    assert out.source == "none"
    assert key.startswith("__refrain_idle_logged__:")


def test_effective_duration_preview_clip_skip_uses_effective():
    """A preview-clip-mode MPRIS report (14 s) on a song iTunes knows
    is full-length (3:00) must NOT be skipped from idle detection —
    the effective duration is full-length so dangling-handle protection
    still applies."""
    track = TrackInfo(
        source="mpris",
        title="A",
        artist="B",
        album="C",
        duration_ms=14_000,  # MPRIS preview-clip lie
        status=PlaybackStatus.PLAYING,
    )
    real_dur = 180_000  # iTunes truth: 3:00
    track_key = "mpris|A|B|C"
    seen_at = 100.0
    now = seen_at + 250.0  # past 3:00 + 30 s grace = 210 s
    out, key, _seen = compute_idle_state(
        track,
        prev_track_key=track_key,
        prev_seen_at=seen_at,
        grace_s=30,
        now=now,
        effective_duration_ms=real_dur,
    )
    assert out.source == "none"
    assert key.startswith("__refrain_idle_logged__:")
