"""An album that turns up after the song started belongs to the same play."""

from __future__ import annotations

import logging

from refrain.config import HistoryConfig, LastfmConfig
from refrain.history import PlayHistory
from refrain.scrobble import Scrobbler, fills_in_album
from refrain.scrobble_queue import ScrobbleQueue
from refrain.sources.base import PlaybackStatus, TrackInfo

ARTIST, TITLE, ALBUM = "Marlow Vance", "Paper Satellites", "Low Orbit"
SONG_MS = 200_000
T0 = 1_700_000_000.0


def song(album="", title=TITLE):
    return TrackInfo(
        source="mpris",
        title=title,
        artist=ARTIST,
        album=album,
        duration_ms=SONG_MS,
        status=PlaybackStatus.PLAYING,
    )


class Clock:
    def __init__(self):
        self.wall = T0
        self.mono = 1000.0


def feed(target, track, seconds, clock, step=2.0, **kw):
    for _ in range(int(seconds / step)):
        target.update(track, SONG_MS, now_wall=clock.wall, now_mono=clock.mono, **kw)
        clock.wall += step
        clock.mono += step


def history_lines(caplog, text):
    return [r.getMessage() for r in caplog.records if text in r.getMessage()]


def test_fills_in_album_only_for_the_same_song_that_had_none():
    key = f"mpris|{TITLE}|{ARTIST}|"
    assert fills_in_album(key, song(ALBUM))
    assert not fills_in_album(key, song(""))
    assert not fills_in_album(f"mpris|{TITLE}|{ARTIST}|Harbour Lights", song(ALBUM))
    assert not fills_in_album(f"mpris|Undertow|{ARTIST}|", song(ALBUM))
    assert not fills_in_album(None, song(ALBUM))


def test_history_takes_a_late_album_into_the_running_entry(caplog):
    caplog.set_level(logging.DEBUG, logger="refrain.history")
    h, clock = PlayHistory(HistoryConfig()), Clock()
    feed(h, song(""), 2, clock)
    feed(h, song(ALBUM), 110, clock)
    entries = h.snapshot().entries
    assert [(e.title, e.album, e.started_at) for e in entries] == [(TITLE, ALBUM, int(T0))]
    assert len(history_lines(caplog, "now playing")) == 1
    assert history_lines(caplog, "dropped") == []
    assert history_lines(caplog, "kept") == [
        f"History: kept {ARTIST} — {TITLE} (played 1:40 of 3:20)"
    ]


def test_an_album_found_after_the_song_counted_reaches_the_disk():
    h, clock = PlayHistory(HistoryConfig()), Clock()
    feed(h, song(""), 110, clock)
    assert h.snapshot().entries[0].album == ""
    feed(h, song(ALBUM), 4, clock)
    stored = PlayHistory(HistoryConfig()).snapshot().entries
    assert [(e.title, e.album) for e in stored] == [(TITLE, ALBUM)]


def test_a_song_taken_out_stays_out_when_its_album_arrives():
    h, clock = PlayHistory(HistoryConfig()), Clock()
    feed(h, song(""), 4, clock)
    assert h.remove(int(T0), TITLE, ARTIST)
    feed(h, song(ALBUM), 110, clock)
    assert h.snapshot().entries == ()


def test_history_keeps_two_albums_apart(caplog):
    caplog.set_level(logging.DEBUG, logger="refrain.history")
    h, clock = PlayHistory(HistoryConfig()), Clock()
    feed(h, song("Harbour Lights"), 4, clock)
    feed(h, song(ALBUM), 110, clock)
    assert [e.album for e in h.snapshot().entries] == [ALBUM]
    assert len(history_lines(caplog, "now playing")) == 2
    assert len(history_lines(caplog, "dropped")) == 1


class Client:
    session_key = "SK"

    def __init__(self):
        self.now_playing: list[tuple] = []
        self.scrobbled: list[dict] = []

    def update_now_playing(self, artist, track, album="", duration_s=0):
        self.now_playing.append((artist, track, album, duration_s))

    def scrobble(self, batch):
        self.scrobbled.extend(batch)
        return len(batch)


def scrobbler(tmp_path):
    cfg = LastfmConfig(
        enabled=True,
        api_key="K",
        shared_secret="S",
        session_key="SK",
        username="marlow",
        scrobble_now_playing=True,
    )
    sc = Scrobbler(
        cfg, queue=ScrobbleQueue(path=tmp_path / "q.jsonl"), current_path=tmp_path / "cur.json"
    )
    sc._client = Client()
    return sc


def sent(sc):
    sc._executor.submit(lambda: None).result()
    return sc._client


def test_the_scrobble_carries_an_album_that_arrived_late(tmp_path):
    sc, clock = scrobbler(tmp_path), Clock()
    feed(sc, song(""), 2, clock, privacy_off=False)
    feed(sc, song(ALBUM), 110, clock, privacy_off=False)
    feed(sc, song("", title="Undertow"), 2, clock, privacy_off=False)
    client = sent(sc)
    assert [(s["track"], s["album"], s["timestamp"]) for s in client.scrobbled] == [
        (TITLE, ALBUM, int(T0))
    ]
    assert client.now_playing[0] == (ARTIST, TITLE, "", 200)
    assert [n[1] for n in client.now_playing] == [TITLE, "Undertow"]


def test_the_scrobbler_keeps_two_albums_apart(tmp_path):
    sc, clock = scrobbler(tmp_path), Clock()
    feed(sc, song("Harbour Lights"), 4, clock, privacy_off=False)
    feed(sc, song(ALBUM), 110, clock, privacy_off=False)
    feed(sc, song("", title="Undertow"), 2, clock, privacy_off=False)
    client = sent(sc)
    assert [(s["album"], s["timestamp"]) for s in client.scrobbled] == [(ALBUM, int(T0) + 4)]
    assert [n[2] for n in client.now_playing] == ["Harbour Lights", ALBUM, ""]
