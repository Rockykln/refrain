"""Recently played: what counts, what is kept, and what reaches the disk.
The clocks are passed in, so a four-minute listen takes microseconds."""

from __future__ import annotations

import json
import logging
import os

import pytest

from refrain.config import HistoryConfig
from refrain.history import PlayHistory, clamp_limit, counts_as_played, history_path
from refrain.sources.base import PlaybackStatus, TrackInfo

SONG_MS = 200_000


def _t(title, *, status=PlaybackStatus.PLAYING, source="mpris", player="Chromium"):
    return TrackInfo(
        source=source,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=SONG_MS,
        status=status,
        player=player,
    )


class Clock:
    def __init__(self):
        self.wall = 1_700_000_000.0
        self.mono = 1000.0


def _feed(h, track, seconds, clock, duration_ms=SONG_MS, step=2.0, **kw):
    """Poll ``track`` for ``seconds``, the way the daemon tick does."""
    changed = False
    for _ in range(max(1, int(seconds / step)) + 1):
        changed |= h.update(track, duration_ms, now_wall=clock.wall, now_mono=clock.mono, **kw)
        clock.wall += step
        clock.mono += step
    return changed


def _play(h, title, clock, *, start_s, seconds, step=2.0):
    """Like ``_feed``, with the player reporting a position that moves on."""
    pos = start_s
    for _ in range(int(seconds / step) + 1):
        h.update(
            _t(title),
            SONG_MS,
            now_wall=clock.wall,
            now_mono=clock.mono,
            position_ms=int(pos * 1000),
        )
        clock.wall += step
        clock.mono += step
        pos += step


def _counted(h, title, clock):
    """Play ``title`` well past half its length."""
    _feed(h, _t(title), 110, clock)


def _titles(h):
    return [e.title for e in h.snapshot().entries]


def _stored():
    """The songs on disk that counted — not the one saved mid-play."""
    if not history_path().exists():
        return []
    data = json.loads(history_path().read_text(encoding="utf-8"))
    return [e["title"] for e in data["entries"]]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def hist(xdg_tmp):
    return PlayHistory(HistoryConfig())


# --------------------------------------------------------------------------- #
# The rule                                                                     #
# --------------------------------------------------------------------------- #


def test_last_fm_rule_half_or_four_minutes():
    assert counts_as_played(100_000, 200_000)
    assert not counts_as_played(99_999, 200_000)
    # A long song counts after four minutes, not after half of it.
    assert counts_as_played(240_000, 600_000)
    assert not counts_as_played(239_999, 600_000)
    # Anything 30 s or shorter never counts, however long it played.
    assert not counts_as_played(30_000, 30_000)


def test_unknown_length_counts_after_four_minutes():
    """Without a length (often AVRCP) a song still counts at Last.fm's four-minute cap."""
    assert counts_as_played(240_000, 0)
    assert not counts_as_played(239_999, 0)


def test_limit_is_clamped():
    assert clamp_limit(0) == 1
    assert clamp_limit(500) == 100
    assert clamp_limit("junk") == 30


# --------------------------------------------------------------------------- #
# Now playing, kept, dropped                                                   #
# --------------------------------------------------------------------------- #


def test_song_shows_as_now_playing_immediately(hist, clock):
    assert hist.update(_t("A"), SONG_MS, now_wall=clock.wall, now_mono=clock.mono)
    snap = hist.snapshot()
    assert [e.title for e in snap.entries] == ["A"]
    assert snap.now_playing and snap.playing
    # Nothing has counted yet, so nothing is written.
    assert not history_path().exists()


def test_skipped_song_leaves_no_trace(hist, clock):
    _feed(hist, _t("A"), 20, clock)
    _feed(hist, _t("B"), 2, clock)
    assert _titles(hist) == ["B"]
    assert not history_path().exists()


def test_song_is_kept_once_half_played(hist, clock, caplog):
    caplog.set_level(logging.INFO, logger="refrain.history")
    _counted(hist, "A", clock)
    # Written the moment it counts, not when the next song starts.
    assert _stored() == ["A"]
    assert "History: kept Artist — A" in caplog.text
    _feed(hist, _t("B"), 2, clock)
    snap = hist.snapshot()
    assert [e.title for e in snap.entries] == ["B", "A"]
    assert snap.now_playing


