"""Pure-Python timing helpers for the daemon.

Lives in its own module — kept free of Qt and D-Bus imports — so the unit
suite can exercise it in isolation without the GUI runtime installed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


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


@dataclass
class PositionState:
    """Rolling view of the source's reported ``Position`` for one track.

    Owned by the daemon worker and handed back into
    :func:`compensate_stalled_position` on every poll. Kept as a plain
    dataclass (rather than worker attributes) so the stall logic stays
    pure and unit-testable without a D-Bus or Qt runtime.
    """

    track_key: str = ""
    # Last *source-reported* position, and the monotonic timestamp at
    # which that value first showed up. Everything else is derived.
    raw_position_ms: int = 0
    anchor_at: float = 0.0
    stalled: bool = False


def compensate_stalled_position(
    state: PositionState,
    track_key: str,
    position_ms: int,
    duration_ms: int,
    is_playing: bool,
    now: float,
    stall_after_s: float = 4.0,
    tolerance_ms: int = 250,
) -> tuple[int, PositionState]:
    """Detect a frozen ``Position`` on a still-playing source and keep
    the clock running from wall time.

    Apple Music's MPRIS surface intermittently stops updating
    ``Position`` mid-track while ``PlaybackStatus`` stays ``Playing``:
    plasma-browser-integration's property cache goes stale, or the
    browser-native player only refreshes Position on a seek event. The
    song keeps playing in the tab, but every consumer that trusts
    Position (tray progress label, Discord's elapsed timer, the MPRIS
    server we publish to Plasma) sticks at the same second until the
    user seeks manually or restarts the track.

    The check: while the track is PLAYING and the same ``track_key``
    keeps reporting a position that hasn't moved by more than
    ``tolerance_ms`` for longer than ``stall_after_s`` seconds, the
    source is considered stalled. From then on the returned position is
    ``anchor position + wall-clock time since the anchor``, clamped to
    ``duration_ms`` — i.e. we run the clock ourselves instead of
    echoing a frozen number.

    ``tolerance_ms`` (not "exactly equal") absorbs sources that update
    Position coarsely: at a 250 ms poll interval a player refreshing
    once per second legitimately reports the same value twice in a
    row. Only a freeze that outlives ``stall_after_s`` counts.

    Recovery is automatic: as soon as the source reports a position
    that moved — a real tick, a seek, or the user's manual nudge — that
    value re-anchors the state and is passed through untouched.

    ``stall_after_s <= 0`` disables the whole mechanism (config knob
    ``advanced.position_stall_s``), and any non-playing / trackless
    poll resets the state, so a genuine pause is never extrapolated
    over. The one case this cannot distinguish is a source that lies
    about PlaybackStatus while actually paused; the clamp to
    ``duration_ms`` bounds how far that can run, and idle detection
    clears it from there.

    Returns ``(effective_position_ms, new_state)``.
    """
    if stall_after_s <= 0 or not is_playing or not track_key:
        return position_ms, PositionState()

    fresh = PositionState(
        track_key=track_key,
        raw_position_ms=position_ms,
        anchor_at=now,
        stalled=False,
    )
    if track_key != state.track_key:
        return position_ms, fresh
    if abs(position_ms - state.raw_position_ms) > tolerance_ms:
        # Source is alive — a normal tick, a seek, or the preview-clip
        # 0→8s→0 loop. Re-anchor and pass the real value through.
        return position_ms, fresh

    frozen_s = now - state.anchor_at
    if frozen_s <= stall_after_s:
        # Not frozen long enough to call it: keep the existing anchor
        # (so the freeze window keeps accumulating) and report as-is.
        return position_ms, state

    projected_ms = state.raw_position_ms + int(frozen_s * 1000)
    if duration_ms > 0:
        projected_ms = min(projected_ms, duration_ms)
    return projected_ms, replace(state, stalled=True)
