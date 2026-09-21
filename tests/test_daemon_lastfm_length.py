"""Last.fm's track.getInfo as a second opinion on a length measured from one whole play."""

from __future__ import annotations

import logging
import urllib.parse

import pytest

pytest.importorskip("PySide6")

from refrain import daemon, scrobble  # noqa: E402
from refrain.config import LastfmConfig  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from tests.daemon_fakes import FakeRPC, Player, install, make_config  # noqa: E402
from tests.test_scrobble import _resp  # noqa: E402

ARTIST, TITLE, ALBUM = "Mara Keel", "Paper Satellites", "Tidal"
OTHER = "Silk Road Radio"
LASTFM = {"enabled": True, "api_key": "12345apikey"}


class FakeLastfm:
    """Stands in for ws.audioscrobbler.com: answers track.getInfo per title."""

    def __init__(self, durations: dict[str, int]):
        self.durations = durations
        self.asked: list[dict[str, str]] = []

    def __call__(self, request, *args, **kwargs):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))
        self.asked.append(query)
        ms = self.durations.get(query["track"], 0)
        return _resp({"track": {"name": query["track"], "duration": str(ms)}})

    def titles(self) -> list[str]:
        return [q["track"] for q in self.asked]


@pytest.fixture
def rig(monkeypatch):
    workers = []

    def build(durations: dict[str, int], **sections):
        clock, _ = install(monkeypatch)
        monkeypatch.setattr(daemon, "Scrobbler", scrobble.Scrobbler)
        lastfm = FakeLastfm(durations)
        monkeypatch.setattr(scrobble.urllib.request, "urlopen", lastfm)
        sections = {"behavior": {"cover_art": False}, "lastfm": LASTFM} | sections
        worker = DaemonWorker(make_config(**sections))
        workers.append(worker)
        return worker, Player(worker, clock), lastfm

    yield build
    for worker in workers:
        worker._scrobbler.shutdown()


def settle(worker) -> None:
    worker._scrobbler._executor.submit(lambda: None).result(timeout=5)


def play_whole(player, title, seconds):
    player.play(title=title, artist=ARTIST, album=ALBUM, duration_ms=0)
    player.tick(seconds=1, n=seconds)


def start(player, worker):
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, duration_ms=0)
    player.tick()
    settle(worker)
    return FakeRPC.instances[-1].last[1]


def test_an_agreeing_lastfm_length_confirms_the_first_measured_play(rig):
    worker, player, lastfm = rig({TITLE: 151_000})
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert worker._song_lengths.get_ms(ARTIST, TITLE, ALBUM) == 150_000
    payload = start(player, worker)
    assert payload["end"] - payload["start"] == 150
    query = lastfm.asked[0]
    assert query["method"] == "track.getInfo"
    assert (query["artist"], query["track"], query["api_key"]) == (ARTIST, TITLE, "12345apikey")
    assert "api_sig" not in query and "sk" not in query


def test_a_disagreeing_lastfm_length_changes_nothing_and_is_logged_once(rig, caplog):
    worker, player, lastfm = rig({TITLE: 210_000})
    with caplog.at_level(logging.DEBUG, logger="refrain.daemon"):
        play_whole(player, TITLE, 150)
        play_whole(player, OTHER, 60)
        settle(worker)
        payload = start(player, worker)
    assert lastfm.titles() == [TITLE, OTHER]
    assert worker._song_lengths.get_ms(ARTIST, TITLE, ALBUM) == 0
    assert "end" not in payload
    assert caplog.text.count("Last.fm gives Mara Keel — Paper Satellites as 3:30") == 1


def test_a_song_with_an_apple_length_is_never_looked_up(rig):
    worker, player, lastfm = rig({TITLE: 150_000, OTHER: 60_000}, behavior={"cover_art": True})
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 150_000
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    play_whole(player, TITLE, 150)
    settle(worker)
    assert lastfm.titles() == [OTHER]


def test_nothing_is_looked_up_while_lastfm_is_off(rig):
    worker, player, lastfm = rig({TITLE: 150_000, OTHER: 60_000}, lastfm={"enabled": False})
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert lastfm.asked == []
    worker._scrobbler.reconfigure(LastfmConfig(**LASTFM))
    play_whole(player, TITLE, 150)
    settle(worker)
    assert lastfm.titles() == [OTHER]


def test_nothing_is_looked_up_with_privacy_off(rig):
    worker, player, lastfm = rig({TITLE: 150_000, OTHER: 60_000}, privacy={"mode": "off"})
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert lastfm.asked == []
    worker._config.privacy = make_config().privacy
    play_whole(player, TITLE, 150)
    settle(worker)
    assert lastfm.titles() == [OTHER]


def test_a_song_is_looked_up_once_and_the_answer_confirms_a_later_play(rig):
    worker, player, lastfm = rig({TITLE: 210_000})
    play_whole(player, TITLE, 150)  # skipped in the player, 1:00 early
    play_whole(player, OTHER, 60)
    settle(worker)
    assert worker._song_lengths.get_ms(ARTIST, TITLE, ALBUM) == 0
    play_whole(player, TITLE, 210)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert lastfm.titles().count(TITLE) == 1
    assert worker._song_lengths.get_ms(ARTIST, TITLE, ALBUM) == 210_000


def test_the_answer_outlives_a_restart(rig):
    worker, player, lastfm = rig({TITLE: 0})
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert lastfm.titles() == [TITLE]
    worker, player, lastfm = rig({TITLE: 0})
    play_whole(player, TITLE, 140)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert TITLE not in lastfm.titles()


def test_a_lastfm_length_of_zero_confirms_nothing(rig):
    worker, player, lastfm = rig({TITLE: 0})
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert lastfm.titles() == [TITLE]
    assert worker._song_lengths.get_ms(ARTIST, TITLE, ALBUM) == 0
    assert "end" not in start(player, worker)


def test_an_unreachable_lastfm_is_not_taken_for_an_answer(rig, monkeypatch):
    worker, player, lastfm = rig({TITLE: 150_000})

    def offline(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", offline)
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    assert worker._song_lengths.wants_reference(ARTIST, TITLE, ALBUM) is False
    assert worker._song_lengths._references == {}


def test_track_getinfo_needs_no_secret_and_reads_the_duration(monkeypatch):
    seen = []

    def fake(request, *args, **kwargs):
        seen.append(request.full_url)
        return _resp({"track": {"duration": "187000"}})

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", fake)
    assert scrobble.LastfmClient("12345apikey", "").track_duration_ms(ARTIST, TITLE) == 187_000
    assert "method=track.getInfo" in seen[0] and "api_sig" not in seen[0]


@pytest.mark.parametrize("track", [{}, {"duration": "n/a"}, None, "12345"])
def test_track_getinfo_without_a_usable_duration_is_zero(monkeypatch, track):
    monkeypatch.setattr(
        scrobble.urllib.request, "urlopen", lambda *a, **kw: _resp({"track": track})
    )
    assert scrobble.LastfmClient("12345apikey", "").track_duration_ms(ARTIST, TITLE) == 0


def test_track_not_found_is_remembered_as_no_length(rig, monkeypatch):
    worker, player, _ = rig({})
    not_found = {"error": 6, "message": "Track not found"}
    monkeypatch.setattr(scrobble.urllib.request, "urlopen", lambda *a, **kw: _resp(not_found))
    play_whole(player, TITLE, 150)
    play_whole(player, OTHER, 60)
    settle(worker)
    key = next(iter(worker._song_lengths._references))
    assert worker._song_lengths._references == {key: 0}
