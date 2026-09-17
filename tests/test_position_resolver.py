"""resolve_position — the three-tier position decision.

Tier 1 is what the source reports, tier 2 is our own clock anchored at a
track start we witnessed, tier 3 is nothing at all. The numbers in the
queue-cumulative tests come from a live session log: "Blessings" starts
at 234299 ms on Apple Music's queue timeline and the change to "Dior"
lands at 454260 ms — one poll tick after 234299 plus its 219650 ms
catalog length.
"""

from __future__ import annotations

from refrain.timing import (
    PositionState,
    PositionTier,
    resolve_position,
    start_is_witnessed,
)

A = "mpris|No Broke Boys|Disco Lines|"
B = "mpris|Blessings|Calvin Harris|"
C = "mpris|Dior|MK|"
DUR = 220_000


def step(state, key, reported_ms, now, *, duration_ms=DUR, playing=True, length_ms=None, **kw):
    """`length_ms` defaults to `duration_ms`: for most sources the raw
    length and the effective one are the same number, and the tests that
    care about them diverging say so explicitly."""
    kw.setdefault("reported_length_ms", duration_ms if length_ms is None else length_ms)
    return resolve_position(state, key, reported_ms, duration_ms, playing, now, **kw)


# --------------------------------------------------- tier 1: believe the source


def test_ordinary_source_is_passed_through():
    state = PositionState()
    pos, tier, state = step(state, A, 0, 1000.0)
    assert (pos, tier) == (0, PositionTier.REPORTED)
    for i in range(1, 20):
        pos, tier, state = step(state, A, i * 500, 1000.0 + i * 0.5)
        assert (pos, tier) == (i * 500, PositionTier.REPORTED)


def test_seek_is_followed():
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    pos, tier, state = step(state, A, 120_000, 1000.5)
    assert (pos, tier) == (120_000, PositionTier.REPORTED)


def test_coarse_source_repeating_a_value_is_still_believed():
    # A player refreshing once per second repeats itself at a 500 ms
    # poll. Only a freeze outlasting stall_after_s counts.
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    pos, tier, state = step(state, A, 30_000, 1000.5)
    assert tier is PositionTier.REPORTED
    pos, tier, state = step(state, A, 31_000, 1001.0)
    assert (pos, tier) == (31_000, PositionTier.REPORTED)


def test_paused_source_is_believed_however_long_it_sits():
    state = PositionState()
    _, _, state = step(state, A, 60_000, 1000.0)
    pos, tier, state = step(state, A, 60_000, 1600.0, playing=False)
    assert (pos, tier) == (60_000, PositionTier.REPORTED)


def test_missing_duration_still_believes_a_moving_source():
    # Bluetooth AVRCP often reports no length; elapsed-only is correct.
    state = PositionState()
    _, _, state = step(state, A, 5_000, 1000.0, duration_ms=0)
    pos, tier, state = step(state, A, 6_000, 1001.0, duration_ms=0)
    assert (pos, tier) == (6_000, PositionTier.REPORTED)


# ------------------------------------------------ tier 2: fall back to our clock


def test_frozen_position_falls_back_to_our_clock():
    state = PositionState()
    _, _, state = step(state, A, 60_000, 1000.0)
    pos, tier, state = step(state, A, 60_000, 1003.0)
    assert tier is PositionTier.REPORTED  # inside the stall window
    pos, tier, state = step(state, A, 60_000, 1005.0)
    assert (pos, tier) == (65_000, PositionTier.COMPUTED)
    pos, tier, state = step(state, A, 60_000, 1030.0)
    assert (pos, tier) == (90_000, PositionTier.COMPUTED)


def test_source_recovering_from_a_freeze_is_believed_again():
    state = PositionState()
    _, _, state = step(state, A, 60_000, 1000.0)
    _, tier, state = step(state, A, 60_000, 1010.0)
    assert tier is PositionTier.COMPUTED
    pos, tier, state = step(state, A, 71_000, 1011.0)
    assert (pos, tier) == (71_000, PositionTier.REPORTED)


