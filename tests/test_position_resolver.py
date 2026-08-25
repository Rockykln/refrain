"""resolve_position — the three-tier position decision.

Tier 1 is what the source reports, tier 2 is our own clock anchored at a
track start we witnessed, tier 3 is nothing at all. The numbers in the
queue-cumulative tests come from a live session log: "Blessings" starts
at 234299 ms on Apple Music's queue timeline and the change to "Dior"
lands at 454260 ms — one poll tick after 234299 plus its 219650 ms
catalog length.
"""

from __future__ import annotations

from refrain.timing import PositionState, PositionTier, resolve_position

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


def test_no_track_resets_everything():
    state = PositionState()
    _, _, state = step(state, A, 30_000, 1000.0)
    pos, tier, state = step(state, "", 0, 1001.0)
    assert pos is None
    assert tier is PositionTier.UNKNOWN
    assert state == PositionState()


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
