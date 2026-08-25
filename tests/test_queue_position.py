"""resolve_track_position — queue-cumulative Position handling.

Apple Music's web player does not restart `Position` per track: it
counts across the whole queue, so the third song of a session reports a
position far past its own length. The numbers here are taken from a live
session log (see the "Blessings" → "Dior" transition, which lands within
one poll tick of the previous track's start plus its catalog length).
"""

from __future__ import annotations

from refrain.timing import QueueOffsetState, resolve_track_position

A = "mpris|No Broke Boys|Disco Lines|"
B = "mpris|Blessings|Calvin Harris|"
C = "mpris|Dior|MK|"


def step(state, key, position_ms, duration_ms=220_000, **kw):
    return resolve_track_position(state, key, position_ms, duration_ms, **kw)


# ------------------------------------------------- normal, track-relative source


def test_source_that_resets_per_track_is_untouched():
    state = QueueOffsetState()
    pos, state = step(state, A, 0)
    assert pos == 0
    pos, state = step(state, A, 60_000)
    assert pos == 60_000
    # Next track restarts at zero — the classic MPRIS contract.
    pos, state = step(state, B, 0)
    assert pos == 0
    assert state.cumulative is False
    assert state.offset_ms == 0
    pos, state = step(state, B, 30_000)
    assert pos == 30_000


def test_track_change_noticed_at_a_small_nonzero_position_is_not_cumulative():
    # A player we catch two polls into the new track still reset, so the
    # offset must stay 0 rather than swallowing the first second.
    state = QueueOffsetState()
    _, state = step(state, A, 180_000)
    pos, state = step(state, B, 1_000)
    assert pos == 1_000
    assert state.cumulative is False


# ------------------------------------------------------ queue-cumulative source


def test_position_continuing_across_a_track_change_anchors():
    state = QueueOffsetState()
    _, state = step(state, A, 234_000)
    _, state = step(state, A, 234_500)
    # Live numbers: Blessings started at 234_299 ms on the queue
    # timeline, Dior at 454_260 — exactly one poll tick after
    # 234_299 + its 219_650 ms catalog length.
    pos, state = step(state, B, 235_000)
    assert pos == 0
    assert state.cumulative is True
    assert state.offset_ms == 235_000
    pos, state = step(state, B, 295_000)
    assert pos == 60_000


def test_anchor_survives_and_next_transition_stays_exact():
    state = QueueOffsetState()
    _, state = step(state, A, 100_000)
    _, state = step(state, B, 100_400)
    pos, state = step(state, B, 320_000)
    assert pos == 219_600
    pos, state = step(state, C, 320_400)
    assert pos == 0
    pos, state = step(state, C, 380_400)
    assert pos == 60_000


def test_cumulative_latches_so_a_late_noticed_transition_still_anchors():
    state = QueueOffsetState()
    _, state = step(state, A, 100_000)
    _, state = step(state, B, 100_400)
    assert state.cumulative is True
    # This one we notice 4 s late — outside the continuation window, but
    # the source is already known to be cumulative.
    pos, state = step(state, C, 304_400)
    assert pos == 0
    assert state.offset_ms == 304_400


def test_pause_keeps_the_anchor():
    state = QueueOffsetState()
    _, state = step(state, A, 100_000)
    _, state = step(state, B, 100_400)
    for _ in range(5):
        pos, state = step(state, B, 160_000)
    assert pos == 59_600
    pos, state = step(state, B, 160_500)
    assert pos == 60_100


# ------------------------------------------------------------- missing anchor


def test_track_running_past_its_length_gets_one_rescue_anchor():
    # Refrain started mid-song: the transition was never seen, so the
    # position runs past the track's real length with no anchor.
    state = QueueOffsetState()
    # Rescued on first sight, so the raw queue position never reaches
    # the tray even for a single poll.
    pos, state = step(state, A, 598_000, 169_000)
    assert pos == 0
    assert state.rescued is True
    pos, state = step(state, A, 628_000, 169_000)
    assert pos == 30_000


def test_rescue_fires_only_once_per_track():
    state = QueueOffsetState()
    _, state = step(state, A, 600_000, 60_000)
    assert state.rescued is True
    # A too-short catalog duration must not loop the clock 0 → end → 0.
    pos, state = step(state, A, 700_000, 60_000)
    assert pos == 100_000
    assert state.rescued is True


def test_rescue_arms_again_on_the_next_track():
    state = QueueOffsetState()
    _, state = step(state, A, 600_000, 60_000)
    _, state = step(state, B, 0, 60_000)
    assert state.rescued is False


def test_unknown_duration_never_rescues():
    state = QueueOffsetState()
    _, state = step(state, A, 600_000, 0)
    pos, state = step(state, A, 900_000, 0)
    assert pos == 900_000
    assert state.rescued is False


# -------------------------------------------------------------------- guards


def test_empty_track_key_resets_state():
    state = QueueOffsetState()
    _, state = step(state, A, 100_000)
    _, state = step(state, B, 100_400)
    assert state.cumulative is True
    pos, state = step(state, "", 0)
    assert pos == 0
    assert state == QueueOffsetState()


def test_backward_jump_at_a_track_change_is_not_a_continuation():
    state = QueueOffsetState()
    _, state = step(state, A, 200_000)
    pos, state = step(state, B, 120_000)
    assert pos == 120_000
    assert state.cumulative is False


def test_relative_position_never_goes_negative():
    state = QueueOffsetState()
    _, state = step(state, A, 100_000)
    _, state = step(state, B, 100_400)
    # User seeks back before this track's start on the queue timeline.
    pos, state = step(state, B, 90_000)
    assert pos == 0