def test_queue_cumulative_position_falls_back_to_our_clock():
    state = PositionState()
    # Playing "Blessings", position already past this track's length
    # because the source counts across the whole queue.
    _, _, state = step(state, B, 234_299, 1000.0)
    _, _, state = step(state, B, 234_799, 1000.5)
    # Change to "Dior" witnessed → the clock is anchored at this poll.
    pos, tier, state = step(state, C, 454_260, 1220.0, duration_ms=169_159)
    assert (pos, tier) == (0, PositionTier.COMPUTED)
    pos, tier, state = step(state, C, 514_260, 1280.0, duration_ms=169_159)
    assert (pos, tier) == (60_000, PositionTier.COMPUTED)


def test_our_clock_does_not_run_through_a_pause():
    state = PositionState()
    _, _, state = step(state, B, 300_000, 1000.0)
    _, _, state = step(state, C, 300_500, 1000.5, duration_ms=169_159)
    pos, tier, state = step(state, C, 310_500, 1010.5, duration_ms=169_159)
    assert (pos, tier) == (10_000, PositionTier.COMPUTED)
    # Paused for ~100 s. The source's value is frozen and past the end,
    # so our clock is what has to stand still.
    for now in (1011.0, 1050.0, 1110.5):
        pos, tier, state = step(state, C, 310_500, now, duration_ms=169_159, playing=False)
        assert pos == 10_500
    # Resumed — the pause is discounted, not counted as playback.
    pos, tier, state = step(state, C, 310_500, 1120.5, duration_ms=169_159)
    assert (pos, tier) == (10_500, PositionTier.COMPUTED)
    pos, tier, state = step(state, C, 310_500, 1130.5, duration_ms=169_159)
    assert (pos, tier) == (20_500, PositionTier.COMPUTED)


def test_a_track_starting_at_zero_anchors_even_without_a_predecessor():
    state = PositionState()
    pos, tier, state = step(state, A, 0, 1000.0)
    assert tier is PositionTier.REPORTED
    # Source freezes immediately; the clock still knows where zero was.
    pos, tier, state = step(state, A, 0, 1030.0)
    assert (pos, tier) == (30_000, PositionTier.COMPUTED)


# ------------------------------------------------------- tier 3: show nothing


def test_no_anchor_and_a_bad_value_hides_the_time():
    # Refrain came up mid-song on a queue-cumulative source: the
    # reported position is past this track's end and there is no
    # witnessed start to count from.
    state = PositionState()
    pos, tier, state = step(state, C, 598_756, 1000.0, duration_ms=169_159)
    assert pos is None
    assert tier is PositionTier.UNKNOWN


def test_hidden_state_recovers_at_the_next_track_change():
    state = PositionState()
    _, tier, state = step(state, C, 598_756, 1000.0, duration_ms=169_159)
    assert tier is PositionTier.UNKNOWN
    pos, tier, state = step(state, A, 623_997, 1100.0, duration_ms=144_900)
    assert (pos, tier) == (0, PositionTier.COMPUTED)


def test_our_clock_running_past_the_track_hides_the_time():
    # A source stuck on "playing" long after the song can have ended.
    state = PositionState()
    _, _, state = step(state, A, 0, 1000.0)
    pos, tier, state = step(state, A, 0, 1000.0 + DUR / 1000 - 10)
    assert tier is PositionTier.COMPUTED
    pos, tier, state = step(state, A, 0, 1000.0 + DUR / 1000 + 30)
    assert pos is None
    assert tier is PositionTier.UNKNOWN


def test_negative_reported_position_is_never_shown():
    state = PositionState()
    pos, tier, state = step(state, A, -1, 1000.0)
    assert pos is None
    assert tier is PositionTier.UNKNOWN


def test_no_track_for_a_while_resets_everything():
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    for now in (1001.0, 1006.0, 1012.0):
        pos, tier, state = step(state, "", 0, now)
        assert pos is None
        assert tier is PositionTier.UNKNOWN
    # All forgotten but the one thing the next track needs to know.
    assert state == PositionState(after_idle=True)


