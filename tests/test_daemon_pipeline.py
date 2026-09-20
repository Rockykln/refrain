"""What one poll tick hands Discord, the tray, Last.fm, the history and the MPRIS server."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from tests.daemon_fakes import (  # noqa: E402
    CLIENT_ID,
    CLIENT_ID_BT,
    CLIENT_ID_MPRIS,
    FakeRPC,
    Player,
    install,
    make_config,
    song,
)

ARTIST, TITLE = "Mara Keel", "Paper Satellites"
COVER = "https://is1-ssl.mzstatic.example/image/paper-lanterns.jpg"
WALL0 = 1_700_000_000


@pytest.fixture
def rig(monkeypatch):
    def build(**sections):
        clock, _ = install(monkeypatch)
        worker = DaemonWorker(make_config(**sections))
        worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
        return worker, Player(worker, clock), clock

    return build


def rpc() -> FakeRPC:
    return FakeRPC.instances[-1]


def signals(worker, name):
    got = []
    getattr(worker, name).connect(lambda *args: got.append(args))
    return got


def test_playing_song_reaches_discord_with_cover_and_timer(rig):
    worker, player, _ = rig()
    player.play()
    player.tick()
    assert rpc().last == (
        "update",
        {
            "details": TITLE,
            "state": ARTIST,
            "large_image": COVER,
            "large_text": "Tidal",
            "start": WALL0,
            "end": WALL0 + 200,
        },
    )


def test_start_does_not_move_while_the_song_plays_on(rig):
    worker, player, _ = rig()
    player.play()
    player.tick(n=20)
    assert {p["start"] for p in rpc().updates} == {WALL0}


def test_pause_clears_discord_and_resume_shifts_the_timer_by_the_pause(rig):
    worker, player, clock = rig()
    player.play()
    player.tick(n=4)
    player.pause()
    player.tick()
    assert rpc().last == ("clear", {})
    clock.advance(30)
    player.resume()
    player.tick()
    kind, payload = rpc().last
    assert kind == "update"
    assert payload["start"] == WALL0 + 30
    assert payload["end"] == WALL0 + 30 + 200


def test_seek_moves_the_start(rig):
    worker, player, _ = rig()
    player.play()
    player.tick(n=2)
    player.src.track = song(position_ms=90_000)
    player.tick()
    # 90.5 s in, one second of wall time after the first tick.
    assert rpc().last[1]["start"] == WALL0 + 1 - 90


def test_track_change_restarts_the_timer_for_the_new_song(rig):
    worker, player, clock = rig()
    player.play()
    player.tick(n=10)
    worker._cover_fetcher.urls[(ARTIST, "Silk Road Radio")] = COVER + "?b"
    player.play(title="Silk Road Radio", duration_ms=150_000)
    player.tick()
    payload = rpc().last[1]
    assert payload["details"] == "Silk Road Radio"
    assert payload["start"] == int(clock.wall - 0.5)
    assert payload["end"] == payload["start"] + 150


def test_new_song_waits_for_its_cover_instead_of_flashing_the_logo(rig):
    worker, player, _ = rig()
    del worker._cover_fetcher.urls[(ARTIST, TITLE)]
    player.play()
    player.tick(n=2)
    assert rpc().updates == []
    worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
    player.tick()
    assert [p["large_image"] for p in rpc().updates] == [COVER]


def test_song_without_a_cover_goes_out_with_the_logo_after_three_polls(rig):
    worker, player, _ = rig()
    del worker._cover_fetcher.urls[(ARTIST, TITLE)]
    player.play()
    player.tick(n=3)
    assert rpc().updates == []
    player.tick()
    assert rpc().last[1]["large_image"] == "refrain"


def test_late_cover_replaces_the_logo_on_the_next_poll(rig):
    worker, player, _ = rig()
    del worker._cover_fetcher.urls[(ARTIST, TITLE)]
    player.play()
    player.tick(n=4)
    worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
    player.tick()
    images = [p["large_image"] for p in rpc().updates]
    assert images == ["refrain", COVER]
    assert rpc().updates[0]["start"] == rpc().updates[1]["start"]


def test_cover_art_off_sends_the_logo_without_waiting(rig):
    worker, player, _ = rig(behavior={"cover_art": False})
    player.play()
    player.tick()
    assert rpc().last[1]["large_image"] == "refrain"
    assert worker._cover_fetcher.requested == []


def test_privacy_off_clears_discord_and_tells_the_scrobbler(rig):
    worker, player, _ = rig(privacy={"mode": "off"})
    player.play()
    player.tick()
    assert rpc().calls == [("clear", {})]
    assert worker._scrobbler.calls[-1]["privacy_off"] is True


def test_privacy_minimal_hides_the_song(rig):
    worker, player, _ = rig(privacy={"mode": "minimal"})
    player.play()
    player.tick()
    assert rpc().last == (
        "update",
        {"details": "Listening to music", "large_image": "refrain", "large_text": "Refrain"},
    )
    assert worker._scrobbler.calls[-1]["privacy_off"] is False


def test_nothing_playing_clears_discord(rig):
    worker, player, _ = rig()
    player.play()
    player.tick()
    player.stop()
    player.tick()
    assert rpc().last == ("clear", {})


def test_album_matching_the_title_is_not_repeated(rig):
    worker, player, _ = rig()
    player.play(album=f"{ARTIST} - {TITLE}")
    player.tick()
    assert "large_text" not in rpc().last[1]


@pytest.mark.parametrize(
    ("artist", "album", "state"),
    [("", "Tidal", "Tidal"), ("", "", "Apple Music")],
)
def test_second_line_falls_back_when_the_artist_is_missing(rig, artist, album, state):
    worker, player, _ = rig(behavior={"cover_art": False})
    player.play(artist=artist, album=album)
    player.tick()
    payload = rpc().last[1]
    assert payload["state"] == state
    assert "large_text" not in payload


def test_long_title_is_left_to_the_rpc_client_to_fit(rig):
    worker, player, _ = rig(behavior={"cover_art": False})
    player.play(title="x" * 300)
    player.tick()
    assert rpc().last[1]["details"] == "x" * 300


def test_a_blank_title_clears_instead_of_sending(rig):
    worker, player, _ = rig(behavior={"cover_art": False})
    player.play(title="   ")
    player.tick()
    assert rpc().last == ("clear", {})
    assert rpc().updates == []


def test_short_song_gets_no_timer(rig):
    worker, player, _ = rig()
    player.play(duration_ms=20_000)
    player.tick()
    payload = rpc().last[1]
    assert "start" not in payload
    assert "end" not in payload


def test_segment_length_on_a_full_song_hides_the_time_everywhere(rig):
    worker, player, _ = rig()
    ticks = signals(worker, "progressTick")
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 200_000
    player.play(duration_ms=14_000)
    player.tick(n=3)
    payload = rpc().last[1]
    assert "start" not in payload
    assert "end" not in payload
    assert ticks[-1] == (-1, 0)
    assert worker._mpris_server.updates[-1][2] == 0
    # Last.fm still needs the song's real length to count the play.
    assert worker._scrobbler.calls[-1]["duration_ms"] == 200_000


def test_disputed_length_at_startup_scrobbles_with_the_shorter(rig):
    worker, player, _ = rig()
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 131_000
    player.play(position_ms=60_000, duration_ms=441_000)
    player.tick(n=2)
    payload = rpc().last[1]
    assert "start" not in payload
    assert "end" not in payload
    assert worker._scrobbler.calls[-1]["duration_ms"] == 131_000
    assert worker._scrobbler.calls[-1]["position_ms"] is None
    assert worker._history.calls[-1]["duration_ms"] == 131_000


def test_length_the_source_witnessed_from_the_start_beats_the_catalog(rig):
    worker, player, _ = rig()
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 131_000
    player.play(duration_ms=441_000)
    player.tick()
    payload = rpc().last[1]
    assert payload["end"] - payload["start"] == 441


def test_length_that_turns_out_to_be_a_stream_is_judged_by_the_catalog_in_the_same_poll(rig):
    worker, player, _ = rig()
    ticks = signals(worker, "progressTick")
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 150_000
    player.play(duration_ms=160_000)
    player.tick(n=320)
    assert ticks[-1] == (160_000, 160_000)
    player.play(position_ms=player.src.track.position_ms, duration_ms=300_000)
    player.tick()
    assert ticks[-1] == (-1, 0)
    assert worker._scrobbler.calls[-1]["position_ms"] is None


def test_button_links_the_catalog_song_before_the_tab(rig):
    worker, player, _ = rig()
    worker._cover_fetcher.song_urls[(ARTIST, TITLE)] = "https://music.apple.com/song/12345"
    player.play(url="https://music.apple.com/playlist/12345")
    player.tick()
    assert rpc().last[1]["buttons"] == [
        {"label": "Listen on Apple Music", "url": "https://music.apple.com/song/12345"}
    ]


def test_button_falls_back_to_the_browser_tab(rig):
    worker, player, _ = rig()
    player.play(url="https://music.apple.com/playlist/12345")
    player.tick()
    assert rpc().last[1]["buttons"][0]["url"] == "https://music.apple.com/playlist/12345"


def test_bluetooth_track_url_never_becomes_a_button(rig):
    worker, player, clock = rig()
    bt = Player(worker, clock, source="bluetooth")
    bt.play(url="https://example.com/12345")
    bt.tick()
    assert "buttons" not in rpc().last[1]


def test_buttons_off_sends_none(rig):
    worker, player, _ = rig(behavior={"show_buttons": False})
    worker._cover_fetcher.song_urls[(ARTIST, TITLE)] = "https://music.apple.com/song/12345"
    player.play()
    player.tick()
    assert "buttons" not in rpc().last[1]


def test_each_source_speaks_as_its_own_discord_app(rig):
    worker, player, clock = rig(
        discord={"client_id_mpris": CLIENT_ID_MPRIS, "client_id_bluetooth": CLIENT_ID_BT}
    )
    first = rpc()
    assert first.client_id == CLIENT_ID
    player.play()
    player.tick()
    browser_app = rpc()
    assert first.closed
    assert browser_app.client_id == CLIENT_ID_MPRIS
    assert browser_app.last[1]["details"] == TITLE

    player.pause()
    bt = Player(worker, clock, source="bluetooth")
    worker._cover_fetcher.urls[(ARTIST, "Undertow")] = COVER
    bt.play(title="Undertow")
    bt.tick()
    assert browser_app.closed
    assert rpc().client_id == CLIENT_ID_BT
    assert rpc().last[1]["details"] == "Undertow"
    assert worker._active_source == "bluetooth"


def test_shared_app_id_keeps_one_connection_across_sources(rig):
    worker, player, clock = rig()
    player.play()
    player.tick()
    player.pause()
    bt = Player(worker, clock, source="bluetooth")
    worker._cover_fetcher.urls[(ARTIST, "Undertow")] = COVER
    bt.play(title="Undertow")
    bt.tick()
    assert len(FakeRPC.instances) == 1
    assert rpc().last[1]["details"] == "Undertow"


def test_bluetooth_is_not_asked_while_the_browser_plays(rig):
    worker, player, _ = rig()
    player.play()
    player.tick(n=3)
    assert worker._bluetooth.reads == 0


def test_disabled_source_is_never_read(rig):
    worker, player, _ = rig(sources={"mpris_enabled": False})
    player.play()
    player.tick()
    assert worker._mpris.reads == 0
    assert rpc().calls == [("clear", {})]


def test_dangling_player_is_cleared_after_its_length_plus_grace(rig):
    worker, player, _ = rig(advanced={"idle_grace_s": 30})
    changes = signals(worker, "trackChanged")
    player.play()
    player.tick()
    player.frozen = True
    player.tick(seconds=10, n=22)
    assert rpc().last[0] == "update"
    player.tick(seconds=10, n=2)
    assert rpc().last == ("clear", {})
    assert not changes[-1][0].has_track


def test_dangling_player_with_a_disputed_length_is_cleared_after_the_longer_one(rig):
    worker, player, _ = rig(advanced={"idle_grace_s": 30})
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 131_000
    player.play(position_ms=60_000, duration_ms=441_000)
    player.tick()
    player.frozen = True
    player.tick(seconds=10, n=45)
    assert rpc().last[0] == "update"
    player.tick(seconds=10, n=3)
    assert rpc().last == ("clear", {})


def test_stall_check_off_still_clears_a_dangling_player(rig):
    worker, player, _ = rig(advanced={"idle_grace_s": 30, "position_stall_s": 0})
    player.play()
    player.tick()
    player.frozen = True
    player.tick(seconds=10, n=24)
    assert rpc().last == ("clear", {})


def test_idle_detection_off_keeps_a_frozen_player(rig):
    worker, player, _ = rig(advanced={"idle_grace_s": 0})
    player.play()
    player.tick()
    player.frozen = True
    player.tick(seconds=10, n=60)
    assert rpc().last[0] == "update"


def test_tray_progress_follows_the_song(rig):
    worker, player, _ = rig()
    ticks = signals(worker, "progressTick")
    player.play()
    player.tick(n=3)
    assert ticks == [(500, 200_000), (1000, 200_000), (1500, 200_000)]


def test_tray_progress_without_a_length_shows_elapsed_only(rig):
    worker, player, _ = rig()
    ticks = signals(worker, "progressTick")
    player.play(duration_ms=0)
    player.tick()
    assert ticks == [(500, 0)]


def test_frozen_player_mid_song_falls_back_to_refrains_own_clock(rig):
    worker, player, _ = rig()
    ticks = signals(worker, "progressTick")
    player.play(position_ms=60_000)
    player.frozen = True
    player.tick(seconds=5, n=3)
    assert [t[0] for t in ticks] == [60_000, 65_000, 70_000]
    assert {p["start"] for p in rpc().updates} == {WALL0 + 5 - 60}


def test_status_signal_fires_only_on_changes(rig):
    worker, player, _ = rig()
    statuses = signals(worker, "statusChanged")
    player.play()
    player.tick(n=3)
    player.pause()
    player.tick(n=2)
    player.resume()
    player.tick()
    assert [s[0] for s in statuses] == [
        PlaybackStatus.PLAYING,
        PlaybackStatus.PAUSED,
        PlaybackStatus.PLAYING,
    ]


def test_track_signal_fires_once_per_song(rig):
    worker, player, _ = rig()
    changes = signals(worker, "trackChanged")
    player.play()
    player.tick(n=5)
    player.play(title="Silk Road Radio")
    player.tick(n=5)
    assert [c[0].title for c in changes] == [TITLE, "Silk Road Radio"]


def test_scrobbler_and_history_get_the_players_position(rig):
    worker, player, _ = rig()
    player.play()
    player.tick(n=4)
    scrobble = worker._scrobbler.calls[-1]
    assert scrobble["track"].title == TITLE
    assert scrobble["duration_ms"] == 200_000
    assert scrobble["position_ms"] == 2000
    assert scrobble["restarted"] is False
    history = worker._history.calls[-1]
    assert history["position_ms"] == 2000
    assert history["cover_url"] == COVER


def test_song_starting_over_is_reported_as_a_replay_once(rig):
    worker, player, _ = rig()
    player.play(duration_ms=60_000)
    player.tick(seconds=5, n=12)
    player.src.track = song(duration_ms=60_000, position_ms=0)
    player.tick(n=3)
    flags = [c["restarted"] for c in worker._scrobbler.calls]
    assert flags.count(True) == 1
    assert flags[-3:] == [True, False, False]
    assert [c["restarted"] for c in worker._history.calls] == flags


def test_history_change_is_published(rig):
    worker, player, _ = rig()
    snapshots = signals(worker, "historyChanged")
    worker._history.changes = True
    player.play()
    player.tick()
    assert len(snapshots) == 1


def test_history_failure_is_logged_once_and_the_tick_goes_on(rig, caplog):
    worker, player, _ = rig()

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    worker._history.update = broken
    player.play()
    with caplog.at_level(logging.ERROR, logger="refrain.daemon"):
        player.tick(n=3)
    assert [r.getMessage() for r in caplog.records] == ["History update failed"]
    assert len(rpc().updates) == 3


def test_scrobbler_failure_does_not_stop_discord(rig):
    worker, player, _ = rig()

    def broken(*args, **kwargs):
        raise RuntimeError("queue locked")

    worker._scrobbler.update = broken
    player.play()
    player.tick(n=2)
    assert len(rpc().updates) == 2


def test_mpris_server_gets_the_corrected_length_and_cover(rig):
    worker, player, _ = rig()
    worker._cover_fetcher.durations[(ARTIST, TITLE)] = 200_000
    player.play(duration_ms=0)
    player.tick()
    track, cover, length = worker._mpris_server.updates[-1]
    assert (track.title, cover, length) == (TITLE, COVER, 200_000)


def test_a_failing_source_read_is_logged_not_raised(rig, caplog):
    worker, player, _ = rig()

    def broken():
        raise RuntimeError("bus gone")

    worker._mpris.read = broken
    with caplog.at_level(logging.ERROR, logger="refrain.daemon"):
        worker._tick()
    assert "Daemon tick failed" in caplog.text


def test_stopped_source_keeps_the_last_active_one(rig):
    worker, player, _ = rig()
    player.play()
    player.tick()
    player.src.track = TrackInfo.empty()
    player.tick()
    assert worker._active_source == "mpris"


def test_queue_counting_player_is_timed_from_the_track_change(rig):
    worker, player, clock = rig(behavior={"cover_art": False})
    player.play()
    player.tick(n=120)
    changed_at = int(clock.wall)
    player.play(
        title="Silk Road Radio", position_ms=player.src.track.position_ms, duration_ms=600_000
    )
    player.tick(n=3)
    payload = rpc().last[1]
    assert payload["details"] == "Silk Road Radio"
    assert payload["start"] == changed_at
    # The 10-minute length is the queue's, not the song's.
    assert "end" not in payload


def _play_whole(player, title, seconds):
    player.play(title=title, duration_ms=0)
    player.tick(seconds=1, n=seconds)


def test_two_whole_plays_teach_the_length_of_a_song_the_catalog_lacks(rig):
    worker, player, _ = rig(behavior={"cover_art": False})
    for _ in range(2):
        _play_whole(player, TITLE, 150)
        _play_whole(player, "Silk Road Radio", 60)
    assert worker._song_lengths.get_ms(ARTIST, TITLE, "Tidal") == 150_000
    player.play(duration_ms=0)
    player.tick()
    assert rpc().last[1]["end"] - rpc().last[1]["start"] == 150


def test_a_skipped_song_teaches_no_length(rig):
    worker, player, _ = rig(behavior={"cover_art": False})
    for _ in range(2):
        _play_whole(player, TITLE, 150)
        worker.control_next()
        _play_whole(player, "Silk Road Radio", 60)
    assert worker._song_lengths.get_ms(ARTIST, TITLE, "Tidal") == 0


def test_skipping_during_the_cover_wait_gives_the_next_song_its_full_wait(rig):
    worker, player, _ = rig()
    del worker._cover_fetcher.urls[(ARTIST, TITLE)]
    player.play()
    player.tick(n=2)
    player.play(title="Silk Road Radio")
    player.tick(n=2)
    assert rpc().updates == []


def test_a_song_change_never_leaves_the_old_song_on_the_profile(rig):
    """Waiting for a cover is fine before the first song, not after it."""
    worker, player, _ = rig()
    player.play()
    player.tick()
    assert rpc().last[1]["details"] == TITLE

    player.play(title="Salt Flats", artist="Wren & Ash")
    player.tick()
    # Out at once with the logo; the cover follows when it lands.
    assert rpc().last[1]["details"] == "Salt Flats"
    assert rpc().last[1]["large_image"] == "refrain"
