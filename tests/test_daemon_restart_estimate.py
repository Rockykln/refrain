"""After a restart mid-song: the time from the history, shown as an estimate."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain import daemon, history  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.history import PlayHistory  # noqa: E402
from tests.daemon_fakes import FakeRPC, Player, install, make_config  # noqa: E402

ARTIST, TITLE = "Mara Keel", "Paper Satellites"
SONG_MS = 200_000
# A browser whose integration reports the buffered media segment, not the song.
SEGMENT_MS = 10_400
COVER = "https://is1-ssl.mzstatic.example/image/paper-lanterns.jpg"


@pytest.fixture
def restart(monkeypatch):
    """Play 65 s of a song with a working position, crash, come back ``gap_s`` later."""

    def run(gap_s: float = 3.0):
        clock, _ = install(monkeypatch)
        monkeypatch.setattr(daemon, "PlayHistory", PlayHistory)
        monkeypatch.setattr(history, "time", clock)
        before = DaemonWorker(make_config())
        before._cover_fetcher.durations[(ARTIST, TITLE)] = SONG_MS
        player = Player(before, clock)
        player.play(duration_ms=SONG_MS)
        # The history saves at 1:00 heard (the time shown is 1:00.5 then),
        # and Refrain dies 5 s after that without saving again.
        player.tick(n=131)
        clock.advance(gap_s)
        after = DaemonWorker(make_config())
        after._cover_fetcher.durations[(ARTIST, TITLE)] = SONG_MS
        after._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
        ticks, marks = [], []
        after.progressTick.connect(lambda *a: ticks.append(a))
        after.progressEstimated.connect(marks.append)
        return after, Player(after, clock), clock, ticks, marks

    return run


def test_a_source_without_a_position_shows_the_estimate_marked(restart):
    worker, player, clock, ticks, marks = restart()
    player.play(duration_ms=SONG_MS)  # reads 0 all song long
    player.frozen = True
    player.tick(n=3)
    # 1:00.5 at the save, + 5 s to the crash, + 3 s away, + 0.5 s per poll.
    assert ticks == [(69_000, SONG_MS), (69_500, SONG_MS), (70_000, SONG_MS)]
    assert marks == [True]
    assert worker._position_tier.value == "estimated"
    update = FakeRPC.instances[-1].last
    start = int(clock.wall - 70.0)
    assert update[0] == "update"
    assert (update[1]["start"], update[1]["end"]) == (start, start + 200)


def test_a_segment_source_shows_the_estimate_marked(restart):
    worker, player, _, ticks, marks = restart()
    player.play(position_ms=4_000, duration_ms=SEGMENT_MS)
    player.tick(n=2)
    assert ticks == [(69_000, SONG_MS), (69_500, SONG_MS)]
    assert marks == [True]


def test_a_real_position_wins_and_carries_no_mark(restart):
    worker, player, _, ticks, marks = restart()
    player.play(position_ms=66_000, duration_ms=SONG_MS)
    player.tick(n=2)
    assert ticks == [(66_500, SONG_MS), (67_000, SONG_MS)]
    assert marks == []


def test_the_mark_goes_once_the_source_has_a_position_again(restart):
    worker, player, _, ticks, marks = restart()
    player.play(duration_ms=SONG_MS)
    player.frozen = True
    player.tick()
    player.play(position_ms=70_000, duration_ms=SONG_MS)
    player.frozen = False
    player.tick()
    assert ticks == [(69_000, SONG_MS), (70_500, SONG_MS)]
    assert marks == [True, False]


def test_a_save_too_old_leaves_the_time_hidden(restart):
    worker, player, _, ticks, marks = restart(gap_s=60.0)
    player.play(position_ms=4_000, duration_ms=SEGMENT_MS)
    player.tick(n=2)
    assert ticks == [(-1, 0), (-1, 0)]
    assert marks == []
    assert "start" not in FakeRPC.instances[-1].last[1]


def test_another_song_leaves_the_time_hidden(restart):
    worker, player, _, ticks, marks = restart()
    worker._cover_fetcher.durations[(ARTIST, "Undertow")] = SONG_MS
    player.play(title="Undertow", position_ms=4_000, duration_ms=SEGMENT_MS)
    player.tick(n=2)
    assert ticks == [(-1, 0), (-1, 0)]
    assert marks == []


def test_an_estimate_past_the_end_of_the_song_leaves_the_time_hidden(restart):
    worker, player, _, ticks, marks = restart()
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 68_000
    player.play(position_ms=4_000, duration_ms=SEGMENT_MS)
    player.tick(n=2)
    assert ticks == [(-1, 0), (-1, 0)]
    assert marks == []


def test_the_estimate_never_reaches_last_fm_or_the_players_position(restart):
    worker, player, _, _, _ = restart()
    player.play(duration_ms=SONG_MS)
    player.frozen = True
    player.tick(n=3)
    assert {c["position_ms"] for c in worker._scrobbler.calls} == {None}