def test_long_song_is_kept_after_four_minutes(hist, clock):
    _feed(hist, _t("Long"), 242, clock, duration_ms=600_000)
    assert _stored() == ["Long"]


def test_song_without_a_length_is_kept_after_four_minutes(hist, clock):
    _feed(hist, _t("BT", source="bluetooth", player="Desk Speaker"), 242, clock, duration_ms=0)
    assert _stored() == ["BT"]
    assert hist.snapshot().entries[0].player == "Desk Speaker"


def test_paused_time_does_not_count(hist, clock):
    _feed(hist, _t("A"), 60, clock)
    _feed(hist, _t("A", status=PlaybackStatus.PAUSED), 300, clock)
    snap = hist.snapshot()
    assert snap.now_playing and not snap.playing
    assert _stored() == []
    _feed(hist, _t("A"), 42, clock)
    assert _stored() == ["A"]


def test_the_gap_between_two_songs_does_not_flash_paused(hist, clock):
    """Apple Music reports: A paused, B paused, B playing — within a second."""
    _feed(hist, _t("A"), 30, clock)
    _feed(hist, _t("A", status=PlaybackStatus.PAUSED), 0.5, clock, step=0.5)
    assert hist.snapshot().playing
    _feed(hist, _t("B", status=PlaybackStatus.PAUSED), 0.5, clock, step=0.5)
    snap = hist.snapshot()
    assert [e.title for e in snap.entries] == ["B"]
    assert snap.now_playing and snap.playing
    _feed(hist, _t("B"), 2, clock)
    assert hist.snapshot().playing


def test_a_real_pause_shows_after_two_seconds(hist, clock):
    _feed(hist, _t("A"), 10, clock)
    _feed(hist, _t("A", status=PlaybackStatus.PAUSED), 1, clock, step=0.5)
    assert hist.snapshot().playing
    _feed(hist, _t("A", status=PlaybackStatus.PAUSED), 2, clock, step=0.5)
    assert not hist.snapshot().playing


def test_a_paused_tab_at_startup_is_not_now_playing(hist, clock):
    _feed(hist, _t("A", status=PlaybackStatus.PAUSED), 10, clock)
    assert _titles(hist) == []


def test_stopping_ends_the_song(hist, clock):
    _counted(hist, "A", clock)
    _feed(hist, TrackInfo.empty(), 8, clock)
    assert hist.snapshot().now_playing  # could still be a hiccup of the source
    _feed(hist, TrackInfo.empty(), 4, clock)
    snap = hist.snapshot()
    assert [e.title for e in snap.entries] == ["A"]
    assert not snap.now_playing


def test_progress_survives_a_single_empty_poll(hist, clock):
    _feed(hist, _t("A"), 60, clock)
    _feed(hist, TrackInfo.empty(), 0, clock)
    _feed(hist, _t("A"), 60, clock)
    assert _stored() == ["A"]
    assert _titles(hist) == ["A"]


def test_a_removed_song_stays_out_across_an_empty_poll(hist, clock):
    _feed(hist, _t("A"), 10, clock)
    a = hist.snapshot().entries[0]
    assert hist.remove(a.started_at, a.title, a.artist)
    _feed(hist, TrackInfo.empty(), 0, clock)
    _feed(hist, _t("A"), 120, clock)
    assert _titles(hist) == []
    assert _stored() == []


def test_cover_arriving_later_repaints(hist, clock):
    _feed(hist, _t("A"), 4, clock)
    assert hist.update(
        _t("A"), SONG_MS, cover_url="https://x/c.jpg", now_wall=clock.wall, now_mono=clock.mono
    )
    assert hist.snapshot().entries[0].cover_url == "https://x/c.jpg"


# --------------------------------------------------------------------------- #
# Limit and switch                                                             #
# --------------------------------------------------------------------------- #


