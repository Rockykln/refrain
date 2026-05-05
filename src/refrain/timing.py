"""Pure-Python timing helpers for the daemon.

Lives in its own module — kept free of Qt and D-Bus imports — so the unit
suite can exercise it in isolation without the GUI runtime installed.
"""

from __future__ import annotations


def compute_rpc_start_ts(
    prev_start_ts: int,
    prev_track_key: str,
    track_key: str,
    position_ms: int,
    now: float,
    drift_threshold_s: float = 3.0,
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

    Returns ``(start_ts, recomputed)``.
    """
    actual_position_s = max(0, position_ms) / 1000.0
    expected_position_s = max(0.0, now - prev_start_ts)
    drift_s = abs(expected_position_s - actual_position_s)
    if track_key != prev_track_key or drift_s > drift_threshold_s:
        return int(now - actual_position_s), True
    return prev_start_ts, False
