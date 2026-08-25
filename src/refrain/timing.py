"""Pure-Python timing helpers for the daemon.

Lives in its own module — kept free of Qt and D-Bus imports — so the unit
suite can exercise it in isolation without the GUI runtime installed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


def pick_effective_duration_ms(mpris_dur_ms: int, itunes_dur_ms: int) -> int:
    """Choose between the source-reported and iTunes-catalog track lengths.

    The source's ``mpris:length`` describes the media element actually
    playing, so it is the better answer whenever it is an answer at all.
    The iTunes duration is a catalog guess reached by searching for an
    artist and title, and a search can land on the wrong record: a live
    session had it return 58 s for a 2:45 song, and the daemon believed
    it — long enough for idle detection to clear a track a minute into
    playing it.

    So iTunes fills gaps rather than overruling:

    - No ``mpris:length`` at all (Bluetooth AVRCP mostly, and Apple
      Music on some tracks) — the catalog is all there is.
    - A length under 30 s where the catalog says otherwise. That's Apple
      Music's preview-clip representation, which it reports for a few
      seconds on a full-length song; the catalog is right there.

    Anything else keeps the source's own number. The one case that used
    to need iTunes to overrule — Apple Music reporting a running total
    instead of a track length — is not a length problem at all: that
    player reports a position and a length for the *stream*, and both
    are recognised as such by ``resolve_position``, whose caller then
    asks for the catalog length directly.
    """
    if mpris_dur_ms <= 0:
        return itunes_dur_ms
    if itunes_dur_ms <= 0:
        return mpris_dur_ms
    if mpris_dur_ms < 30_000 <= itunes_dur_ms:
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
    # Latched when the source has been seen describing something other
    # than the current track — carrying its position across a track
    # change, or changing the track's length underneath it. Tier 1 is
    # off the table for as long as it holds.
    cumulative: bool = False
    # Positive evidence of the opposite: the source restarted its
    # position for a track, so it does describe tracks. Until one or the
    # other is established, a source is an unknown quantity and its
    # numbers are only as good as what corroborates them.
    track_relative: bool = False
    # Last length the source reported for this track. A track's length
    # does not change; a stream's does.
    last_length_ms: int = 0
    # Freshness of the source's own value: what it last read, when it was
    # read, and when it last actually moved.
    last_reported_ms: int = 0
    last_seen_at: float = 0.0
    moved_at: float = 0.0


def resolve_position(
    state: PositionState,
    track_key: str,
    reported_ms: int,
    duration_ms: int,
    is_playing: bool,
    now: float,
    reported_length_ms: int = 0,
    duration_disputed: bool = False,
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

    ``reported_length_ms`` is the source's *raw* length, as opposed to
    the catalog-corrected ``duration_ms`` the tiers are judged against.
    A length that changes while the same track plays is not a track
    length — Apple Music's grows as its stream buffers, by 135 s over
    144 s of playback in one measurement — and latches the source as
    stream-relative on its own. Without it, a session that starts
    mid-track has nothing to catch the source out with until the first
    track change, and spends that time rendering a plausible-looking
    wrong pair.

    ``duration_disputed`` says the source's length and the catalog's
    disagree and nothing yet establishes which to believe — the state at
    startup mid-track, where a position past the catalog length is
    equally consistent with "the catalog matched the wrong record" and
    "this position belongs to a stream". Tier 1's end-of-track check is
    meaningless then, so tier 1 is only offered for a track whose start
    we actually saw. A merely *absent* length is not a dispute: nothing
    contradicts the source, and Bluetooth AVRCP tracks keep their
    elapsed count.

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
        state = replace(state, last_length_ms=reported_length_ms)
    else:
        state = _track_length(state, reported_length_ms)
        if state.cumulative and 0 <= reported_ms <= max(tolerance_ms, 2_000):
            # The source just produced a plausible track start mid-track.
            # A seek can't do that on a stream-relative timeline — seeking
            # to the top of a song still lands hundreds of seconds into
            # the stream — so the player has changed what it is counting.
            # Believe the new frame and take the latch off.
            state = replace(
                state,
                cumulative=False,
                started_at=now - reported_ms / 1000.0,
                paused_ms=0,
                paused_since=now if not is_playing else 0.0,
                anchored=True,
            )
        elif state.cumulative and is_playing:
            state = _follow_seek(state, reported_ms, now, tolerance_ms)
        state, moved = _track_movement(state, reported_ms, now, tolerance_ms)
    state = replace(state, last_seen_at=now)
    if not is_playing:
        # The freshness clock only runs while playing. A paused source is
        # supposed to stand still, and letting the stall window accrue
        # through a pause made every resume from a pause longer than
        # `stall_after_s` look like a freeze for one poll.
        state = replace(state, moved_at=now)
    state = _track_pause(state, is_playing, now)

    # -- tier 1: the source's own value -------------------------------
    # `stall_after_s <= 0` disables the freshness check entirely, which
    # is what `advanced.position_stall_s = 0` is documented to do.
    frozen = stall_after_s > 0 and is_playing and (now - state.moved_at) > stall_after_s
    past_end = duration_ms > 0 and reported_ms > duration_ms + overrun_grace_ms
    undecidable = duration_disputed and not state.anchored
    if (
        reported_ms >= 0
        and not frozen
        and not past_end
        and not state.cumulative
        and not undecidable
    ):
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
    # A change the source didn't reset for means its position belongs to
    # the stream rather than to the track. Latched here and cleared the
    # moment a change does reset, so a source that starts behaving (or a
    # different player taking over) gets tier 1 back.
    cumulative = not reset_by_source and (witnessed_change or state.cumulative)
    return PositionState(
        track_key=track_key,
        started_at=now - (reported_ms / 1000.0 if reset_by_source else 0.0),
        anchored=anchored,
        cumulative=cumulative,
        track_relative=reset_by_source or (state.track_relative and not cumulative),
        last_reported_ms=reported_ms,
        last_seen_at=now,
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


def _follow_seek(
    state: PositionState, reported_ms: int, now: float, tolerance_ms: int
) -> PositionState:
    """Shift our clock when the user seeks on a stream-relative source.

    Its position is useless as an absolute in-track value, but it is
    still a faithful *timeline*: over one poll it advances by exactly the
    wall-clock time that passed, unless someone dragged the slider. The
    excess is the seek, and our clock's zero moves by the same amount.

    A frozen source looks superficially similar — its position also
    fails to advance by the elapsed time — so this only acts on a value
    that actually moved. A freeze moves nothing and is left to the
    caller's stall handling.
    """
    delta_ms = reported_ms - state.last_reported_ms
    if abs(delta_ms) <= tolerance_ms:
        return state  # standing still: a freeze, not a seek
    jump_ms = delta_ms - int((now - state.last_seen_at) * 1000)
    if abs(jump_ms) <= max(tolerance_ms, 2_000):
        return state  # ordinary playback advance
    # Never past `now`: a start in the future would mean negative elapsed,
    # and no seek can put the track's beginning ahead of the clock.
    return replace(state, started_at=min(state.started_at - jump_ms / 1000.0, now))


def _track_length(state: PositionState, reported_length_ms: int) -> PositionState:
    """Latch the source as stream-relative if it moves the track's length.

    A track is as long as it is. A length that shifts underneath an
    unchanging track is describing something else — for Apple Music's
    web player, how far its stream has buffered — and that is decisive
    on its own, without waiting for a track change to prove it.

    The tolerance absorbs a player re-reporting the same length with
    microsecond rounding; the growth this catches is in whole seconds.
    """
    if reported_length_ms <= 0:
        return state
    if state.last_length_ms <= 0:
        return replace(state, last_length_ms=reported_length_ms)
    if abs(reported_length_ms - state.last_length_ms) <= 1_000:
        return state
    if state.cumulative:
        return replace(state, last_length_ms=reported_length_ms)
    # Latching for the first time also voids the anchor, because the only
    # anchor that can exist at this point came from tier 1 believing a
    # value we have just learned belongs to the stream. An anchor placed
    # by a witnessed track start is safe from this: that path latches at
    # the change itself, so it never reaches here un-latched.
    return replace(state, last_length_ms=reported_length_ms, cumulative=True, anchored=False)