def test_another_track_after_a_moment_of_nothing_starts_as_after_idle():
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    _, _, state = step(state, "", 0, 1001.0)
    pos, tier, state = step(state, B, 0, 1002.0)
    assert (pos, tier) == (0, PositionTier.REPORTED)
    assert (state.track_key, state.cumulative, state.anchored) == (B, False, True)


def test_a_moment_of_nothing_mid_segment_keeps_the_clock():
    """Probe: at 2:00 one empty poll (a D-Bus hiccup), then the segment
    source came back 1.5 s into a segment — and that was taken for the
    song's start, 2:00 behind for the rest of it."""
    state = PositionState()
    _, _, state = step(state, B, 120_000, 1000.0)
    seen, state = _segments(state, A, 1001.0, 300, 120)
    assert 119_000 <= seen[-1][0] <= 121_000
    _, _, state = step(state, "", 0, 1121.5)
    seen, state = _segments(state, A, 1122.0, 1_500, 20)
    assert all(tier is PositionTier.COMPUTED for _, tier in seen)
    assert 140_000 <= seen[-1][0] <= 142_000


def test_stall_check_disabled_keeps_believing_a_frozen_source():
    state = PositionState()
    _, _, state = step(state, A, 60_000, 1000.0, stall_after_s=0)
    pos, tier, state = step(state, A, 60_000, 1600.0, stall_after_s=0)
    assert (pos, tier) == (60_000, PositionTier.REPORTED)


# ------------------------------------------- a source that streams, not tracks


def test_stream_relative_source_stops_being_believed_after_one_change():
    # Apple Music's web player carries its position across track
    # boundaries: the position belongs to the stream, so it is never an
    # in-track value again once we have seen that happen.
    state = PositionState()
    _, tier, state = step(state, B, 234_299, 1000.0, duration_ms=0)
    assert tier is PositionTier.REPORTED  # nothing yet says otherwise
    pos, tier, state = step(state, C, 267_590, 1035.0, duration_ms=0)
    assert (pos, tier) == (0, PositionTier.COMPUTED)
    assert state.cumulative is True
    # Even with no catalog length to catch it out, the reported value is
    # not offered again — this is the regression that made the elapsed
    # timer disappear one poll after every track change.
    pos, tier, state = step(state, C, 297_590, 1065.0, duration_ms=0)
    assert (pos, tier) == (30_000, PositionTier.COMPUTED)


def test_a_late_catalog_length_does_not_poison_the_anchor():
    # The exact live sequence: at the track change the only length known
    # is the source's own (a stream buffer marker, far larger than the
    # song), so nothing looks wrong yet. The catalog answers a second
    # later with the real one.
    state = PositionState()
    _, _, state = step(state, B, 234_299, 1000.0, duration_ms=411_918)
    pos, tier, state = step(state, C, 267_590, 1035.0, duration_ms=411_918)
    assert (pos, tier) == (0, PositionTier.COMPUTED)
    pos, tier, state = step(state, C, 268_090, 1035.5, duration_ms=200_000)
    assert (pos, tier) == (500, PositionTier.COMPUTED)


def test_seek_on_a_stream_relative_source_moves_our_clock():
    state = PositionState()
    _, _, state = step(state, B, 234_299, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 267_590, 1035.0, duration_ms=0)
    pos, _, state = step(state, C, 297_590, 1065.0, duration_ms=0)
    assert pos == 30_000
    # User drags the slider 60 s forward: the stream position jumps by
    # far more than the half-second that passed.
    pos, tier, state = step(state, C, 358_090, 1065.5, duration_ms=0)
    assert (pos, tier) == (90_500, PositionTier.COMPUTED)
    # And keeps counting from there.
    pos, _, state = step(state, C, 368_090, 1075.5, duration_ms=0)
    assert pos == 100_500


