"""compute_rpc_start_ts — Discord elapsed-timer correctness.

Tests that the helper behaves correctly under: new tracks, steady playback
with poll jitter, pause + resume, forward and backward seeks, the very
first call (uninitialized state), and configurable drift thresholds.
"""

from __future__ import annotations

import pytest

from refrain.timing import compute_rpc_start_ts

# ---------------------------------------------------------------------------
# Happy path: a new track always triggers a recompute.
# ---------------------------------------------------------------------------


def test_new_track_triggers_recompute():
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="",
        track_key="mpris|Title|Artist|Album",
        position_ms=5_000,
        now=1000.0,
    )
    assert changed is True
    assert start == 995  # 1000 - 5


def test_track_change_recomputes_even_with_no_drift():
    """Even if the position happens to align, a different track key wins."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=900,
        prev_track_key="mpris|Old|Artist|Album",
        track_key="mpris|New|Artist|Album",
        position_ms=10_000,
        now=910.0,  # would otherwise match the old start_ts perfectly
    )
    assert changed is True
    assert start == 900  # 910 - 10


# ---------------------------------------------------------------------------
# Steady playback: small jitter must NOT trigger recompute (avoids twitching).
# ---------------------------------------------------------------------------


def test_steady_playback_no_jitter():
    """Same track, position lines up exactly — keep the previous start_ts."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=1000,
        prev_track_key="k",
        track_key="k",
        position_ms=10_000,
        now=1010.0,
    )
    assert changed is False
    assert start == 1000


def test_sub_second_poll_jitter_is_ignored():
    """A 0.4 s drift (typical of poll-interval timing) keeps the start_ts."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=1000,
        prev_track_key="k",
        track_key="k",
        position_ms=10_000,
        now=1010.4,
    )
    assert changed is False
    assert start == 1000


def test_two_second_drift_is_ignored():
    """Drift just under the 3-second threshold stays stable."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=1000,
        prev_track_key="k",
        track_key="k",
        position_ms=10_000,
        now=1012.5,  # expected=12.5s, actual=10s, drift=2.5s
    )
    assert changed is False
    assert start == 1000


# ---------------------------------------------------------------------------
# Pause/resume: this was the user-reported bug — Discord drifted off the
# track's actual elapsed time after a pause.
# ---------------------------------------------------------------------------


def test_pause_resume_short_pause_resyncs():
    """Track played 30 s, paused 5 s, resumed. Position is still 30 s,
    wall-clock is at 35. Drift = 5 s > 3 s → recompute."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=30_000,
        now=35.0,
    )
    assert changed is True
    assert start == 5  # 35 - 30


def test_pause_resume_long_pause_resyncs():
    """Track played 30 s, paused 60 s, resumed. Position 30 s, wall 90 s."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=30_000,
        now=90.0,
    )
    assert changed is True
    assert start == 60  # 90 - 30


# ---------------------------------------------------------------------------
# Seek: same track, position jumps. Must resync so Discord doesn't show the
# wrong elapsed time.
# ---------------------------------------------------------------------------


def test_seek_forward_resyncs():
    """User seeks from 5 s to 60 s. now=5, position=60, drift=55 s."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=60_000,
        now=5.0,
    )
    assert changed is True
    assert start == -55  # implied "started 55 s in the future"; harmless to Discord


def test_seek_backward_resyncs():
    """User seeks back from 60 s to 5 s. now=60, position=5, drift=55 s."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=5_000,
        now=60.0,
    )
    assert changed is True
    assert start == 55


# ---------------------------------------------------------------------------
# Boundary conditions.
# ---------------------------------------------------------------------------


def test_threshold_boundary_not_recomputed():
    """Exactly 3 s drift: kept (we use strict > 3.0)."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=7_000,
        now=10.0,  # drift = 3.0
    )
    assert changed is False
    assert start == 0


def test_threshold_just_above_recomputes():
    """Drift just over 3 s triggers recompute."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=6_900,
        now=10.0,  # drift = 3.1
    )
    assert changed is True
    assert start == 3  # 10 - 6.9 = 3.1 → int() floors to 3


def test_initial_zero_state_treated_as_track_change():
    """Daemon starts up with prev_start_ts=0, prev_track_key=''. The first
    valid track call must recompute via the track_key path."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="",
        track_key="mpris|t|a|al",
        position_ms=0,
        now=12345.0,
    )
    assert changed is True
    assert start == 12345


def test_negative_position_is_clamped_to_zero():
    """Sources can return negative positions on glitches; treat as 0."""
    start, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=-500,
        now=10.0,
    )
    # Position is clamped to 0, so actual_position_s = 0, expected = 10.0,
    # drift = 10.0 → recompute.
    assert changed is True
    assert start == 10


# ---------------------------------------------------------------------------
# Custom drift threshold (used by future config / advanced users).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drift_threshold,position_ms,now,expected_changed",
    [
        (1.0, 11_500, 10.0, True),  # drift 1.5 > 1.0
        (5.0, 12_000, 10.0, False),  # drift 2.0 < 5.0
        (0.5, 10_300, 10.0, False),  # drift 0.3 < 0.5
        (0.5, 10_700, 10.0, True),  # drift 0.7 > 0.5
    ],
)
def test_custom_drift_threshold(drift_threshold, position_ms, now, expected_changed):
    _, changed = compute_rpc_start_ts(
        prev_start_ts=0,
        prev_track_key="k",
        track_key="k",
        position_ms=position_ms,
        now=now,
        drift_threshold_s=drift_threshold,
    )
    assert changed is expected_changed
