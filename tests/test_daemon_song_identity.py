"""Metadata that fills in a poll late is still the song that started."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from refrain import daemon  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.history import PlayHistory  # noqa: E402
from tests.daemon_fakes import Player, install, make_config  # noqa: E402

ARTIST, TITLE, ALBUM = "Marlow Vance", "Paper Satellites", "Low Orbit"
COVER = "https://is1-ssl.mzstatic.example/image/low-orbit.jpg"


@pytest.fixture
def rig(monkeypatch, caplog):
    clock, _ = install(monkeypatch)
    monkeypatch.setattr(daemon, "PlayHistory", PlayHistory)
    worker = DaemonWorker(make_config())
    covers = worker._cover_fetcher
    covers.urls[(ARTIST, TITLE)] = COVER
    covers.urls[(ARTIST, "Undertow")] = COVER
    lookups: list[tuple[str, str, str]] = []
    real_get = covers.get

    def get(artist, title, album=""):
        lookups.append((artist, title, album))
        return real_get(artist, title, album)

    covers.get = get
    changes: list = []
    worker.trackChanged.connect(changes.append)
    caplog.set_level(logging.DEBUG, logger="refrain.history")
    return worker, Player(worker, clock), lookups, changes, caplog


def history_lines(caplog, text: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "refrain.history" and text in r.getMessage()
    ]


def test_an_album_sent_a_poll_late_does_not_start_the_song_again(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album="")
    player.tick()
    at = player.src.track.position_ms
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, position_ms=at)
    player.tick(n=4)

    assert len(history_lines(caplog, "now playing")) == 1
    assert history_lines(caplog, "dropped") == []
    assert len(changes) == 1
    assert {album for _, _, album in lookups} == {""}
    assert [c["track"].album for c in worker._scrobbler.calls] == ["", *[ALBUM] * 4]
    assert worker._position_state.anchored
    entry = worker._history.snapshot().entries[0]
    assert (entry.title, entry.album) == (TITLE, ALBUM)


def test_an_album_dropped_for_a_poll_does_not_start_the_song_again(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album=ALBUM)
    player.tick(n=2)
    at = player.src.track.position_ms
    player.play(title=TITLE, artist=ARTIST, album="", position_ms=at)
    player.tick()
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, position_ms=at + 500)
    player.tick(n=2)

    assert len(history_lines(caplog, "now playing")) == 1
    assert history_lines(caplog, "dropped") == []
    assert len(changes) == 1
    assert {album for _, _, album in lookups} == {ALBUM}


def test_a_catalog_album_found_a_poll_late_does_not_start_the_song_again(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album="")
    player.tick()
    worker._cover_fetcher.albums[(ARTIST, TITLE)] = ALBUM
    player.tick(n=4)

    assert len(history_lines(caplog, "now playing")) == 1
    assert history_lines(caplog, "dropped") == []
    assert len(changes) == 1
    assert {album for _, _, album in lookups} == {""}
    assert [c["track"].album for c in worker._scrobbler.calls] == ["", *[ALBUM] * 4]
    assert worker._history.snapshot().entries[0].album == ALBUM


def test_the_first_album_found_stays_when_the_player_sends_another(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album="")
    player.tick()
    worker._cover_fetcher.albums[(ARTIST, TITLE)] = ALBUM
    player.tick()
    at = player.src.track.position_ms
    player.play(title=TITLE, artist=ARTIST, album="Low Orbit (Deluxe)", position_ms=at)
    player.tick(n=3)

    assert len(history_lines(caplog, "now playing")) == 1
    assert history_lines(caplog, "dropped") == []
    assert [c["track"].album for c in worker._scrobbler.calls] == ["", *[ALBUM] * 4]
    assert worker._history.snapshot().entries[0].album == ALBUM


def test_a_different_title_is_still_a_new_song(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album="")
    player.tick(n=2)
    player.play(title="Undertow", artist=ARTIST, album=ALBUM)
    player.tick(n=2)

    assert len(history_lines(caplog, "now playing")) == 2
    assert len(history_lines(caplog, "dropped")) == 1
    assert [t.title for t in changes] == [TITLE, "Undertow"]
    assert changes[-1].album == ALBUM


def test_a_different_album_is_a_different_recording(rig):
    worker, player, lookups, changes, caplog = rig
    player.play(title=TITLE, artist=ARTIST, album=ALBUM)
    player.tick(n=2)
    player.play(title=TITLE, artist=ARTIST, album="Live at the Harbour")
    player.tick(n=2)

    assert len(history_lines(caplog, "now playing")) == 2
    assert [t.album for t in changes] == [ALBUM, "Live at the Harbour"]