def test_a_frozen_stream_position_is_not_mistaken_for_a_seek():
    state = PositionState()
    _, _, state = step(state, B, 234_299, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 267_590, 1035.0, duration_ms=0)
    # Source stops updating entirely; our clock must keep its zero.
    for now in (1040.0, 1050.0, 1065.0):
        pos, tier, state = step(state, C, 267_590, now, duration_ms=0)
    assert (pos, tier) == (30_000, PositionTier.COMPUTED)


def test_a_stream_position_catching_up_after_a_stall_is_not_a_seek():
    """Probe: stuck for 15 s, then right again — read as a 15 s seek, and
    the time ran 15 s ahead until the next song."""
    state = PositionState()
    _, _, state = step(state, B, 234_299, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 267_590, 1035.0, duration_ms=0)
    for now in (1036.0, 1037.0, 1038.0, 1039.0, 1040.0):
        _, _, state = step(state, C, 267_590 + int((now - 1035.0) * 1000), now, duration_ms=0)
    for now in (1041.0, 1045.0, 1050.0, 1055.0):
        _, _, state = step(state, C, 272_590, now, duration_ms=0)
    pos, tier, state = step(state, C, 288_590, 1056.0, duration_ms=0)
    assert (pos, tier) == (21_000, PositionTier.COMPUTED)


def test_an_album_filled_in_late_is_the_same_song():
    """Probe: the album arrived 4 s in, the key changed, and the clock
    started again at zero — 4 s behind, with tier 1 lost for the song."""
    state = PositionState()
    _, _, state = step(state, B, 0, 1000.0)
    for s in range(1, 4):
        _, _, state = step(state, B, s * 1_000, 1000.0 + s)
    pos, tier, state = step(state, B + "Some Album", 4_000, 1004.0)
    assert (pos, tier) == (4_000, PositionTier.REPORTED)
    assert state.cumulative is False
    pos, tier, state = step(state, B + "Some Album", 33_000, 1033.0)
    assert (pos, tier) == (33_000, PositionTier.REPORTED)


def test_the_same_title_on_another_album_from_the_top_is_a_new_track():
    state = PositionState()
    _, _, state = step(state, B, 200_000, 1000.0)
    pos, tier, state = step(state, B + "Live", 0, 1001.0)
    assert (pos, tier) == (0, PositionTier.REPORTED)
    assert state.track_key == B + "Live"


def test_a_source_that_resets_again_gets_tier_1_back():
    state = PositionState()
    _, _, state = step(state, B, 234_299, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 267_590, 1035.0, duration_ms=0)
    assert state.cumulative is True
    # A different player takes over and reports per-track positions.
    pos, tier, state = step(state, A, 0, 1200.0)
    assert state.cumulative is False
    pos, tier, state = step(state, A, 1_000, 1201.0)
    assert (pos, tier) == (1_000, PositionTier.REPORTED)


def test_source_switching_to_track_relative_mid_track_is_believed_again():
    # Seen live: the player counted the stream for one track, then
    # started counting the track itself without a track change in
    # between. Read as a seek, that 700-second jump backwards put the
    # clock's zero in the future and the time vanished.
    state = PositionState()
    _, _, state = step(state, B, 690_000, 1000.0, duration_ms=0)
    _, tier, state = step(state, C, 700_000, 1035.0, duration_ms=0)
    assert (tier, state.cumulative) == (PositionTier.COMPUTED, True)
    pos, tier, state = step(state, C, 500, 1040.0, duration_ms=158_000)
    assert (pos, tier) == (500, PositionTier.REPORTED)
    assert state.cumulative is False
    pos, tier, state = step(state, C, 10_500, 1050.0, duration_ms=158_000)
    assert (pos, tier) == (10_500, PositionTier.REPORTED)


def test_a_seek_can_never_put_the_track_start_in_the_future():
    state = PositionState()
    _, _, state = step(state, B, 690_000, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 700_000, 1035.0, duration_ms=0)
    # A jump backwards larger than everything played so far.
    pos, tier, state = step(state, C, 3_000, 1036.0, duration_ms=0)
    assert pos is not None
    assert pos >= 0


