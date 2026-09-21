"""Which length a tick uses: the catalog's once it has one, else the player's or a learned one."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain.daemon import DaemonWorker  # noqa: E402
from tests.daemon_fakes import FakeRPC, Player, install, make_config  # noqa: E402

ARTIST, TITLE, ALBUM = "Marlow Vance", "Paper Satellites", "Low Orbit"
COVER = "https://is1-ssl.mzstatic.example/image/low-orbit.jpg"


@pytest.fixture
def rig(monkeypatch):
    clock, _ = install(monkeypatch)
    worker = DaemonWorker(make_config())
    worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
    ticks: list[tuple[int, int]] = []
    worker.progressTick.connect(lambda pos, dur: ticks.append((pos, dur)))
    return worker, Player(worker, clock), ticks


def play(player, **fields):
    player.play(title=TITLE, artist=ARTIST, album=ALBUM, **fields)


def discord_length_s() -> int:
    payload = FakeRPC.instances[-1].last[1]
    return payload["end"] - payload["start"]


def test_growing_buffer_lengths_never_replace_the_catalog_length(rig):
    """Vivaldi, first song after start: 5:23, 7:23, 10:06 on a 2:45 song."""
    worker, player, ticks = rig
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 165_000
    play(player, duration_ms=323_000)
    player.tick(n=84)
    at = player.src.track.position_ms
    play(player, position_ms=at, duration_ms=443_000)
    player.tick(n=40)
    at = player.src.track.position_ms
    play(player, position_ms=at, duration_ms=606_000)
    player.tick(n=40)
    assert {dur for _, dur in ticks} == {165_000}
    assert ticks[-1] == (82_000, 165_000)
    assert discord_length_s() == 165
    assert {c["duration_ms"] for c in worker._history.calls} == {165_000}
    assert {c["duration_ms"] for c in worker._scrobbler.calls} == {165_000}
    assert worker._mpris_server.updates[-1][2] == 165_000


def test_a_player_length_short_of_the_song_gives_way_to_the_catalog(rig):
    """Firefox: the player says 1:30, the catalog 3:16."""
    worker, player, ticks = rig
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 196_000
    play(player, duration_ms=90_000)
    player.tick(n=240)
    # Two minutes in, past the player's 1:30, and still the song's own position.
    assert ticks[-1] == (120_000, 196_000)
    assert discord_length_s() == 196
    assert worker._history.calls[-1]["duration_ms"] == 196_000
    assert worker._scrobbler.calls[-1]["duration_ms"] == 196_000


def test_the_catalog_length_is_used_mid_song_at_startup_too(rig):
    """No track change to learn from: the catalog is not in dispute."""
    worker, player, ticks = rig
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 165_000
    play(player, position_ms=60_000, duration_ms=323_000)
    player.tick(n=2)
    assert ticks[-1] == (61_000, 165_000)
    assert discord_length_s() == 165
    assert worker._scrobbler.calls[-1]["duration_ms"] == 165_000


def test_a_catalog_length_that_arrives_late_replaces_the_players(rig):
    worker, player, ticks = rig
    play(player, duration_ms=90_000)
    player.tick(n=4)
    assert ticks[-1] == (2_000, 90_000)
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 196_000
    player.tick()
    assert ticks[-1] == (2_500, 196_000)
    assert worker._history.calls[-1]["duration_ms"] == 196_000


def test_without_a_catalog_length_the_players_own_counts(rig):
    worker, player, ticks = rig
    play(player, duration_ms=90_000)
    player.tick(n=3)
    assert ticks[-1] == (1_500, 90_000)
    assert discord_length_s() == 90
    assert worker._scrobbler.calls[-1]["duration_ms"] == 90_000


def test_without_a_catalog_or_player_length_the_learned_one_counts(rig):
    worker, player, ticks = rig
    for _ in range(2):
        worker._song_lengths.observe(ARTIST, TITLE, ALBUM, 157_000)
    play(player, duration_ms=0)
    player.tick(n=3)
    assert ticks[-1] == (1_500, 157_000)
    assert discord_length_s() == 157
    assert worker._history.calls[-1]["duration_ms"] == 157_000


def test_without_a_catalog_length_the_player_beats_a_learned_one(rig):
    worker, player, ticks = rig
    for _ in range(2):
        worker._song_lengths.observe(ARTIST, TITLE, ALBUM, 157_000)
    play(player, duration_ms=250_000)
    player.tick(n=3)
    assert ticks[-1] == (1_500, 250_000)