def test_limit_caps_what_is_kept_and_what_is_shown(xdg_tmp, clock):
    h = PlayHistory(HistoryConfig(max_entries=3))
    for title in "ABCDE":
        _counted(h, title, clock)
    _feed(h, _t("F"), 2, clock)
    assert _stored() == ["E", "D", "C"]
    # The song on right now takes a slot on screen without pushing a
    # kept song off the disk — it may still be skipped.
    assert _titles(h) == ["F", "E", "D"]


def test_lowering_the_limit_drops_the_oldest(hist, clock):
    for title in "ABCD":
        _counted(hist, title, clock)
    assert hist.reconfigure(HistoryConfig(max_entries=2))
    assert _stored() == ["D", "C"]


def test_turning_it_off_deletes_and_stops_recording(hist, clock):
    _counted(hist, "A", clock)
    assert history_path().exists()
    assert hist.reconfigure(HistoryConfig(enabled=False))
    assert not history_path().exists()
    snap = hist.snapshot()
    assert not snap.enabled and snap.entries == ()
    assert not _feed(hist, _t("B"), 120, clock)
    assert not history_path().exists()


def test_off_at_startup_deletes_a_leftover_file(xdg_tmp):
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text('{"version": 1, "entries": [{"title": "A"}]}', encoding="utf-8")
    PlayHistory(HistoryConfig(enabled=False))
    assert not history_path().exists()


def test_clear_forgets_everything(hist, clock):
    _counted(hist, "A", clock)
    _feed(hist, _t("B"), 10, clock)
    assert hist.clear()
    assert _titles(hist) == []
    assert not history_path().exists()
    # Still playing, so B is back as "now playing" — counting from zero.
    _feed(hist, _t("B"), 2, clock)
    assert _titles(hist) == ["B"]
    assert not history_path().exists()


# --------------------------------------------------------------------------- #
# On disk                                                                      #
# --------------------------------------------------------------------------- #


def test_survives_a_restart(hist, clock):
    _counted(hist, "A", clock)
    _counted(hist, "B", clock)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    snap = again.snapshot()
    assert [e.title for e in snap.entries] == ["B", "A"]
    assert not snap.now_playing
    first = snap.entries[0]
    assert (first.source, first.player, first.artist) == ("mpris", "Chromium", "Artist")
    assert first.started_at > 0 and first.duration_ms == SONG_MS


def test_a_crash_mid_song_keeps_a_song_that_already_counted(hist, clock):
    _counted(hist, "A", clock)  # still playing — no shutdown, no next song
    assert _titles(PlayHistory(HistoryConfig())) == ["A"]


def test_quit_keeps_what_counted_and_drops_the_rest(hist, clock):
    _counted(hist, "A", clock)
    _feed(hist, _t("B"), 10, clock)
    hist.shutdown()
    assert _titles(PlayHistory(HistoryConfig())) == ["A"]


# --------------------------------------------------------------------------- #
# Stopping and starting mid-song                                               #
# --------------------------------------------------------------------------- #


def test_a_restart_mid_song_carries_on_counting(hist, clock, caplog):
    caplog.set_level(logging.INFO, logger="refrain.history")
    _feed(hist, _t("A"), 60, clock)  # 60 s of a 200 s song — not counted yet
    started = hist.snapshot().entries[0].started_at
    hist.shutdown()

    again = PlayHistory(HistoryConfig())
    _feed(again, _t("A"), 42, clock)  # 60 + 42 ≥ half
    assert _stored() == ["A"]
    assert again.snapshot().entries[0].started_at == started
    assert "carrying on with Artist — A" in caplog.text


def test_a_crash_mid_song_carries_on_from_the_last_save(hist, clock):
    _feed(hist, _t("A"), 64, clock)  # saved at 30 s and 60 s; no shutdown
    again = PlayHistory(HistoryConfig())
    _feed(again, _t("A"), 42, clock)
    assert _stored() == ["A"]


def test_the_saved_song_is_let_go_when_another_plays(hist, clock):
    _feed(hist, _t("A"), 60, clock)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    _feed(again, _t("B"), 110, clock)
    assert _stored() == ["B"]
    current = json.loads(history_path().read_text(encoding="utf-8"))["current"]
    assert current["entry"]["title"] == "B"  # A is gone from the file


