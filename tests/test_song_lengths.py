"""Lengths measured from whole plays, for songs the catalog has none for."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from refrain.config import Config  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.song_lengths import MAX_ENTRIES, LearnedLengths, song_key  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.timing import PositionState, PositionTier  # noqa: E402

A = ("moi", "f**k dich", "LOVE IS A BITCH")


def _lengths(tmp_path):
    return LearnedLengths(path=tmp_path / "lengths.txt")


def test_a_featuring_credit_is_the_same_song():
    assert song_key("moi", "f**k dich (feat. date)", "LOVE IS A BITCH") == song_key(*A)


def test_another_album_is_another_song():
    """A live version runs longer than the studio one."""
    assert song_key("Lindemann", "Knebel", "Live") != song_key("Lindemann", "Knebel", "Zunge")


def test_one_play_is_not_yet_a_length(tmp_path):
    lengths = _lengths(tmp_path)
    assert lengths.observe(*A, 157_000) is False
    assert lengths.get_ms(*A) == 0


def test_two_plays_that_agree_make_a_length(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.observe(*A, 158_400) is True  # within the tolerance
    assert lengths.get_ms(*A) == 157_000


def test_a_play_that_ended_early_is_overwritten_not_averaged(tmp_path):
    """A skip 40 s in must not drag the song's length down with it."""
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 40_000)
    assert lengths.get_ms(*A) == 0
    lengths.observe(*A, 157_000)
    assert lengths.get_ms(*A) == 0, "the 40 s reading replaced it — count again from one"
    lengths.observe(*A, 157_000)
    assert lengths.get_ms(*A) == 157_000


def test_lengths_no_song_has_are_ignored(tmp_path):
    lengths = _lengths(tmp_path)
    for played in (0, 29_000, 3_600_001):
        assert lengths.observe(*A, played) is False
    assert lengths.get_ms(*A) == 0


def test_it_survives_a_restart_and_holds_no_titles(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)
    text = (tmp_path / "lengths.txt").read_text(encoding="utf-8")
    assert text.split() == [song_key(*A), "157", "2"]
    assert "dich" not in text and "moi" not in text
    assert (tmp_path / "lengths.txt").stat().st_mode & 0o777 == 0o600
    assert _lengths(tmp_path).get_ms(*A) == 157_000


def test_a_corrupt_line_costs_only_itself(tmp_path):
    (tmp_path / "lengths.txt").write_text(
        f"nonsense\n{song_key(*A)} 157 2\nffff 12 x\n", encoding="utf-8"
    )
    assert _lengths(tmp_path).get_ms(*A) == 157_000


def test_the_least_recently_heard_song_goes_first(tmp_path):
    lengths = _lengths(tmp_path)
    for i in range(MAX_ENTRIES):
        lengths.observe("Art", f"T{i}", "", 100_000)
    lengths.observe("Art", "T0", "", 100_000)  # heard again, so it stays
    lengths.observe("Art", "new", "", 100_000)
    assert len(lengths._entries) == MAX_ENTRIES
    assert song_key("Art", "T0", "") in lengths._entries
    assert song_key("Art", "T1", "") not in lengths._entries


# --------------------------------------------------- what the daemon measures


def _worker(tmp_path, *, catalog_ms=0, tier=PositionTier.COMPUTED, control_at=0.0):
    lengths = _lengths(tmp_path)
    worker = SimpleNamespace(
        _config=Config(),
        _song_lengths=lengths,
        _prev_track=TrackInfo(
            source="mpris",
            title="f**k dich",
            artist="moi",
            album="LOVE IS A BITCH",
            status=PlaybackStatus.PLAYING,
        ),
        _position_state=PositionState(track_key="mpris|x", anchored=True, started_at=1000.0),
        _position_tier=tier,
        _control_at=control_at,
        _cover_fetcher=SimpleNamespace(get_duration_ms=lambda *_: catalog_ms),
    )
    worker._catalog_duration_ms = lambda t: DaemonWorker._catalog_duration_ms(worker, t)
    return worker, lengths


def _measure(worker, now=1157.0):
    DaemonWorker._measure_previous_song(worker, now)


def test_a_song_played_to_its_end_is_measured(tmp_path):
    worker, lengths = _worker(tmp_path)
    _measure(worker)
    _measure(worker)
    assert lengths.get_ms(*A) == 157_000


def test_a_song_we_skipped_ourselves_is_not_measured(tmp_path):
    worker, lengths = _worker(tmp_path, control_at=1156.0)
    _measure(worker)
    _measure(worker)
    assert lengths.get_ms(*A) == 0


@pytest.mark.parametrize(
    "kw", [{"tier": PositionTier.UNKNOWN}, {"catalog_ms": 157_000}], ids=["no position", "catalog"]
)
def test_nothing_to_learn_is_not_learned(tmp_path, kw):
    worker, lengths = _worker(tmp_path, **kw)
    _measure(worker)
    _measure(worker)
    assert lengths.get_ms(*A) == 0


def test_a_song_already_playing_at_startup_is_not_measured(tmp_path):
    """Nothing places its start, so the time since says nothing."""
    worker, lengths = _worker(tmp_path)
    worker._position_state = PositionState(track_key="mpris|x", anchored=False)
    _measure(worker)
    _measure(worker)
    assert lengths.get_ms(*A) == 0


def test_a_measured_length_is_what_the_scrobbler_is_given(tmp_path):
    worker, lengths = _worker(tmp_path)
    _measure(worker)
    _measure(worker)
    worker._known_duration_ms = lambda t: DaemonWorker._known_duration_ms(worker, t)
    assert worker._known_duration_ms(worker._prev_track) == 157_000
    assert DaemonWorker._duration_for(worker, worker._prev_track) == (157_000, False)
