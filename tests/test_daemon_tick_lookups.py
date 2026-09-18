"""Lookups a poll tick makes once rather than once per pipeline stage."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain import daemon, song_lengths  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.song_lengths import LearnedLengths  # noqa: E402
from tests.daemon_fakes import FakeRPC, Player, install, make_config  # noqa: E402

ARTIST, TITLE, ALBUM = "Wren & Ash", "Salt Flats", "Open Country"
COVER = "https://is1-ssl.mzstatic.example/image/open-country.jpg"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    def build(**sections):
        clock, _ = install(monkeypatch)
        worker = DaemonWorker(make_config(**sections))
        worker._song_lengths = LearnedLengths(path=tmp_path / "lengths.txt")
        for artist, title in (
            (ARTIST, TITLE),
            ("Juniper Lane", "Rooftop Weather"),
            ("Mara Keel", "Undertow"),
        ):
            worker._cover_fetcher.urls[(artist, title)] = COVER
        return worker, Player(worker, clock)

    return build


def _count(monkeypatch, obj, name):
    calls = []
    real = getattr(obj, name)

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, name, spy)
    return calls


def _learn(lengths, seconds, artist=ARTIST, title=TITLE, album=ALBUM):
    for _ in range(2):
        lengths.observe(artist, title, album, seconds * 1000)


def test_the_known_length_is_looked_up_once_per_tick(rig, monkeypatch):
    worker, player = rig(behavior={"cover_art": False})
    _learn(worker._song_lengths, 234)
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, duration_ms=0)
    player.tick()
    known = _count(monkeypatch, worker, "_known_duration_ms")
    player.tick(n=5)
    assert len(known) == 5
    assert FakeRPC.instances[-1].last[1]["end"] - FakeRPC.instances[-1].last[1]["start"] == 234


def test_the_catalog_is_asked_once_per_tick_for_the_playing_song(rig, monkeypatch):
    worker, player = rig()
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 234_000
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, duration_ms=0)
    player.tick()
    catalog = _count(monkeypatch, worker._cover_fetcher, "get_duration_ms")
    player.tick(n=4)
    # One for the pipeline, one for the check whether a measured length
    # is being played past.
    assert len(catalog) == 8


def test_learned_song_keys_are_hashed_once(tmp_path, monkeypatch):
    lengths = LearnedLengths(path=tmp_path / "lengths.txt")
    _learn(lengths, 234)
    hashed = _count(monkeypatch, song_lengths, "song_key")
    for _ in range(10):
        assert lengths.get_ms(ARTIST, TITLE, ALBUM) == 234_000
    assert hashed == []


def test_a_length_changed_mid_tick_reaches_the_later_stages(rig):
    worker, player = rig(behavior={"cover_art": False})
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, duration_ms=0)
    worker._tick_known = None
    assert worker._tick_known_ms(player.src.track) == 0
    _learn(worker._song_lengths, 234)
    assert worker._tick_known_ms(player.src.track) == 234_000
    worker._song_lengths.forget(ARTIST, TITLE, ALBUM)
    assert worker._tick_known_ms(player.src.track) == 0


def test_a_catalog_length_that_arrives_is_used_on_the_next_tick(rig):
    worker, player = rig()
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, duration_ms=0)
    player.tick()
    assert "end" not in FakeRPC.instances[-1].last[1]
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 234_000
    player.tick()
    payload = FakeRPC.instances[-1].last[1]
    assert payload["end"] - payload["start"] == 234


def test_the_album_line_follows_a_track_change(rig, monkeypatch):
    worker, player = rig()
    formatted = _count(monkeypatch, daemon, "_format_album_for_display")
    player.play(title=TITLE, artist=ARTIST, album=f"{ARTIST} - {ALBUM}")
    player.tick(n=3)
    assert FakeRPC.instances[-1].last[1]["large_text"] == ALBUM
    assert len(formatted) == 1
    player.play(title="Rooftop Weather", artist="Juniper Lane", album="Rooftop Weather")
    player.tick(n=3)
    assert "large_text" not in FakeRPC.instances[-1].last[1]
    assert len(formatted) == 2
    player.play(title="Undertow", artist="Mara Keel", album="Tidal")
    player.tick()
    assert FakeRPC.instances[-1].last[1]["large_text"] == "Tidal"
    assert len(formatted) == 3
