"""Scrobbler state machine: accrual, finalize, gating, lifecycle.

The Scrobbler enqueues synchronously under its lock (network work is
executor-offloaded), so asserting on the on-disk queue is deterministic
without touching threads. A FakeClient stands in for LastfmClient so
nothing hits the network; where now-playing / drain matter the
executor is flushed explicitly.
"""

from __future__ import annotations

from refrain.config import LastfmConfig
from refrain.scrobble import LastfmError, Scrobbler, accrue_play_ms
from refrain.scrobble_queue import ScrobbleQueue
from refrain.sources.base import PlaybackStatus, TrackInfo

# --------------------------------------------------------------------------- #
# accrue_play_ms (pure)                                                        #
# --------------------------------------------------------------------------- #


def test_accrue_first_observation_credits_nothing():
    assert accrue_play_ms(0, None, True, 100.0) == (0, 100.0)


def test_accrue_counts_only_while_playing():
    played, last = accrue_play_ms(0, 100.0, True, 102.0)
    assert played == 2000 and last == 102.0
    played, last = accrue_play_ms(played, last, False, 110.0)  # paused gap
    assert played == 2000 and last == 110.0


def test_accrue_clamps_suspend_gap():
    # A 5-minute single-tick jump = machine asleep, not listening.
    assert accrue_play_ms(1000, 100.0, True, 400.0) == (1000, 400.0)


def test_accrue_ignores_backwards_clock_defensively():
    assert accrue_play_ms(500, 100.0, True, 99.0) == (500, 99.0)


# --------------------------------------------------------------------------- #
# Scrobbler                                                                    #
# --------------------------------------------------------------------------- #


class FakeClient:
    def __init__(self):
        self.now_playing: list[tuple] = []
        self.scrobbled: list[dict] = []
        self.session_key = "SK"
        self.raise_invalid = False

    def update_now_playing(self, artist, track, album="", duration_s=0):
        self.now_playing.append((artist, track, album, duration_s))

    def scrobble(self, batch):
        if self.raise_invalid:
            raise LastfmError("Last.fm error 9: Invalid session key", code=9)
        self.scrobbled.extend(batch)
        return len(batch)


def _cfg(**kw):
    base = {
        "enabled": True,
        "api_key": "K",
        "shared_secret": "S",
        "session_key": "SK",
        "username": "alice",
        "scrobble_now_playing": True,
    }
    base.update(kw)
    return LastfmConfig(**base)


def _scrobbler(tmp_path, cfg=None, client=None):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(cfg or _cfg(), queue=q)
    sc._client = FakeClient() if client is None else client
    return sc, q


def _t(title, status=PlaybackStatus.PLAYING, artist="Art", album="Alb"):
    return TrackInfo(
        source="mpris",
        title=title,
        artist=artist,
        album=album,
        duration_ms=200_000,
        position_ms=0,
        status=status,
    )


def _play(sc, track, eff_dur, *, seconds, start_mono, start_wall, privacy_off=False, step=2.0):
    """Feed ticks covering `seconds` of playback for `track`."""
    mono, wall = start_mono, start_wall
    n = max(1, int(seconds / step))
    for _ in range(n + 1):
        sc.update(track, eff_dur, privacy_off=privacy_off, now_wall=wall, now_mono=mono)
        mono += step
        wall += step
    return mono, wall


def test_qualifying_track_queued_on_switch(tmp_path):
    sc, q = _scrobbler(tmp_path)
    mono, wall = _play(sc, _t("A"), 200_000, seconds=110, start_mono=1000.0, start_wall=1_700_000_000)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    pending = q.pending()
    assert len(pending) == 1
    assert pending[0]["artist"] == "Art"
    assert pending[0]["track"] == "A"
    assert pending[0]["duration"] == 200
    assert pending[0]["timestamp"] == 1_700_000_000


def test_short_play_not_queued(tmp_path):
    sc, q = _scrobbler(tmp_path)
    mono, wall = _play(sc, _t("A"), 200_000, seconds=20, start_mono=1000.0, start_wall=1_700_000_000)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    assert len(q) == 0


def test_preview_clip_never_scrobbled(tmp_path):
    sc, q = _scrobbler(tmp_path)
    # 20 s effective duration → below the 30 s floor, never a candidate.
    mono, wall = _play(sc, _t("Clip"), 20_000, seconds=60, start_mono=1000.0, start_wall=1_700_000_000)
    sc.update(_t("Next"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    assert len(q) == 0


def test_privacy_off_drops_in_progress(tmp_path):
    sc, q = _scrobbler(tmp_path)
    _play(sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000)
    # Privacy flips to Off mid-track, then track changes.
    sc.update(_t("A"), 200_000, privacy_off=True, now_wall=1_700_000_130, now_mono=1131.0)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=1_700_000_132, now_mono=1133.0)
    assert len(q) == 0


def test_disabled_client_does_not_scrobble(tmp_path):
    sc, q = _scrobbler(tmp_path)
    sc._client = None  # not connected / scrobbling disabled
    _play(sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=1_700_000_200, now_mono=1200.0)
    assert len(q) == 0


def test_reconfigure_drops_in_progress(tmp_path):
    sc, q = _scrobbler(tmp_path)
    _play(sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000)
    sc.reconfigure(_cfg(username="bob"))  # account/settings changed
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=1_700_000_200, now_mono=1200.0)
    assert len(q) == 0


def test_shutdown_banks_qualifying_track(tmp_path):
    sc, q = _scrobbler(tmp_path)
    _play(sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000)
    sc.shutdown()  # quit mid-listen
    assert [p["track"] for p in q.pending()] == ["A"]


def test_now_playing_sent_once_per_track(tmp_path):
    fake = FakeClient()
    sc, _q = _scrobbler(tmp_path, client=fake)
    _play(sc, _t("A"), 200_000, seconds=10, start_mono=1000.0, start_wall=1_700_000_000)
    sc._executor.shutdown(wait=True)  # flush async now-playing sends
    assert fake.now_playing
    artists = {np[0] for np in fake.now_playing}
    titles = {np[1] for np in fake.now_playing}
    assert artists == {"Art"} and titles == {"A"}
    # Exactly one now-playing for the single track key.
    assert len(fake.now_playing) == 1


def test_invalid_session_latches_and_keeps_queue(tmp_path):
    fake = FakeClient()
    fake.raise_invalid = True
    sc, q = _scrobbler(tmp_path, client=fake)
    mono, wall = _play(sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.shutdown(wait=True)  # let the drain attempt run
    assert sc.session_invalid is True
    assert len(q) == 1  # scrobble kept for retry after reconnect