def test_resuming_from_a_long_pause_is_not_read_as_a_freeze():
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    for now in (1001.0, 1030.0, 1060.0):
        _, tier, state = step(state, A, 30_000, now, playing=False)
        assert tier is PositionTier.REPORTED
    # Resumed after 60 s — far longer than the 4 s stall window, but the
    # source was never expected to move during it.
    pos, tier, state = step(state, A, 30_200, 1060.5)
    assert (pos, tier) == (30_200, PositionTier.REPORTED)


def test_a_length_that_grows_underneath_the_track_latches_the_source():
    # Apple Music's `mpris:length` tracks how far its stream has
    # buffered: measured live, it grew by 135 s across 144 s of playback
    # on one unchanging track. A track's length does not do that, so
    # this catches the source out without waiting for a track change —
    # which matters for a session that starts mid-song, where there is
    # nothing else to go on.
    state = PositionState()
    pos, tier, state = step(state, A, 454_260, 1000.0, duration_ms=632_166)
    assert tier is PositionTier.REPORTED  # nothing says otherwise yet
    pos, tier, state = step(state, A, 464_260, 1010.0, duration_ms=642_000)
    assert state.cumulative is True
    # No witnessed start to count from, so the honest answer is nothing.
    assert (pos, tier) == (None, PositionTier.UNKNOWN)


def test_a_stable_length_is_left_alone():
    state = PositionState()
    _, _, state = step(state, A, 1_000, 1000.0, duration_ms=180_000)
    for i in range(1, 10):
        pos, tier, state = step(state, A, 1_000 + i * 500, 1000.0 + i * 0.5, duration_ms=180_000)
    assert state.cumulative is False
    assert tier is PositionTier.REPORTED


def test_a_length_arriving_late_is_not_growth():
    # Metadata often lands a poll after the track change.
    state = PositionState()
    _, _, state = step(state, A, 0, 1000.0, duration_ms=0)
    _, tier, state = step(state, A, 500, 1000.5, duration_ms=180_000)
    assert state.cumulative is False
    assert tier is PositionTier.REPORTED


def test_microsecond_rounding_is_not_growth():
    state = PositionState()
    _, _, state = step(state, A, 0, 1000.0, duration_ms=180_000)
    _, _, state = step(state, A, 500, 1000.5, duration_ms=180_400)
    assert state.cumulative is False


# ---------------------------------------- when the two lengths disagree


def test_a_disputed_length_hides_a_track_we_did_not_see_start():
    # Startup mid-track: position 5:39, the source says the thing is
    # 10:03 long, the catalog says the song is 3:46. Both readings are
    # internally consistent — a wrong catalog match, or a stream — and
    # nothing yet distinguishes them. Guessing renders a confident wrong
    # answer either way.
    state = PositionState()
    pos, tier, state = step(
        state, A, 339_054, 1000.0, duration_ms=0, length_ms=603_153, duration_disputed=True
    )
    assert (pos, tier) == (None, PositionTier.UNKNOWN)


def test_a_disputed_length_is_fine_once_we_saw_the_track_start():
    # The wrong-catalog-match case: the track began at zero under our
    # own eyes, so the source's position needs no corroboration.
    state = PositionState()
    pos, tier, state = step(state, A, 0, 1000.0, duration_ms=164_041)
    assert tier is PositionTier.REPORTED
    assert state.track_relative is True
    pos, tier, state = step(state, A, 90_000, 1090.0, duration_ms=164_041, duration_disputed=True)
    assert (pos, tier) == (90_000, PositionTier.REPORTED)


def test_an_absent_length_is_not_a_dispute():
    # Bluetooth AVRCP reports no length at all. Nothing contradicts the
    # source, so its position still counts.
    state = PositionState()
    pos, tier, state = step(state, A, 45_000, 1000.0, duration_ms=0, length_ms=0)
    assert (pos, tier) == (45_000, PositionTier.REPORTED)