def test_the_saved_song_is_let_go_after_too_long(hist, clock):
    _feed(hist, _t("A"), 60, clock)
    hist.shutdown()
    clock.wall += 3600  # an hour later the same song is a new play
    again = PlayHistory(HistoryConfig())
    _feed(again, _t("A"), 42, clock)
    assert _stored() == []  # counting started over: 42 s isn't half


def test_a_restart_during_a_song_that_counted_does_not_add_it_twice(hist, clock, caplog):
    """Counted at 1:50, Refrain restarted at 2:30, the song still on."""
    caplog.set_level(logging.INFO, logger="refrain.history")
    _counted(hist, "A", clock)
    _feed(hist, _t("A"), 40, clock)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    _feed(again, _t("A"), 20, clock)
    snap = again.snapshot()
    assert [e.title for e in snap.entries] == ["A"]
    assert snap.now_playing
    assert _stored() == ["A"]
    assert "carrying on with Artist — A (already in the list)" in caplog.text
    _feed(again, _t("B"), 2, clock)
    assert _titles(again) == ["B", "A"]


def test_a_replay_long_after_is_a_new_play(hist, clock):
    _counted(hist, "A", clock)
    hist.shutdown()
    clock.wall += 3600
    again = PlayHistory(HistoryConfig())
    _counted(again, "A", clock)
    assert _stored() == ["A", "A"]


def test_a_restart_that_follows_on_is_the_same_play(hist, clock, caplog):
    """Counted at 1:40, quit at 2:30, back 7 s later at 2:37."""
    caplog.set_level(logging.INFO, logger="refrain.history")
    _play(hist, "A", clock, start_s=0, seconds=150)
    hist.shutdown()
    clock.wall += 5
    clock.mono += 5
    again = PlayHistory(HistoryConfig())
    _play(again, "A", clock, start_s=157, seconds=20)
    assert _titles(again) == ["A"]
    assert _stored() == ["A"]
    assert "carrying on with Artist — A (already in the list)" in caplog.text


def test_a_song_that_started_over_during_a_restart_is_a_new_play(hist, clock):
    """A song back at 0:10 after a restart began again, even within the old time window."""
    _play(hist, "A", clock, start_s=0, seconds=150)
    hist.shutdown()
    clock.wall += 60
    clock.mono += 60
    again = PlayHistory(HistoryConfig())
    _play(again, "A", clock, start_s=10, seconds=110)
    assert _stored() == ["A", "A"]


def test_a_crash_after_it_counted_still_tells_a_replay_apart(hist, clock):
    # Progress is saved every 30 s of play once counted too, so the last
    # position on disk is recent enough to compare with.
    _play(hist, "A", clock, start_s=0, seconds=190)  # no shutdown: a crash
    data = json.loads(history_path().read_text(encoding="utf-8"))
    assert data["current"]["counted"] and data["current"]["position_ms"] >= 160_000
    again = PlayHistory(HistoryConfig())
    _play(again, "A", clock, start_s=20, seconds=110)
    assert _stored() == ["A", "A"]


def test_a_song_on_repeat_is_kept_once_per_play(hist, clock, caplog):
    caplog.set_level(logging.INFO, logger="refrain.history")
    _play(hist, "A", clock, start_s=0, seconds=196)
    _play(hist, "A", clock, start_s=0, seconds=110)
    assert _titles(hist) == ["A", "A"]
    assert _stored() == ["A", "A"]
    assert "History: Artist — A started over — a new play" in caplog.text


def test_a_restart_the_player_shows_is_a_new_play_once_it_counted(hist, clock):
    # A source without a usable position: only its start frame tells.
    _counted(hist, "A", clock)
    hist.update(_t("A"), SONG_MS, now_wall=clock.wall, now_mono=clock.mono, restarted=True)
    _counted(hist, "A", clock)
    assert _stored() == ["A", "A"]


