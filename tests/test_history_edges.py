"""Edge cases of the recently-played history: damaged files, failed writes, resume corner cases."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from refrain.config import HistoryConfig
from refrain.history import PlayHistory, history_path
from tests.test_history import (  # noqa: F401
    SONG_MS,
    _counted,
    _feed,
    _stored,
    _t,
    _titles,
    clock,
    hist,
)


def test_a_non_numeric_saved_duration_falls_back_to_zero(xdg_tmp):
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(
        json.dumps({"version": 1, "entries": [{"title": "Glass Tides", "duration_ms": "oops"}]}),
        encoding="utf-8",
    )
    entry = PlayHistory(HistoryConfig()).snapshot().entries[0]
    assert entry.title == "Glass Tides"
    assert entry.duration_ms == 0


def test_a_saved_current_song_without_a_title_is_ignored_on_restart(xdg_tmp, clock):  # noqa: F811
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [],
                "current": {
                    "entry": {"title": ""},
                    "counted": False,
                    "played_ms": 50_000,
                    "saved_at": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    h = PlayHistory(HistoryConfig())
    # No resume to carry on with — the song is heard as a brand-new play.
    _feed(h, _t("Glass Tides"), 20, clock)
    assert _titles(h) == ["Glass Tides"]
    _feed(h, _t("Glass Tides"), 90, clock)
    assert _stored() == ["Glass Tides"]


def test_a_saved_current_song_with_a_broken_played_ms_is_ignored_on_restart(xdg_tmp, clock):  # noqa: F811
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [],
                "current": {
                    "entry": {"title": "Glass Tides", "artist": "Neon Harbor"},
                    "counted": False,
                    "played_ms": "oops",
                    "saved_at": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    h = PlayHistory(HistoryConfig())
    _feed(h, _t("Glass Tides"), 20, clock)
    assert _titles(h) == ["Glass Tides"]


def test_trimming_on_startup_still_saves_the_pending_resume(xdg_tmp):
    history_path().parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"title": t, "artist": "Artist", "album": "Album", "duration_ms": SONG_MS} for t in "ABCDE"
    ]
    history_path().write_text(
        json.dumps(
            {
                "version": 1,
                "entries": entries,
                "current": {
                    "entry": {"title": "Glass Tides", "artist": "Neon Harbor"},
                    "counted": False,
                    "played_ms": 50_000,
                    "position_ms": None,
                    "saved_at": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    PlayHistory(HistoryConfig(max_entries=3))
    data = json.loads(history_path().read_text(encoding="utf-8"))
    # A lower limit drops the oldest songs on disk right away...
    assert [e["title"] for e in data["entries"]] == ["A", "B", "C"]
    # ...without losing the song that was still playing, for a later resume.
    assert data["current"]["entry"]["title"] == "Glass Tides"


def test_a_length_correction_after_counting_is_saved(hist, clock):  # noqa: F811
    _counted(hist, "Glass Tides", clock)
    hist.update(_t("Glass Tides"), 210_000, now_wall=clock.wall, now_mono=clock.mono)
    stored = json.loads(history_path().read_text(encoding="utf-8"))["entries"][0]
    assert stored["duration_ms"] == 210_000


def test_a_song_page_found_after_counting_is_saved(hist, clock):  # noqa: F811
    _counted(hist, "Glass Tides", clock)
    hist.update(
        _t("Glass Tides"),
        SONG_MS,
        song_url="https://music.apple.com/us/song/a/9",
        now_wall=clock.wall,
        now_mono=clock.mono,
    )
    stored = json.loads(history_path().read_text(encoding="utf-8"))["entries"][0]
    assert stored["url"] == "https://music.apple.com/us/song/a/9"


def test_marking_scrobbled_does_nothing_when_history_is_off(xdg_tmp):
    h = PlayHistory(HistoryConfig(enabled=False))
    assert not h.mark_scrobbled("Neon Harbor", "Glass Tides")


def test_marking_a_song_not_in_the_history_does_nothing(hist, clock):  # noqa: F811
    _counted(hist, "Glass Tides", clock)
    assert not hist.mark_scrobbled("Someone Else", "Not There")


def test_removing_does_nothing_when_history_is_off(xdg_tmp):
    h = PlayHistory(HistoryConfig(enabled=False))
    assert not h.remove(0, "Glass Tides", "Neon Harbor")


def test_shutdown_does_nothing_when_history_is_off(xdg_tmp):
    h = PlayHistory(HistoryConfig(enabled=False))
    h.shutdown()
    assert not history_path().exists()


def test_removing_a_song_that_would_resume_also_forgets_the_resume(hist, clock):  # noqa: F811
    _counted(hist, "Glass Tides", clock)
    hist.shutdown()
    again = PlayHistory(HistoryConfig())
    entry = again.snapshot().entries[0]
    assert again.remove(entry.started_at, entry.title, entry.artist)
    # Without forgetting the pending resume, the very next poll would
    # silently bring the removed song back as "already in the list".
    _feed(again, _t("Glass Tides"), 20, clock)
    assert _titles(again) == ["Glass Tides"]
    _feed(again, _t("Glass Tides"), 90, clock)
    assert _stored() == ["Glass Tides"]


def test_turning_history_back_on_after_off_works_and_reports_a_change(hist, clock):  # noqa: F811
    _counted(hist, "Glass Tides", clock)
    hist.reconfigure(HistoryConfig(enabled=False))
    assert hist.reconfigure(HistoryConfig(enabled=True))
    assert _titles(hist) == []
    _feed(hist, _t("Salt Flats"), 2, clock)
    assert _titles(hist) == ["Salt Flats"]


def test_raising_the_limit_does_not_drop_anything(hist, clock, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger="refrain.history")
    _counted(hist, "Glass Tides", clock)
    assert hist.reconfigure(HistoryConfig(max_entries=50))
    assert _titles(hist) == ["Glass Tides"]
    assert "History limit is now 50" in caplog.text


def test_an_unreadable_history_file_starts_empty_without_crashing(xdg_tmp, caplog):
    caplog.set_level(logging.WARNING, logger="refrain.history")
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().mkdir()  # a directory where a file is expected: read_text() fails
    h = PlayHistory(HistoryConfig())
    assert h.snapshot().entries == ()
    assert "unreadable" in caplog.text


def test_a_history_file_without_an_entries_list_is_moved_aside(xdg_tmp, caplog):
    caplog.set_level(logging.WARNING, logger="refrain.history")
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(json.dumps({"version": 1}), encoding="utf-8")
    h = PlayHistory(HistoryConfig())
    assert h.snapshot().entries == ()
    assert history_path().with_suffix(".json.bad").exists()
    assert "damaged" in caplog.text


def test_a_resume_song_no_longer_in_the_list_is_forgotten(xdg_tmp, clock):  # noqa: F811
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [],  # the song fell out of the list already
                "current": {
                    "entry": {"title": "Glass Tides", "artist": "Neon Harbor"},
                    "counted": True,
                    "played_ms": 120_000,
                    "saved_at": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    h = PlayHistory(HistoryConfig())
    _feed(h, _t("Glass Tides"), 20, clock)
    assert _titles(h) == ["Glass Tides"]
    _feed(h, _t("Glass Tides"), 90, clock)
    assert _stored() == ["Glass Tides"]


def test_a_failed_write_does_not_crash_and_keeps_the_list_for_this_session(
    hist,  # noqa: F811
    clock,  # noqa: F811
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.WARNING, logger="refrain.history")
    import refrain.history as history_mod

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(history_mod.os, "replace", _boom)
    _counted(hist, "Glass Tides", clock)
    assert _titles(hist) == ["Glass Tides"]
    assert "Could not save history" in caplog.text


def test_a_history_file_that_cannot_be_deleted_does_not_crash_turning_history_off(
    hist,  # noqa: F811
    clock,  # noqa: F811
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.WARNING, logger="refrain.history")
    _counted(hist, "Glass Tides", clock)
    target = history_path()
    original_unlink = Path.unlink

    def _guarded_unlink(self, *a, **k):
        if self == target:
            raise PermissionError("no permission")
        return original_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _guarded_unlink)
    assert hist.reconfigure(HistoryConfig(enabled=False))
    assert "Could not delete history file" in caplog.text
