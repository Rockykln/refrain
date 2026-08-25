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


def step(state, key, reported_ms, now, *, duration_ms=DUR, playing=True, **kw):
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
