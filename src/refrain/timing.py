"""Pure-Python timing helpers for the daemon.

Lives in its own module — kept free of Qt and D-Bus imports — so the unit
suite can exercise it in isolation without the GUI runtime installed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


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


class PositionTier(StrEnum):
    """Which of the three position sources produced the current value."""

    REPORTED = "reported"  # the source's own Position, believed
    COMPUTED = "computed"  # our clock, anchored at a track start we saw
    UNKNOWN = "unknown"  # neither is trustworthy — render nothing


@dataclass
class PositionState:
    """Everything the resolver remembers between polls, for one track."""

    track_key: str = ""
    # Our own clock: the monotonic instant this track is believed to have
    # started, plus paused time to discount from the elapsed span.
    started_at: float = 0.0
    paused_ms: int = 0
    paused_since: float = 0.0
    # True once we know where this track began — either the source
    # reported it near zero, or we witnessed the change from another
    # track. False when Refrain came up mid-song: the clock has no zero
    # to count from, and inventing one would be a lie.
    anchored: bool = False
    # Freshness of the source's own value: what it last read, and when it
    # last actually moved.
    last_reported_ms: int = 0
    moved_at: float = 0.0


def resolve_position(
    state: PositionState,
    track_key: str,
    reported_ms: int,
    duration_ms: int,
    is_playing: bool,
    now: float,
    stall_after_s: float = 4.0,
    tolerance_ms: int = 250,
    overrun_grace_ms: int = 5_000,
) -> tuple[int | None, PositionTier, PositionState]:
    """Resolve the current position through three tiers, in order.

    Sources lie about position in several different ways, and each lie
    used to need its own patch. This is the single decision instead:

    1. **What the source reports**, when it holds up. It must be
       non-negative, must not sit past the end of the track (Apple
       Music's web player counts Position across the whole queue, so
       three songs in it reads 11:08 on a 2:25 track), and — while
       playing — must actually be moving (the same player stops
       refreshing Position mid-track while still reporting Playing).
       Accepting it also re-anchors our own clock to it, so tier 2 can
       pick up from the last value known to be good.

    2. **Our own clock**, when the reported value fails but we know when
       the track started: wall-clock elapsed since that anchor, minus
       time spent paused. This is what carries a queue-cumulative or
       frozen source through to the end of the track.

    3. **Nothing.** No anchor to count from, or even our own clock has
       run past the end of the track — the source has been claiming
       "playing" for longer than the song lasts. The caller hides the
       time entirely rather than showing a number known to be wrong.

    ``duration_ms <= 0`` means the source gave no length (common on
    Bluetooth AVRCP): start and end are the same instant, so the two
    end-of-track checks are skipped and only movement decides. The
    caller renders elapsed-only in that case, as it always has.

    Returns ``(position_ms_or_None, tier, new_state)``.
    """
    if not track_key:
        return None, PositionTier.UNKNOWN, PositionState()

    if track_key != state.track_key:
        state, moved = _anchor_new_track(state, track_key, reported_ms, now, tolerance_ms), True
    else:
        state, moved = _track_movement(state, reported_ms, now, tolerance_ms)
    state = _track_pause(state, is_playing, now)

    # -- tier 1: the source's own value -------------------------------
    # `stall_after_s <= 0` disables the freshness check entirely, which
    # is what `advanced.position_stall_s = 0` is documented to do.
    frozen = stall_after_s > 0 and is_playing and (now - state.moved_at) > stall_after_s
    past_end = duration_ms > 0 and reported_ms > duration_ms + overrun_grace_ms
    if reported_ms >= 0 and not frozen and not past_end:
        if not moved:
            # Believed, but standing still — inside the stall window, or
            # paused. Re-syncing the clock to a value that isn't moving
            # would drag its zero along with it and quietly swallow the
            # seconds the source spent stuck.
            return reported_ms, PositionTier.REPORTED, state
        # Believed and moving — so make it our clock's zero as well. If
        # the source goes bad later in this track, tier 2 resumes here.
        return (
            reported_ms,
            PositionTier.REPORTED,
            replace(
                state,
                started_at=now - reported_ms / 1000.0,
                paused_ms=0,
                paused_since=now if not is_playing else 0.0,
                anchored=True,
            ),
        )

    # -- tier 2: our own clock ----------------------------------------
    if state.anchored:
        elapsed_ms = int((now - state.started_at) * 1000) - state.paused_ms
        if state.paused_since:
            elapsed_ms -= int((now - state.paused_since) * 1000)
        if elapsed_ms >= 0 and (duration_ms <= 0 or elapsed_ms <= duration_ms + overrun_grace_ms):
            return elapsed_ms, PositionTier.COMPUTED, state

    # -- tier 3: no honest answer -------------------------------------
    return None, PositionTier.UNKNOWN, state


def _anchor_new_track(
    state: PositionState,
    track_key: str,
    reported_ms: int,
    now: float,
    tolerance_ms: int,
) -> PositionState:
    """Start a fresh clock for a track, anchored if we can place its start.

    Two ways to know where the track began: the source restarted its
    position for it (every ordinary MPRIS player, Bluetooth AVRCP), or
    we were already watching another track and saw this one take over —
    which places the boundary within one poll, whatever the source's
    numbers say. Neither applies to the track that was already playing
    when Refrain started, and that one stays unanchored.
    """
    # A negative position is garbage, not a track start — it must not
    # place a zero the clock would then count from.
    reset_by_source = 0 <= reported_ms <= max(tolerance_ms, 2_000)
    witnessed_change = bool(state.track_key)
    anchored = reset_by_source or witnessed_change
    return PositionState(
        track_key=track_key,
        started_at=now - (reported_ms / 1000.0 if reset_by_source else 0.0),
        anchored=anchored,
        last_reported_ms=reported_ms,
        moved_at=now,
    )


def _track_movement(
    state: PositionState, reported_ms: int, now: float, tolerance_ms: int
) -> tuple[PositionState, bool]:
    """Remember when the source's value last actually changed.

    The tolerance rather than exact equality absorbs players that
    refresh Position coarsely: at a 250 ms poll a source updating once
    per second legitimately repeats itself.

    Returns ``(new_state, moved_this_poll)``.
    """
    if abs(reported_ms - state.last_reported_ms) > tolerance_ms:
        return replace(state, last_reported_ms=reported_ms, moved_at=now), True
    return state, False


def _track_pause(state: PositionState, is_playing: bool, now: float) -> PositionState:
    """Accumulate paused time so our own clock doesn't run through a pause."""
    if is_playing and state.paused_since:
        return replace(
            state,
            paused_ms=state.paused_ms + int((now - state.paused_since) * 1000),
            paused_since=0.0,
        )
    if not is_playing and not state.paused_since:
        return replace(state, paused_since=now)
    return state
