"""Plasma's Play / Pause / Stop reach a source that only has a toggle.

Each may toggle only when that gets it where it asks to go — a blind
toggle made Plasma's "Stop" start the music whenever it was paused.

Named to be collected before test_mpris_dispatch.py, which swaps the
``dbus`` module for a mock for the rest of the session.
"""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.sources.mpris_server import MPRISServer  # noqa: E402


def _server(status: PlaybackStatus):
    toggles: list[str] = []
    server = MPRISServer(
        on_play_pause=lambda: toggles.append("toggle"),
        on_next=lambda: None,
        on_previous=lambda: None,
    )
    server._track = TrackInfo(source="mpris", title="T", status=status)
    return server, toggles


@pytest.mark.parametrize(
    "method,status,toggled",
    [
        ("Stop", PlaybackStatus.PAUSED, False),  # the reported bug
        ("Stop", PlaybackStatus.PLAYING, True),
        ("Pause", PlaybackStatus.PAUSED, False),
        ("Pause", PlaybackStatus.PLAYING, True),
        ("Play", PlaybackStatus.PLAYING, False),
        ("Play", PlaybackStatus.PAUSED, True),
        ("Play", PlaybackStatus.STOPPED, True),
        ("PlayPause", PlaybackStatus.PAUSED, True),
        ("PlayPause", PlaybackStatus.PLAYING, True),
    ],
)
def test_toggle_only_towards_the_asked_state(method, status, toggled):
    server, toggles = _server(status)
    getattr(server, method)()
    assert bool(toggles) is toggled


def test_a_title_beyond_ascii_still_makes_a_valid_track_id():
    """Measured: "Wer weiß das schon" failed every read of Metadata."""
    from refrain.sources.mpris_server import _track_id

    for title in ("Wer weiß das schon", "Königin", "f**k dich (feat. dateツ & flippin'dope)", ""):
        path = _track_id(TrackInfo(source="mpris", title=title))
        assert path.startswith("/refrain/track/")
