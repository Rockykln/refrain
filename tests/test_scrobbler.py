"""Scrobbler state machine: accrual, finalize, gating, lifecycle.
Enqueueing is synchronous, so the on-disk queue can be checked without waiting on threads."""

from __future__ import annotations

import logging

import pytest

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
    sc = Scrobbler(cfg or _cfg(), queue=q, current_path=tmp_path / "current.json")
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
    # Offline, so no drain empties the queue before it is looked at.
    sc, q = _scrobbler(tmp_path, client=_OfflineClient())
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=110, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    pending = q.pending()
    assert len(pending) == 1
    assert pending[0]["artist"] == "Art"
    assert pending[0]["track"] == "A"
    assert pending[0]["duration"] == 200
    assert pending[0]["timestamp"] == 1_700_000_000


def test_a_song_that_starts_over_is_scrobbled_again(tmp_path):
    """Repeat-one: heard to the end, then from the top — two plays."""
    sc, q = _scrobbler(tmp_path)
    mono, wall = 1000.0, 1_700_000_000.0
    for seconds in (196, 110):
        for pos in range(0, seconds + 1, 2):
            sc.update(
                _t("A"),
                200_000,
                privacy_off=False,
                now_wall=wall,
                now_mono=mono,
                position_ms=pos * 1000,
            )
            mono += 2
            wall += 2
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.submit(lambda: None).result()  # let a drain finish
    sent = [s["track"] for s in sc._client.scrobbled]
    assert sorted(sent + [p["track"] for p in q.pending()]) == ["A", "A"]


def test_a_restart_the_player_shows_is_scrobbled_again(tmp_path):
    sc, q = _scrobbler(tmp_path)
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=196, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("A"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono, restarted=True)
    mono, wall = _play(sc, _t("A"), 200_000, seconds=110, start_mono=mono + 2, start_wall=wall + 2)
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.submit(lambda: None).result()
    sent = [s["track"] for s in sc._client.scrobbled]
    assert sorted(sent + [p["track"] for p in q.pending()]) == ["A", "A"]


def test_an_unknown_position_never_splits_a_play(tmp_path):
    sc, q = _scrobbler(tmp_path)
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=300, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.submit(lambda: None).result()
    sent = [s["track"] for s in sc._client.scrobbled]
    assert sent + [p["track"] for p in q.pending()] == ["A"]


# ------------------------------------------------------ across a restart

T0 = 1_700_000_000


class _OfflineClient(FakeClient):
    """Last.fm unreachable, so every scrobble stays in the queue to be counted."""

    def scrobble(self, batch):
        raise LastfmError("offline")


def _run(sc, track, seconds, clock, *, eff=200_000, start_pos=None, step=2.0):
    """``seconds`` of playback; ``clock`` is [wall, mono] and moves on."""
    pos = start_pos
    for _ in range(int(seconds / step) + 1):
        sc.update(
            track,
            eff,
            privacy_off=False,
            now_wall=clock[0],
            now_mono=clock[1],
            position_ms=None if pos is None else int(pos * 1000),
        )
        clock[0] += step
        clock[1] += step
        if pos is not None:
            pos += step


def _launch(tmp_path):
    sc, _ = _scrobbler(tmp_path, client=_OfflineClient())
    return sc


def _relaunch(tmp_path, sc, clock, away_s=3.0):
    sc.shutdown()
    clock[0] += away_s
    clock[1] += away_s
    return _launch(tmp_path)


def _queued(tmp_path):
    return [
        (p["track"], p["timestamp"]) for p in ScrobbleQueue(path=tmp_path / "q.jsonl").pending()
    ]


def _next_song(sc, clock):
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=clock[0], now_mono=clock[1])


def test_a_restart_mid_song_never_scrobbles_it_twice(tmp_path):
    """A ten-minute song restarted at 5:00 is queued once, not again after the restart."""
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("Long"), 300, clock, eff=600_000)
    sc = _relaunch(tmp_path, sc, clock)
    assert _queued(tmp_path) == [("Long", T0)], "queued on quit: never lost"
    _run(sc, _t("Long"), 296, clock, eff=600_000)
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("Long", T0)]