def test_a_mid_track_frame_switch_also_proves_the_source_counts_tracks():
    """Taking the latch off is only half the answer.

    `cumulative` says "this source's numbers belong to a stream";
    `track_relative` is the positive evidence of the opposite, and the
    daemon consults it before it will publish a length at all. Clearing
    the first without setting the second left the source in limbo: the
    frame switch was believed for the position but the track's total
    stayed hidden until the next track change.
    """
    state = PositionState()
    _, _, state = step(state, B, 690_000, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 700_000, 1035.0, duration_ms=0)
    assert state.track_relative is False
    _, _, state = step(state, C, 500, 1040.0, duration_ms=158_000)
    assert (state.cumulative, state.track_relative) == (False, True)


# ------------------------------------------- the freshness window itself


def test_a_zero_stall_window_never_calls_the_source_stale():
    """`position_stall_s = 0` is documented as disabling the check."""
    from refrain.timing import source_position_is_fresh

    # Frozen for an hour, and still fresh: there is no window to fall out of.
    assert source_position_is_fresh(1000.0, 4600.0, 0) is True
    assert source_position_is_fresh(1000.0, 4600.0, -1) is True
    # With a window, the ordinary rule applies.
    assert source_position_is_fresh(1000.0, 1003.0, 4) is True
    assert source_position_is_fresh(1000.0, 1005.0, 4) is False


# ------------------------------- a source that counts media segments


def test_a_source_that_resets_every_few_seconds_is_not_switching_frames():
    """Plasma's browser integration reports the *segment*, not the track.

    Measured live on Apple Music's web player: position ran 0.5 s, 2.6 s,
    1.1 s, 3.2 s, 5.0 s, 0 s … while `mpris:length` moved between 8433,
    9999 and 11033 ms — both describing whichever media segment the page
    had buffered. Each shifting length latched the source as
    stream-relative and each return to zero was read as the player
    changing frames, which unlatched it and re-anchored the clock. The
    elapsed time fell back to the start every few seconds, all song long.

    A frame switch happens once and sticks. Repeated resets are a source
    that does not describe tracks at all, and our own clock — anchored on
    a track start we watched — outranks it.
    """
    # Refrain was running with nothing playing; then the track starts, and
    # the source resets for it: a real anchor. The value itself is a
    # segment's, so it comes from our clock — which reads the same here,
    # having just been placed by it.
    _, _, state = step(PositionState(), "", 0, 999.5)
    pos, tier, state = step(state, A, 1_591, 1000.0, duration_ms=157_594, length_ms=13_899)
    assert (pos, tier) == (1_591, PositionTier.COMPUTED)
    assert (state.anchored, state.track_relative) == (True, True)

    # Segment lengths and positions cycling underneath the same track.
    reported = [(2.0, 5_043, 8_433), (4.0, 0, 9_999), (6.0, 2_645, 9_999), (8.0, 1_059, 11_033)]
    for offset, ms, length in reported:
        pos, tier, state = step(
            state, A, ms, 1000.0 + offset, duration_ms=157_594, length_ms=length
        )

    assert state.cumulative is True, "a length that keeps moving is not a track's length"
    assert tier is PositionTier.COMPUTED, "our own clock should have taken over"
    assert pos is not None

    # And it must keep climbing rather than snapping back to the source.
    previous = pos
    for offset, ms, length in [(10.0, 3_184, 8_433), (12.0, 0, 9_999), (14.0, 1_315, 9_999)]:
        pos, tier, state = step(
            state, A, ms, 1000.0 + offset, duration_ms=157_594, length_ms=length
        )
        assert pos >= previous, f"elapsed went backwards: {previous} → {pos}"
        previous = pos
    assert pos >= 13_000, "our clock should be near the 14 s that actually elapsed"


SEGMENT_MS = 10_416  # measured: Plasma's browser integration, constant all song


def _segments(state, key, start_s, first_ms, seconds, *, duration_ms=215_867):
    """A segment source for ``seconds``: position runs to 10.4 s and wraps."""
    out = []
    ms = first_ms
    for i in range(seconds + 1):
        pos, tier, state = step(
            state, key, ms, start_s + i, duration_ms=duration_ms, length_ms=SEGMENT_MS
        )
        out.append((pos, tier))
        ms = (ms + 1_000) % SEGMENT_MS
    return out, state