def test_a_restart_before_it_counted_is_the_same_play(hist, clock):
    _feed(hist, _t("A"), 20, clock)
    hist.update(_t("A"), SONG_MS, now_wall=clock.wall, now_mono=clock.mono, restarted=True)
    _feed(hist, _t("A"), 90, clock)
    assert _stored() == ["A"]


def test_going_back_to_the_start_before_it_counted_is_the_same_play(hist, clock):
    _play(hist, "A", clock, start_s=0, seconds=40)
    _play(hist, "A", clock, start_s=0, seconds=70)  # 40 + 70 s ≥ half
    assert _titles(hist) == ["A"]
    assert _stored() == ["A"]


def test_remove_one_song(hist, clock):
    for title in "ABC":
        _counted(hist, title, clock)
    b = next(e for e in hist.snapshot().entries if e.title == "B")
    assert hist.remove(b.started_at, b.title, b.artist)
    assert _stored() == ["C", "A"]
    assert not hist.remove(b.started_at, b.title, b.artist)


def test_removing_the_playing_song_keeps_it_out_until_the_next(hist, clock):
    _feed(hist, _t("A"), 10, clock)
    a = hist.snapshot().entries[0]
    assert hist.remove(a.started_at, a.title, a.artist)
    _feed(hist, _t("A"), 120, clock)  # still playing — and heard long enough
    assert _titles(hist) == []
    assert _stored() == []
    _feed(hist, _t("B"), 2, clock)
    assert _titles(hist) == ["B"]


def test_skipping_never_touches_the_disk(hist, clock):
    for title in "ABCDE":
        _feed(hist, _t(title), 20, clock)
    assert not history_path().exists()


def test_damaged_file_is_moved_aside_not_overwritten(xdg_tmp, caplog):
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text("not json at all", encoding="utf-8")
    h = PlayHistory(HistoryConfig())
    assert _titles(h) == []
    bad = history_path().with_suffix(".json.bad")
    assert bad.read_text(encoding="utf-8") == "not json at all"
    assert "damaged" in caplog.text


def test_unreadable_rows_are_skipped(xdg_tmp):
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(
        json.dumps({"version": 1, "entries": [{"title": "A"}, {"title": ""}, "junk"]}),
        encoding="utf-8",
    )
    assert _titles(PlayHistory(HistoryConfig())) == ["A"]


def test_file_is_owner_only(hist, clock):
    _counted(hist, "A", clock)
    assert os.stat(history_path()).st_mode & 0o777 == 0o600


# --------------------------------------------------------------------------- #
# Last.fm mark                                                                 #
# --------------------------------------------------------------------------- #


def test_scrobbled_mark_is_kept(hist, clock):
    _counted(hist, "A", clock)
    _feed(hist, _t("B"), 2, clock)
    assert hist.mark_scrobbled("Artist", "A")
    assert [e.scrobbled for e in hist.snapshot().entries] == [False, True]
    assert not hist.mark_scrobbled("Artist", "A")  # already marked
    assert PlayHistory(HistoryConfig()).snapshot().entries[0].scrobbled


def test_the_scrobbler_reports_what_it_queued(tmp_path):
    from refrain.config import LastfmConfig
    from refrain.scrobble import Scrobbler
    from refrain.scrobble_queue import ScrobbleQueue

    seen = []
    sc = Scrobbler(
        LastfmConfig(enabled=True, api_key="K", shared_secret="S", session_key="SK"),
        queue=ScrobbleQueue(path=tmp_path / "q.jsonl"),
        on_queued=lambda artist, title: seen.append((artist, title)),
    )
    sc._client = type("C", (), {"update_now_playing": lambda *a, **k: None})()
    sc._submit = lambda *a, **k: None  # no executor work at all
    clock = Clock()
    for _ in range(60):
        sc.update(_t("A"), SONG_MS, privacy_off=False, now_wall=clock.wall, now_mono=clock.mono)
        clock.wall += 2
        clock.mono += 2
    sc.update(_t("B"), SONG_MS, privacy_off=False, now_wall=clock.wall, now_mono=clock.mono)
    sc.shutdown()
    assert seen == [("Artist", "A")]


