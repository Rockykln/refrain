"""Pure-Python timing helpers for the daemon, free of Qt and D-Bus so tests need neither."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

# A source length below this, on a song the catalog knows to be longer,
# describes a clip or a buffered media segment rather than the song.
CLIP_MAX_MS = 30_000
# A *start frame*: the player reporting the song itself — its full
# length, give or take this — at a position no further in than this.
_START_FRAME_LENGTH_SLACK_MS = 2_000
_START_FRAME_MAX_POS_MS = 3_000
# Plasma can report the song's start frame this late after a loop.
_LATE_START_MAX_POS_MS = 25_000
_LOOP_END_SLACK_MS = 5_000
# A start frame this far into the track is the song beginning again;
# any sooner, it is the track change we already saw, reported twice.
_RESTART_AFTER_MS = 30_000
# Polls that see nothing for no longer than this, followed by the same
# track, are a hiccup of the source (D-Bus, a browser integration busy
# for a few seconds) rather than the song stopping.
_GONE_GRACE_S = 10.0


def pick_effective_duration_ms(mpris_dur_ms: int, catalog_ms: int, learned_ms: int = 0) -> int:
    """Choose the track length from the source's, the catalog's and a learned one.

    A catalog length wins whenever there is one. The source's
    ``mpris:length`` describes the media element, and on a browser that
    is often not the song: a buffer that grows as the stream loads (5:23,
    7:23, 10:06 on a 2:45 song), or a length far short of it (1:30 on a
    3:16 song). A catalog search can land on the wrong record too, but
    that is rarer than a browser misreporting, and the lookup only
    returns a length for a result whose artist and title match.

    Without a catalog length the source's own number stands, with the
    length measured from whole plays (see refrain.song_lengths) filling in:

    - No ``mpris:length`` at all (Bluetooth AVRCP mostly).
    - A length under 30 s where the measured one is longer: Apple Music's
      preview-clip representation, or a buffered media segment.
    """
    if catalog_ms > 0:
        return catalog_ms
    if mpris_dur_ms <= 0:
        return learned_ms
    if learned_ms <= 0:
        return mpris_dur_ms
    if mpris_dur_ms < CLIP_MAX_MS <= learned_ms:
        return learned_ms
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
       ``drift_threshold_s`` — recompute. This catches pause/resume
       (wall clock advances while position is frozen) and seeks
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
    """Which of the position sources produced the current value."""

    REPORTED = "reported"  # the source's own Position, believed
    COMPUTED = "computed"  # our clock, anchored at a track start we saw
    ESTIMATED = "estimated"  # our clock, placed from the progress saved before a restart
    UNKNOWN = "unknown"  # none is trustworthy — render nothing


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
    # How often this track has begun again while we watched — a loop, or
    # played again from the top. The daemon compares it between polls.
    restarts: int = 0
    # The last poll saw nothing playing. A track that appears after that,
    # near zero, is a song somebody just started — the one case where a
    # segment source's zero may place the clock (see _anchor_new_track).
    after_idle: bool = False
    # A start frame arrived while paused. It only counts once the song then
    # plays on from there: a tablet pausing can report position 0 for a tenth
    # of a second before its real position comes back.
    start_pending: bool = False
    # When polls stopped seeing this track, if they have (see _GONE_GRACE_S).
    gone_at: float = 0.0
    # Our clock was placed from the progress the history saved before
    # Refrain restarted, not from anything this session saw: shown, but as
    # an estimate, and only until a better tier has an answer.
    estimated: bool = False
    # The source read zero when the estimate was placed. A player with no
    # position reads zero too, so that zero is only believed once it moves.
    held_zero: bool = False


def start_is_witnessed(state: PositionState) -> bool:
    """Do we know where this track began, on the source's own evidence?

    True when the source reset its position for this track *and* that
    reset placed our clock's zero. Both halves matter: the reset is what
    proves the source describes tracks at all, and the anchor is what
    makes the zero ours to count from.

    When it holds, our own clock is the better answer to anything the
    source does with its position afterwards — a return to zero, a jump
    backwards — because we watched the track start and it did not.
    """
    return state.anchored and state.track_relative


def source_position_is_fresh(moved_at: float, now: float, stall_after_s: float) -> bool:
    """Has the source's own position moved recently enough to be believed?

    The one place that answers it, because two callers ask: the resolver
    uses it to decide whether tier 1 is still on the table, and idle
    detection uses the same movement as proof the source handle isn't
    dangling. `stall_after_s <= 0` means "never call it stale"; idle
    detection therefore never passes it one.
    """
    return stall_after_s <= 0 or (now - moved_at) <= stall_after_s


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
    loop_track: bool = False,
    estimate_ms: int | None = None,
) -> tuple[int | None, PositionTier, PositionState]:
    """Resolve the current position through four tiers, in order.

    Sources lie about position in several different ways; this is the
    single decision for all of them:

    1. What the source reports, when it holds up. It must be
       non-negative, must not sit past the end of the track (Apple
       Music's web player counts Position across the whole queue, so
       three songs in it reads 11:08 on a 2:25 track), and — while
       playing — must actually be moving (the same player stops
       refreshing Position mid-track while still reporting Playing).
       Accepting it also re-anchors our own clock to it, so tier 2 can
       pick up from the last value known to be good.

    2. Our own clock, when the reported value fails but we know when
       the track started: wall-clock elapsed since that anchor, minus
       time spent paused. This is what carries a queue-cumulative or
       frozen source through to the end of the track.

    3. An estimate, right after Refrain restarted mid-song: our clock
       placed at ``estimate_ms``, where the history's saved progress
       says the song is now. Only for the track first seen with it, and
       only while nothing better holds and it stays inside the track.

    4. Nothing. No anchor to count from, or even our own clock has
       run past the end of the track — the source has been claiming
       "playing" for longer than the song lasts. The caller hides the
       time entirely rather than showing a number known to be wrong.

    ``reported_length_ms`` is the source's *raw* length, as opposed to
    the catalog-corrected ``duration_ms`` the tiers are judged against.
    A length that changes while the same track plays is not a track
    length — Apple Music's grows as its stream buffers, by 135 s over
    144 s of playback — and latches the source as
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
    caller renders elapsed-only in that case.

    A source length under ``CLIP_MAX_MS`` on a song the catalog knows to
    be longer is a *segment* source: Plasma's browser integration reports
    the media segment the page has buffered, whose position runs 0 → 10 s
    and starts over, all song long. Its position is never the song's, so
    it is never shown, and its returns to zero are never a frame switch
    or a seek. Its length need not move either (a constant 10.4 s), so
    the length check alone does not catch it.

    A song that begins again on the same track — repeat-one, or played
    again from the top — shows as a *start frame*: the player reporting
    the song's own length at a position near zero. A segment source does
    that only when the song really starts, never in between. Well into
    the track, a start frame places our clock's zero afresh and counts in
    ``state.restarts``. ``loop_track`` says the player repeats this one
    track; a Bluetooth phone on repeat-one keeps counting its position
    across loops (5:13 into a 3:36 song), and then the song's own
    position is what is left over after whole loops.

    Returns ``(position_ms_or_None, tier, new_state)``.
    """
    if not track_key:
        if state.track_key and (not state.gone_at or now - state.gone_at <= _GONE_GRACE_S):
            return None, PositionTier.UNKNOWN, replace(state, gone_at=state.gone_at or now)
        return None, PositionTier.UNKNOWN, PositionState(after_idle=True)
    if state.gone_at:
        state = (
            replace(state, gone_at=0.0)
            if track_key == state.track_key
            else PositionState(after_idle=True)
        )
    # Whatever the catalog says — for the first polls after a start it
    # hasn't said anything yet, and a segment would then pass for a very short
    # song. A song that really is that short loses nothing: its time comes
    # from our clock, anchored at the change, and reads the same.
    segment = 0 < reported_length_ms < CLIP_MAX_MS
    if loop_track and duration_ms >= CLIP_MAX_MS and reported_ms > duration_ms:
        loop_ms = reported_length_ms if reported_length_ms >= CLIP_MAX_MS else duration_ms
        reported_ms %= loop_ms

    if track_key != state.track_key and _only_album_differs(track_key, state.track_key):
        near_zero = not segment and 0 <= reported_ms <= max(tolerance_ms, 2_000)
        if not near_zero:
            # The same song with its album filled in late, not a new one:
            # the source kept counting, so the clock does too.
            state = replace(state, track_key=track_key)
    if track_key != state.track_key:
        state, moved = (
            _anchor_new_track(
                state, track_key, reported_ms, now, tolerance_ms, segment, estimate_ms
            ),
            True,
        )
        state = replace(state, last_length_ms=reported_length_ms)
    else:
        start_frame = _is_start_frame(reported_ms, reported_length_ms, duration_ms) or (
            0 < state.last_length_ms < CLIP_MAX_MS
            and _is_late_start_frame(state, reported_ms, reported_length_ms, duration_ms, now)
        )
        # Arriving at the start, not sitting there: the position fell back
        # from well into the song, or the length just turned from a
        # segment's into the song's. A source frozen at zero does neither.
        arrived = start_frame and (
            state.last_reported_ms - reported_ms >= _RESTART_AFTER_MS
            or abs(reported_length_ms - state.last_length_ms) > _START_FRAME_LENGTH_SLACK_MS
        )
        if not start_frame:
            state = replace(state, start_pending=False)
        elif arrived and not is_playing:
            state = replace(state, start_pending=True)
        if (
            start_frame
            and is_playing
            and (arrived or state.start_pending)
            and (not state.anchored or elapsed_ms(state, now) >= _RESTART_AFTER_MS)
        ):
            # The song began again. The frame is the player's own track
            # start, so it is our clock's new zero — unanchored or not.
            state = replace(
                state,
                started_at=now - reported_ms / 1000.0,
                paused_ms=0,
                paused_since=0.0,
                anchored=True,
                track_relative=True,
                restarts=state.restarts + 1,
                start_pending=False,
                estimated=False,
            )
        state = _track_length(state, reported_length_ms)
        if (
            state.cumulative
            and not segment
            and not state.held_zero
            and not start_is_witnessed(state)
            and 0 <= reported_ms <= max(tolerance_ms, 2_000)
        ):
            # The source just produced a plausible track start mid-track.
            # A seek can't do that on a stream-relative timeline — seeking
            # to the top of a song still lands hundreds of seconds into
            # the stream — so the player has changed what it is counting.
            # Believe the new frame and take the latch off.
            #
            # Only where we don't already know better. Plasma's browser
            # integration reports the position and length of the media
            # *segment* the page has buffered, so it returns to zero
            # every eight to eleven seconds — and it does reset properly
            # at a track change, which means we already have a real zero
            # for this track. Reading each of those returns as a frame
            # switch would re-anchor the clock every few seconds.
            state = replace(
                state,
                cumulative=False,
                track_relative=True,
                started_at=now - reported_ms / 1000.0,
                paused_ms=0,
                paused_since=now if not is_playing else 0.0,
                anchored=True,
                estimated=False,
            )
        elif state.cumulative and is_playing and not segment and not start_is_witnessed(state):
            # The same question gates this. Following a seek trusts the
            # source's *timeline* while distrusting its absolute value —
            # worth doing when the timeline is all we have, wrong when we
            # watched the track start ourselves. A segment source's fall
            # back to zero would read as a seek backwards and put the
            # elapsed time at 0:00.
            state = _follow_seek(state, reported_ms, now, tolerance_ms)
        state, moved = _track_movement(state, reported_ms, now, tolerance_ms)
        if state.held_zero and moved:
            state = replace(state, held_zero=False, track_relative=True)
    state = replace(state, last_seen_at=now)
    if not is_playing:
        # The freshness clock only runs while playing. A paused source is
        # supposed to stand still, and letting the stall window accrue
        # through a pause would make every resume from a pause longer than
        # `stall_after_s` look like a freeze for one poll.
        state = replace(state, moved_at=now)
    state = _track_pause(state, is_playing, now)

    # -- tier 1: the source's own value -------------------------------
    # `stall_after_s <= 0` disables the freshness check entirely, which
    # is what `advanced.position_stall_s = 0` is documented to do.
    frozen = is_playing and not source_position_is_fresh(state.moved_at, now, stall_after_s)
    past_end = duration_ms > 0 and reported_ms > duration_ms + overrun_grace_ms
    undecidable = duration_disputed and not state.anchored
    # A tablet pausing can report 0 for a moment. Passed on as the player's
    # own position, it would look like the song starting over.
    unconfirmed_start = state.start_pending and not is_playing
    if (
        reported_ms >= 0
        and not unconfirmed_start
        and not segment
        and not frozen
        and not past_end
        and not state.cumulative
        and not undecidable
        and not state.held_zero
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
                estimated=False,
            ),
        )

    # -- tier 2: our own clock ----------------------------------------
    if state.anchored:
        elapsed = elapsed_ms(state, now)
        if elapsed >= 0 and (duration_ms <= 0 or elapsed <= duration_ms + overrun_grace_ms):
            return elapsed, PositionTier.COMPUTED, state

    # -- tier 3: the progress saved before a restart ------------------
    if state.estimated and not state.anchored:
        estimate = elapsed_ms(state, now)
        # No overrun grace: an estimate past the end is more likely wrong
        # than a song running long.
        if estimate >= 0 and (duration_ms <= 0 or estimate <= duration_ms):
            return estimate, PositionTier.ESTIMATED, state
        state = replace(state, estimated=False)

    # -- tier 4: no honest answer -------------------------------------
    return None, PositionTier.UNKNOWN, state


def _only_album_differs(key: str, other: str) -> bool:
    """Do two track keys (``source|title|artist|album``) differ in the album alone?"""
    return bool(other) and key.rsplit("|", 1)[0] == other.rsplit("|", 1)[0]


def elapsed_ms(state: PositionState, now: float) -> int:
    """Our own clock: time since the anchor, less time spent paused."""
    ms = int((now - state.started_at) * 1000) - state.paused_ms
    if state.paused_since:
        ms -= int((now - state.paused_since) * 1000)
    return ms


def _is_start_frame(reported_ms: int, reported_length_ms: int, duration_ms: int) -> bool:
    """Is the player reporting the song itself, right at its beginning?"""
    return (
        duration_ms >= CLIP_MAX_MS
        and abs(reported_length_ms - duration_ms) <= _START_FRAME_LENGTH_SLACK_MS
        and 0 <= reported_ms <= _START_FRAME_MAX_POS_MS
    )


def _is_late_start_frame(
    state: PositionState, reported_ms: int, reported_length_ms: int, duration_ms: int, now: float
) -> bool:
    """A segment source leaving its segments soon after a loop.

    Plasma may report a buffered length instead of the song's then, so
    our own clock reaching the song's end has to vouch for it.
    """
    if not 0 <= reported_ms <= _LATE_START_MAX_POS_MS or duration_ms < CLIP_MAX_MS:
        return False
    if abs(reported_length_ms - duration_ms) <= _START_FRAME_LENGTH_SLACK_MS:
        return True
    return (
        reported_length_ms >= CLIP_MAX_MS
        and state.anchored
        and elapsed_ms(state, now) >= duration_ms - _LOOP_END_SLACK_MS
    )


def _anchor_new_track(
    state: PositionState,
    track_key: str,
    reported_ms: int,
    now: float,
    tolerance_ms: int,
    segment: bool = False,
    estimate_ms: int | None = None,
) -> PositionState:
    """Start a fresh clock for a track, anchored if we can place its start.

    Two ways to know where the track began: the source restarted its
    position for it (every ordinary MPRIS player, Bluetooth AVRCP), or
    we were already watching another track and saw this one take over —
    which places the boundary within one poll, whatever the source's
    numbers say. Neither applies to the track that was already playing
    when Refrain started, and that one stays unanchored.

    A segment source's position is near zero every ten seconds, so on
    first sight it proves a track start only after a poll that saw
    nothing playing. Right after Refrain starts mid-song it lands in a
    segment's first two seconds far too often to be taken at its word.

    ``estimate_ms`` places an unanchored clock there, marked estimated.
    It stands against a zero from the source until that zero moves.
    """
    # A negative position is garbage, not a track start — it must not
    # place a zero the clock would then count from.
    reset_by_source = 0 <= reported_ms <= max(tolerance_ms, 2_000)
    witnessed_change = bool(state.track_key)
    if segment and not (witnessed_change or state.after_idle):
        reset_by_source = False
    estimated = estimate_ms is not None and estimate_ms >= 0 and not witnessed_change
    held_zero = estimated and reset_by_source
    if held_zero:
        reset_by_source = False
    anchored = reset_by_source or witnessed_change
    # A change the source didn't reset for means its position belongs to
    # the stream rather than to the track. Latched here and cleared the
    # moment a change does reset, so a source that starts behaving (or a
    # different player taking over) gets tier 1 back.
    cumulative = not reset_by_source and (witnessed_change or state.cumulative)
    if estimated and estimate_ms is not None:
        started_at = now - estimate_ms / 1000.0
    else:
        started_at = now - (reported_ms / 1000.0 if reset_by_source else 0.0)
    return PositionState(
        track_key=track_key,
        started_at=started_at,
        anchored=anchored,
        cumulative=cumulative,
        track_relative=reset_by_source or (state.track_relative and not cumulative),
        last_reported_ms=reported_ms,
        last_seen_at=now,
        moved_at=now,
        estimated=estimated,
        held_zero=held_zero,
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
    # Measured from when that value was read as new, not from the last
    # poll: a source that stood still for a while and then caught up has
    # not been seeked.
    jump_ms = delta_ms - int((now - state.moved_at) * 1000)
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
    # Latching for the first time normally voids the anchor too, because
    # the anchor that usually exists at this point came from tier 1
    # believing a value we have just learned belongs to the stream.
    #
    # Not when the source reset its position for this track: then the
    # zero we are counting from is a track start we watched happen, and
    # it stays true however unreliable the length turns out to be. That
    # is the difference between hiding the time and simply counting it
    # ourselves — and this source is common enough (Plasma's browser
    # integration, whose length is the current media segment's) that
    # throwing the anchor away would hide the elapsed time on every track.
    return replace(
        state,
        last_length_ms=reported_length_ms,
        cumulative=True,
        anchored=start_is_witnessed(state),
    )
