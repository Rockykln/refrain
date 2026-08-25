"""compensate_stalled_position — frozen-Position detection.

Covers the failure this guards against: an MPRIS source that keeps
reporting PLAYING while its `Position` property stops advancing, which
used to freeze the tray label, Discord's elapsed timer and the MPRIS
server we publish until the user seeked manually or restarted the song.
"""

from __future__ import annotations

from refrain.timing import PositionState, compensate_stalled_position

KEY = "mpris|Title|Artist|Album"
DUR = 210_000  # 3:30


def advance(state, position_ms, now, *, key=KEY, duration_ms=DUR, playing=True, **kw):
    return compensate_stalled_position(state, key, position_ms, duration_ms, playing, now, **kw)


# ------------------------------------------------------------------ baseline


def test_first_poll_anchors_and_passes_through():
    pos, state = advance(PositionState(), 30_000, 1000.0)
    assert pos == 30_000
    assert state.stalled is False
    assert state.raw_position_ms == 30_000
    assert state.anchor_at == 1000.0


def test_normally_advancing_position_is_never_touched():
    state = PositionState()
    for i in range(20):
        now = 1000.0 + i * 0.5
        pos, state = advance(state, 30_000 + i * 500, now)
        assert pos == 30_000 + i * 500
        assert state.stalled is False


def test_coarse_source_repeating_a_value_briefly_is_not_a_stall():
    # A player refreshing Position once per second reports the same
    # value twice at a 500 ms poll interval. That must not trip the check.
    state = PositionState()
    pos, state = advance(state, 30_000, 1000.0)
    pos, state = advance(state, 30_000, 1000.5)
    assert pos == 30_000
    assert state.stalled is False
    pos, state = advance(state, 31_000, 1001.0)
    assert pos == 31_000
    assert state.stalled is False


# --------------------------------------------------------------------- stall


def test_frozen_position_extrapolates_after_threshold():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    # Still inside the 4 s window — report verbatim.
    pos, state = advance(state, 60_000, 1003.0)
    assert pos == 60_000
    assert state.stalled is False
    # Past the window — clock runs from wall time.
    pos, state = advance(state, 60_000, 1005.0)
    assert pos == 65_000
    assert state.stalled is True
    pos, state = advance(state, 60_000, 1030.0)
    assert pos == 90_000
    assert state.stalled is True


def test_anchor_is_kept_while_frozen_so_extrapolation_is_continuous():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    for now in (1001.0, 1002.0, 1003.0, 1005.0, 1010.0):
        pos, state = advance(state, 60_000, now)
    # 60 s anchor + 10 s of freeze, not restarted by the intermediate polls.
    assert pos == 70_000
    assert state.anchor_at == 1000.0


def test_extrapolation_is_clamped_to_duration():
    state = PositionState()
    _, state = advance(state, 200_000, 1000.0)
    pos, state = advance(state, 200_000, 1100.0)
    assert pos == DUR


def test_unknown_duration_extrapolates_unclamped():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0, duration_ms=0)
    pos, state = advance(state, 60_000, 1100.0, duration_ms=0)
    assert pos == 160_000


# ------------------------------------------------------------------ recovery


def test_source_resuming_re_anchors_and_passes_through():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    pos, state = advance(state, 60_000, 1010.0)
    assert state.stalled is True
    # The user nudged the slider / the source woke up.
    pos, state = advance(state, 72_000, 1011.0)
    assert pos == 72_000
    assert state.stalled is False
    assert state.anchor_at == 1011.0


def test_backward_seek_re_anchors():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    pos, state = advance(state, 5_000, 1001.0)
    assert pos == 5_000
    assert state.stalled is False


def test_track_change_resets_state():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    _, state = advance(state, 60_000, 1010.0)
    assert state.stalled is True
    pos, state = advance(state, 0, 1011.0, key="mpris|Other|Artist|Album")
    assert pos == 0
    assert state.stalled is False
    assert state.track_key == "mpris|Other|Artist|Album"


# -------------------------------------------------------------------- guards


def test_pause_is_never_extrapolated():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    pos, state = advance(state, 60_000, 1010.0, playing=False)
    assert pos == 60_000
    assert state == PositionState()


def test_empty_track_key_resets():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0)
    pos, state = advance(state, 60_000, 1010.0, key="")
    assert pos == 60_000
    assert state == PositionState()


def test_zero_threshold_disables_the_check():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0, stall_after_s=0)
    pos, state = advance(state, 60_000, 1100.0, stall_after_s=0)
    assert pos == 60_000
    assert state == PositionState()


def test_custom_threshold_is_honoured():
    state = PositionState()
    _, state = advance(state, 60_000, 1000.0, stall_after_s=20.0)
    pos, state = advance(state, 60_000, 1015.0, stall_after_s=20.0)
    assert pos == 60_000
    pos, state = advance(state, 60_000, 1025.0, stall_after_s=20.0)
    assert pos == 85_000


def test_position_stuck_at_zero_still_gets_a_running_clock():
    # Players that never implement Position report 0 forever; a wall
    # clock started at the track's first poll beats a frozen 0:00.
    state = PositionState()
    _, state = advance(state, 0, 1000.0)
    pos, state = advance(state, 0, 1030.0)
    assert pos == 30_000
    assert state.stalled is True