# --------------------------------------------------------------------------- #
# Links                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tab_url,kept",
    [
        ("https://music.apple.com/de/album/x/1?i=2", True),
        ("https://music.apple.com/de/song/x/2", True),
        # A playlist or album tab is not the song — a click would land
        # somewhere else, so it's left to the search fallback.
        ("https://music.apple.com/de/playlist/mix/pl.u-1", False),
        ("https://music.apple.com/de/album/x/1", False),
        ("", False),
    ],
)
def test_a_tab_url_is_kept_only_when_it_is_the_song(hist, clock, tab_url, kept):
    track = _t("A")
    track.url = tab_url
    hist.update(track, SONG_MS, now_wall=clock.wall, now_mono=clock.mono)
    assert hist.snapshot().entries[0].url == (tab_url if kept else "")


def test_the_catalog_page_wins_over_the_tab(hist, clock):
    track = _t("A")
    track.url = "https://music.apple.com/de/album/x/1?i=2"
    hist.update(
        track, SONG_MS, song_url="https://music.apple.com/us/song/a/9", now_wall=0, now_mono=0
    )
    assert hist.snapshot().entries[0].url == "https://music.apple.com/us/song/a/9"


def test_a_snapshot_is_a_copy_the_caller_cannot_change_the_history_through(hist, clock):
    _counted(hist, "Glass Tides", clock)
    shown = hist.snapshot().entries[0]
    shown.title = "Paper Satellites"
    shown.scrobbled = True
    again = hist.snapshot().entries[0]
    assert (again.title, again.scrobbled) == ("Glass Tides", False)
    assert again is not shown
    # Nor is it a live view: a later change leaves the earlier copy as it was.
    assert hist.mark_scrobbled("Artist", "Glass Tides")
    assert not again.scrobbled
    assert hist.snapshot().entries[0].scrobbled


# --------------------------------------------------------------------------- #
# The time to show right after a restart                                      #
# --------------------------------------------------------------------------- #


def _shown(h, title, clock, seconds, *, from_s=0, playing=True):
    """Poll once a second, with Refrain showing the time as it goes."""
    status = PlaybackStatus.PLAYING if playing else PlaybackStatus.PAUSED
    for i in range(seconds + 1):
        h.update(
            _t(title, status=status),
            SONG_MS,
            now_wall=clock.wall,
            now_mono=clock.mono,
            shown_ms=(from_s + (i if playing else 0)) * 1000,
        )
        if i < seconds:
            clock.wall += 1
            clock.mono += 1


def test_after_a_crash_the_estimate_counts_on_from_the_last_save(hist, clock):
    _shown(hist, "A", clock, 65)  # saved at 1:00; the crash comes 5 s later
    clock.wall += 3
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("A"), clock.wall) == 68_000


def test_after_a_quit_the_estimate_counts_on_from_the_quit(hist, clock):
    _shown(hist, "A", clock, 65)
    hist.shutdown()
    clock.wall += 3
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("A"), clock.wall) == 68_000


def test_a_save_older_than_a_minute_gives_no_estimate(hist, clock):
    _shown(hist, "A", clock, 65)
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("A"), clock.wall + 55) == 120_000
    assert again.resume_estimate_ms(_t("A"), clock.wall + 56) is None


def test_another_song_gets_no_estimate(hist, clock):
    _shown(hist, "A", clock, 65)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("B"), clock.wall + 3) is None


def test_a_song_paused_at_the_quit_is_estimated_where_it_stopped(hist, clock):
    _shown(hist, "A", clock, 40)
    _shown(hist, "A", clock, 10, from_s=40, playing=False)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("A"), clock.wall + 3) == 40_000


def test_no_estimate_where_refrain_showed_no_time(hist, clock):
    _feed(hist, _t("A"), 64, clock)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    assert again.resume_estimate_ms(_t("A"), clock.wall + 3) is None


def test_the_estimate_is_only_for_the_first_song_after_the_restart(hist, clock):
    _shown(hist, "A", clock, 65)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    again.update(_t("A"), SONG_MS, now_wall=clock.wall + 3, now_mono=clock.mono + 3)
    assert again.resume_estimate_ms(_t("A"), clock.wall + 3) is None