def test_a_constant_segment_length_never_drags_the_time_back():
    """Refrain restarted mid-song, landing 0.8 s into a segment.

    The segment's length never moves, so nothing latched the source, and
    every wrap to zero was shown: the time fell back every ten seconds.
    """
    seen, _ = _segments(PositionState(), A, 1000.0, 792, 40)
    # Not even that first zero is taken: straight after a start a segment
    # source sits in a segment's first seconds far too often (twice in two
    # live restarts), and counting from there ran 24 s ahead of the song.
    assert all((pos, tier) == (None, PositionTier.UNKNOWN) for pos, tier in seen)


def test_a_restart_mid_segment_hides_the_time_rather_than_guess():
    # 5 s into a segment is no track start; nothing places the song's zero.
    seen, _ = _segments(PositionState(), A, 1000.0, 5_000, 30)
    assert all((pos, tier) == (None, PositionTier.UNKNOWN) for pos, tier in seen)


def test_a_segment_source_after_a_track_change_counts_from_the_change():
    state = PositionState()
    _, _, state = step(state, B, 120_000, 1000.0)
    seen, state = _segments(state, A, 1001.0, 300, 35)
    shown = [pos for pos, _ in seen]
    assert all(tier is PositionTier.COMPUTED for _, tier in seen)
    assert shown == sorted(shown)
    assert 34_000 <= shown[-1] <= 36_000


# ------------------------------------------ a song that begins again


def test_a_segment_source_shows_a_repeat_as_a_start_frame():
    """Measured on Apple Music in the browser, repeat-one: mid-song the
    source reports 10–16 s segments; as the song loops it reports the
    song's own 215.9 s length at position 0 for a few seconds."""
    state = PositionState()
    _, _, state = step(state, B, 120_000, 1000.0)
    seen, state = _segments(state, A, 1001.0, 300, 214)
    assert seen[-1][0] >= 213_000
    pos, tier, state = step(state, A, 400, 1216.0, duration_ms=215_867, length_ms=215_914)
    assert state.restarts == 1
    assert pos is not None and pos <= 1_000
    # Back to segments: our clock counts on from the new zero.
    seen, state = _segments(state, A, 1217.0, 800, 20)
    shown = [p for p, _ in seen]
    assert shown == sorted(shown) and 20_000 <= shown[-1] <= 22_500
    assert state.restarts == 1


def test_the_start_frames_of_a_track_change_are_not_a_repeat():
    # Apple Music reports the new song's start more than once.
    state = PositionState()
    _, _, state = step(state, B, 120_000, 1000.0)
    for i, ms in enumerate((0, 400, 900, 1_400)):
        _, _, state = step(state, A, ms, 1001.0 + i * 0.5, duration_ms=215_867, length_ms=215_914)
    assert state.restarts == 0


def test_a_start_frame_places_the_zero_after_a_restart_mid_song():
    seen, state = _segments(PositionState(), A, 1000.0, 5_000, 20)
    assert seen[-1] == (None, PositionTier.UNKNOWN)
    pos, _, state = step(state, A, 200, 1021.0, duration_ms=215_867, length_ms=215_914)
    assert (pos, state.restarts, state.anchored) == (200, 1, True)
    seen, state = _segments(state, A, 1022.0, 700, 10)
    assert all(tier is PositionTier.COMPUTED for _, tier in seen)
    assert 10_500 <= seen[-1][0] <= 12_000


def test_bluetooth_repeat_one_wraps_the_position_back_into_the_song():
    """Measured: a phone on repeat-one read 5:13 on a 3:36 song — AVRCP
    kept counting across the loop."""
    length = 215_914
    state = PositionState()
    shown = []
    for s in range(0, 260):
        pos, tier, state = step(
            state, A, s * 1_000, 1000.0 + s, duration_ms=length, loop_track=True
        )
        shown.append((pos, tier))
    assert all(tier is PositionTier.REPORTED for _, tier in shown)
    assert shown[215][0] == 215_000
    assert shown[216][0] == 216_000 - length
    assert shown[259][0] == 259_000 - length
    assert state.restarts == 1