def test_a_restart_before_it_counted_carries_the_count_on(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 60, clock)
    sc = _relaunch(tmp_path, sc, clock)
    assert _queued(tmp_path) == []
    _run(sc, _t("A"), 60, clock)  # 60 + 60 s of a 200 s song: past half
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("A", T0)], "one scrobble, at the time it began"


def test_a_crash_after_it_counted_still_scrobbles_it(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 110, clock)  # counted; progress saved at 30/60/90 s — then a crash
    assert (tmp_path / "current.json").stat().st_mode & 0o777 == 0o600
    after = _launch(tmp_path)
    _run(after, _t("B"), 4, clock)  # A ended while Refrain was down
    assert _queued(tmp_path) == [("A", T0)]
    assert not (tmp_path / "current.json").exists()


def test_a_crash_before_it_counted_leaves_nothing(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 40, clock)
    after = _launch(tmp_path)
    _run(after, _t("B"), 4, clock)
    assert _queued(tmp_path) == []


def test_a_song_that_started_over_during_the_restart_is_a_new_play(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 150, clock, start_pos=0)
    sc = _relaunch(tmp_path, sc, clock, away_s=60)
    second = int(clock[0])
    _run(sc, _t("A"), 110, clock, start_pos=10)  # back at 0:10 — it began again
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("A", T0), ("A", second)]


def test_the_same_song_long_after_is_a_new_play(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 60, clock)
    sc = _relaunch(tmp_path, sc, clock, away_s=3600)
    second = int(clock[0])
    _run(sc, _t("A"), 110, clock)
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("A", second)]


def test_applying_settings_that_leave_last_fm_alone_keeps_the_play(tmp_path):
    """Settings → Apply without Last.fm changes keeps the running count of the song."""
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 60, clock)
    offline = sc._client
    sc.reconfigure(_cfg(scrobble_now_playing=False))  # same account, one option changed
    assert sc._client is offline, "nothing about Last.fm changed; nothing rebuilt"
    _run(sc, _t("A"), 50, clock)  # 60 + 50 s of 200: past half
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("A", T0)]


def test_switching_account_drops_the_play_in_progress(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 60, clock)
    sc.reconfigure(_cfg(session_key="OTHER", username="bob"))
    sc._client = _OfflineClient()  # the rebuilt client would reach the network
    _run(sc, _t("A"), 50, clock)
    _next_song(sc, clock)
    assert _queued(tmp_path) == [], "not scrobbled under the new account"


def _nothing(sc, clock, polls=2):
    """Polls that see no song yet — the tab not back, a D-Bus hiccup."""
    for _ in range(polls):
        sc.update(
            TrackInfo(source="mpris"), 0, privacy_off=False, now_wall=clock[0], now_mono=clock[1]
        )
        clock[0] += 2.0
        clock[1] += 2.0


def test_a_restart_whose_first_polls_see_nothing_still_carries_on(tmp_path):
    """An empty first poll after a restart keeps the saved play."""
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("Long"), 250, clock, eff=600_000, start_pos=0)
    sc = _relaunch(tmp_path, sc, clock)
    _nothing(sc, clock)
    _run(sc, _t("Long"), 250, clock, eff=600_000, start_pos=258)
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("Long", T0)]


def test_a_restart_before_it_counted_survives_an_empty_first_poll(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 60, clock, start_pos=0)
    sc = _relaunch(tmp_path, sc, clock)
    _nothing(sc, clock)
    _run(sc, _t("A"), 60, clock, start_pos=68)  # 60 + 60 s of a 200 s song
    _next_song(sc, clock)
    assert _queued(tmp_path) == [("A", T0)]


def test_quitting_before_the_first_poll_keeps_the_saved_play(tmp_path):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    _run(sc, _t("A"), 110, clock)  # counted, then a crash
    _launch(tmp_path).shutdown()  # started and quit before any poll
    after = _launch(tmp_path)
    _run(after, _t("B"), 4, clock)
    assert _queued(tmp_path) == [("A", T0)]


