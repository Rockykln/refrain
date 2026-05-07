"""Pure-Python timing helpers for the daemon.

Lives in its own module — kept free of Qt and D-Bus imports — so the unit
suite can exercise it in isolation without the GUI runtime installed.
"""

from __future__ import annotations


def pick_effective_duration_ms(mpris_dur_ms: int, itunes_dur_ms: int) -> int:
    """Choose the more trustworthy of MPRIS-reported and iTunes-catalog
    durations.

    Apple Music's MPRIS surface (chromium / firefox / plasma-browser-
    integration) is unreliable about ``mpris:length``:

    - During a brief preview-clip representation it reports 8-15 s on a
      track that's actually 2-5 minutes long.
    - During a playlist transition it sometimes reports the playlist
      total ("7:21" or "9:33") instead of the current track's length.
    - On the first MPRIS event after a track change it occasionally
      keeps the previous track's duration for a poll cycle.

    iTunes Search returns the canonical track length in milliseconds.
    When we have it (cover-art lookup populated the cache) and it
    disagrees with MPRIS by more than 15 %, we trust iTunes — the
    "0:14" / "7:21" total flickers users were seeing in Discord were
    Refrain faithfully forwarding whichever wrong value MPRIS sent.

    Falls back to the MPRIS value when iTunes has no match (search
    returned no result for this artist+title), so songs outside the
    catalog still get a progress bar.
    """
    if itunes_dur_ms <= 0:
        return mpris_dur_ms
    if mpris_dur_ms <= 0:
        return itunes_dur_ms
    if mpris_dur_ms < 30_000 <= itunes_dur_ms:
        return itunes_dur_ms
    relative_delta = abs(mpris_dur_ms - itunes_dur_ms) / itunes_dur_ms
    if relative_delta > 0.15:
        return itunes_dur_ms
    return mpris_dur_ms


def compute_rpc_start_ts(
    prev_start_ts: int,
    prev_track_key: str,
    track_key: str,
    position_ms: int,
    now: float,
    drift_threshold_s: float = 3.0,
    is_preview_clip: bool = False,
) -> tuple[int, bool]:
    """Decide whether to recompute the Discord RPC ``start`` timestamp.

    Discord renders elapsed time as ``now - start``. For that to match
    the track's actual position, ``start`` must follow the source's
    reported position. Recomputing on every poll, though, makes
    Discord's progress bar twitch by ±1 s with each tick.

    Strategy:

    1. Track changed (different ``track_key``) — always recompute.
    2. Same track, but the wall-clock view (``now - prev_start_ts``) has
       drifted from the source's reported position by more than
       ``drift_threshold_s`` — recompute. This catches **pause/resume**
       (wall clock advances while position is frozen) and **seeks**
       (position jumps without the song changing).
    3. Otherwise leave ``prev_start_ts`` alone.

    ``is_preview_clip`` skips step 2 entirely. Apple Music's MPRIS
    Position field loops 0→8 s→0→… while a preview-clip plays, which
    looks identical to a "user just seeked back" event to the drift
    check. Without this guard the elapsed counter in Discord would
    reset every 8 s and never climb past the preview-clip length.

    Returns ``(start_ts, recomputed)``.
    """
    if track_key != prev_track_key:
        return int(now - max(0, position_ms) / 1000.0), True
    if is_preview_clip:
        return prev_start_ts, False
    actual_position_s = max(0, position_ms) / 1000.0
    expected_position_s = max(0.0, now - prev_start_ts)
    drift_s = abs(expected_position_s - actual_position_s)
    if drift_s > drift_threshold_s:
        return int(now - actual_position_s), True
    return prev_start_ts, False