def test_a_pause_that_blips_to_zero_is_not_a_restart():
    """Measured on an iPad: pausing at 0:43 it reported 0:00 for a tenth of
    a second, then 0:43 again — a start frame, while paused."""
    length = 215_914
    state = PositionState()
    for s in range(0, 44):
        _, _, state = step(state, A, s * 1_000, 1000.0 + s, duration_ms=length)
    _, _, state = step(state, A, 0, 1044.0, duration_ms=length, playing=False)
    _, _, state = step(state, A, 43_400, 1044.2, duration_ms=length, playing=False)
    pos, _, state = step(state, A, 43_500, 1047.0, duration_ms=length)
    assert state.restarts == 0
    assert pos == 43_500


def test_the_zero_of_a_pause_blip_is_never_passed_on_as_the_players_position():
    """Probe: the blip's 0:00 went to the history and Last.fm as the
    player's own position — which read as the song starting over."""
    length = 215_914
    state = PositionState()
    for s in range(0, 151):
        _, _, state = step(state, A, s * 1_000, 1000.0 + s, duration_ms=length)
    pos, tier, state = step(state, A, 0, 1151.0, duration_ms=length, playing=False)
    assert tier is not PositionTier.REPORTED
    assert pos == 151_000
    pos, tier, state = step(state, A, 150_400, 1151.2, duration_ms=length, playing=False)
    assert (pos, tier) == (150_400, PositionTier.REPORTED)


def test_a_start_frame_seen_while_paused_counts_once_it_plays_on():
    # The browser: its tab can read "paused" for the poll the song loops on.
    state = PositionState()
    _, _, state = step(state, B, 120_000, 1000.0)
    _, state = _segments(state, A, 1001.0, 300, 214)
    _, _, state = step(state, A, 0, 1215.5, duration_ms=215_867, length_ms=215_914, playing=False)
    assert state.restarts == 0
    pos, _, state = step(state, A, 500, 1216.0, duration_ms=215_867, length_ms=215_914)
    assert state.restarts == 1
    assert pos is not None and pos <= 1_000


def test_without_repeat_a_position_past_the_end_is_still_not_believed():
    state = PositionState()
    for s in range(0, 230):
        pos, tier, state = step(state, A, s * 1_000, 1000.0 + s, duration_ms=215_914)
    assert tier is not PositionTier.REPORTED
    assert state.restarts == 0


def test_one_genuine_frame_switch_is_still_believed():
    """The Apple Music case the switch was written for must keep working.

    There the source carried its position across the track change, so we
    never saw this track start — the frame switch is the first real zero
    we get, and taking it is strictly better than not.
    """
    state = PositionState()
    _, _, state = step(state, B, 690_000, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 700_000, 1035.0, duration_ms=0)
    assert state.cumulative is True
    assert start_is_witnessed(state) is False
    pos, tier, state = step(state, C, 500, 1040.0, duration_ms=158_000)
    assert (pos, tier) == (500, PositionTier.REPORTED)
    # Now we do know where the track began, so a later return to zero is
    # the source misbehaving, not another frame switch.
    assert start_is_witnessed(state) is True
    pos, tier, state = step(state, C, 10_500, 1050.0, duration_ms=158_000)
    assert (pos, tier) == (10_500, PositionTier.REPORTED)


def test_a_new_track_asks_the_question_again():
    """Knowing where one track began says nothing about the next."""
    state = PositionState()
    _, _, state = step(state, B, 690_000, 1000.0, duration_ms=0)
    _, _, state = step(state, C, 700_000, 1035.0, duration_ms=0)
    _, _, state = step(state, C, 500, 1040.0, duration_ms=158_000)
    assert start_is_witnessed(state) is True
    # The next change carries the position across again: unknown start.
    _, _, state = step(state, A, 900_000, 1200.0, duration_ms=0)
    assert start_is_witnessed(state) is False