def test_quitting_never_waits_on_last_fm(tmp_path):
    sent = []

    class Recording(FakeClient):
        def scrobble(self, batch):
            sent.extend(batch)
            return len(batch)

    clock = [float(T0), 1000.0]
    sc, q = _scrobbler(tmp_path, client=_OfflineClient())
    _run(sc, _t("Long"), 250, clock, eff=600_000)
    sc._client = Recording()
    sc.shutdown()  # queues the play, which counts — and sends nothing
    sc._executor.shutdown(wait=True)
    assert sent == []
    assert [p["track"] for p in q.pending()] == ["Long"]


def test_plays_during_an_invalid_session_wait_in_the_queue(tmp_path):
    """The log promises they submit after reconnecting; they were dropped."""
    fake = FakeClient()
    sc, q = _scrobbler(tmp_path, client=fake)
    sc._session_invalid = True
    clock = [float(T0), 1000.0]
    _run(sc, _t("A"), 110, clock)
    _run(sc, _t("C"), 110, clock)
    _next_song(sc, clock)
    sc._executor.shutdown(wait=True)
    assert [p["track"] for p in q.pending()] == ["A", "C"]
    assert fake.now_playing == [] and fake.scrobbled == []


def test_the_queue_is_owner_only(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    assert q.enqueue({"artist": "Art", "track": "A", "timestamp": T0})
    assert (tmp_path / "q.jsonl").stat().st_mode & 0o777 == 0o600


def test_a_new_account_is_never_sent_the_old_ones_plays(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    q.enqueue({"artist": "Art", "track": "Alice's", "timestamp": T0, "account": "alice"})
    q.enqueue({"artist": "Art", "track": "Unowned", "timestamp": T0 + 1})
    q.enqueue({"artist": "Art", "track": "Bob's", "timestamp": T0 + 2, "account": "Bob"})
    fake = FakeClient()
    sc = Scrobbler(_cfg(username="bob"), queue=q, current_path=tmp_path / "current.json")
    sc._client = fake
    sc._do_drain()
    assert [it["track"] for it in fake.scrobbled] == ["Unowned", "Bob's"]
    assert len(q) == 0


def test_queued_plays_remember_the_account(tmp_path):
    sc, q = _scrobbler(tmp_path, client=_OfflineClient())
    clock = [float(T0), 1000.0]
    _run(sc, _t("A"), 110, clock)
    _next_song(sc, clock)
    assert [p.get("account") for p in ScrobbleQueue(path=tmp_path / "q.jsonl").pending()] == [
        "alice"
    ]


def test_short_play_not_queued(tmp_path):
    sc, q = _scrobbler(tmp_path)
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=20, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    assert len(q) == 0


def test_preview_clip_never_scrobbled(tmp_path):
    sc, q = _scrobbler(tmp_path)
    # 20 s effective duration → below the 30 s floor, never a candidate.
    mono, wall = _play(
        sc, _t("Clip"), 20_000, seconds=60, start_mono=1000.0, start_wall=1_700_000_000
    )
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
    # reconfigure() builds a real LastfmClient from the config. Swap the
    # fake back in before the next tick, or B's "now playing" goes out
    # to Last.fm from the executor thread.
    sc._client = FakeClient()
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=1_700_000_200, now_mono=1200.0)
    assert len(q) == 0


def test_shutdown_banks_qualifying_track(tmp_path):
    sc, q = _scrobbler(tmp_path, client=_OfflineClient())
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
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.shutdown(wait=True)  # let the drain attempt run
    assert sc._session_invalid is True
    assert len(q) == 1  # scrobble kept for retry after reconnect


def test_permanently_rejected_batch_is_dropped_not_head_of_line_blocking(tmp_path):
    """Tracks Last.fm rejects must not pin the queue forever."""

    class RejectClient(FakeClient):
        def scrobble(self, batch):
            raise LastfmError("Last.fm error 6: Invalid parameters", code=6)

    sc, q = _scrobbler(tmp_path, client=RejectClient())
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.shutdown(wait=True)
    assert sc._session_invalid is False  # not a session problem
    assert len(q) == 0  # poison entry dropped, queue not blocked


@pytest.mark.parametrize("code", [8, 10, 13, 26])
def test_a_key_or_server_problem_keeps_the_queue(tmp_path, code):
    class RejectClient(FakeClient):
        def scrobble(self, batch):
            raise LastfmError(f"Last.fm error {code}", code=code)

    sc, q = _scrobbler(tmp_path, client=RejectClient())
    mono, wall = _play(
        sc, _t("A"), 200_000, seconds=120, start_mono=1000.0, start_wall=1_700_000_000
    )
    sc.update(_t("B"), 200_000, privacy_off=False, now_wall=wall, now_mono=mono)
    sc._executor.shutdown(wait=True)
    assert len(q) == 1


# --------------------------------------------------------------------------- #
# scrobble_duration_ms — what Last.fm gets when the two lengths disagree       #
# --------------------------------------------------------------------------- #


def test_an_undisputed_length_is_passed_straight_through():
    from refrain.daemon import scrobble_duration_ms

    assert scrobble_duration_ms(164_041, False, 164_041, 164_041) == 164_041
    # Including the honest zero of a source that reports no length at all.
    assert scrobble_duration_ms(0, False, 0, 0) == 0


def test_a_disputed_length_still_reaches_last_fm():
    """A disputed length shows as blank but still gives the scrobbler a length.
    A zero would fall under Last.fm's 30-second floor."""
    from refrain.daemon import scrobble_duration_ms

    # Source says 10:03 (its stream buffer), catalog says 3:46. The
    # shorter one wins: guessing long loses the scrobble outright,
    # guessing short only makes it land early on a track that really
    # was playing.
    assert scrobble_duration_ms(0, True, 603_153, 226_000) == 226_000
    assert scrobble_duration_ms(0, True, 226_000, 603_153) == 226_000
    # One candidate is enough.
    assert scrobble_duration_ms(0, True, 226_000, 0) == 226_000
    assert scrobble_duration_ms(0, True, 0, 226_000) == 226_000
    # Nothing to go on stays nothing — no length invented.
    assert scrobble_duration_ms(0, True, 0, 0) == 0


def test_shorter_wins_only_among_lengths_that_can_be_scrobbled():
    """A 14 s preview-clip length is ignored even though it is the smaller one."""
    from refrain.daemon import scrobble_duration_ms

    assert scrobble_duration_ms(0, True, 14_000, 165_832) == 165_832
    assert scrobble_duration_ms(0, True, 165_832, 14_000) == 165_832
    # A track both parties agree is genuinely too short stays too short:
    # choosing cannot rescue it, and no length is invented.
    assert scrobble_duration_ms(0, True, 14_000, 20_000) < 30_000


def test_health_counts_only_scrobbles_held_back_after_a_failed_send(tmp_path):
    sc, q = _scrobbler(tmp_path)
    assert sc.health() == (False, 0)
    q.enqueue({"artist": "Art", "track": "One", "timestamp": 1_700_000_000})
    q.enqueue({"artist": "Art", "track": "Two", "timestamp": 1_700_000_300})
    assert sc.health() == (False, 2)
    sc._drain_inflight = True
    assert sc.health() == (False, 0)
    sc._drain_inflight = False
    sc._session_invalid = True
    assert sc.health() == (True, 2)


def test_a_song_without_a_length_is_logged_instead_of_silently_skipped(tmp_path, caplog):
    clock = [float(T0), 1000.0]
    sc = _launch(tmp_path)
    caplog.set_level(logging.INFO, logger="refrain.scrobble")
    _run(sc, _t("Endless"), 60, clock, eff=0)
    _next_song(sc, clock)
    assert _queued(tmp_path) == []
    _run(sc, _t("Endless"), 40, clock, eff=0)
    _run(sc, _t("Teaser"), 40, clock, eff=25_000)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Not scrobbled")]
    # Each song is named once, and only after 30 s without a usable length.
    assert lines == [
        "Not scrobbled: Art — Endless (the player reported no song length)",
        "Not scrobbled: Art — Teaser (shorter than 30 seconds)",
    ]
    sc.shutdown()
