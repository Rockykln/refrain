"""Scrobble queue edge cases: bad field types, an unreadable file, a failing append, a tiny cap."""

from __future__ import annotations

import json

import refrain.scrobble_queue as mod
from refrain.scrobble_queue import ScrobbleQueue


def _item(track="Glass Tides", ts=1000, artist="Neon Harbor", album="Low Light", duration=200):
    return {
        "artist": artist,
        "track": track,
        "album": album,
        "timestamp": ts,
        "duration": duration,
    }


def test_a_non_numeric_timestamp_makes_the_play_unusable(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    assert q.enqueue(_item(ts="not-a-timestamp")) is False
    assert len(q) == 0


def test_a_non_numeric_duration_is_treated_as_unknown_not_rejected(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    assert q.enqueue(_item(duration="a while")) is True
    assert q.pending()[0]["duration"] == 0


def test_a_queue_file_that_is_a_directory_starts_empty(tmp_path):
    path = tmp_path / "q.jsonl"
    path.mkdir()  # can never be a jsonl file
    q = ScrobbleQueue(path=path)
    assert len(q) == 0
    # A directory in the way must not stop a later play from being queued.
    assert q.enqueue(_item()) is True


def test_a_file_written_with_a_smaller_cap_trims_on_load(tmp_path):
    path = tmp_path / "q.jsonl"
    rows = [_item(track=f"t{i}", ts=1000 + i) for i in range(5)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    q = ScrobbleQueue(path=path, max_entries=3)
    assert [it["track"] for it in q.pending()] == ["t2", "t3", "t4"]


def test_drop_other_accounts_of_nothing_is_a_no_op(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    q.enqueue(_item())
    assert q.drop_other_accounts("") == 0
    assert len(q) == 1


def test_a_write_that_fails_mid_append_is_not_lost_from_memory(tmp_path, monkeypatch):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    q.enqueue(_item(track="first"))

    real_fdopen = mod.os.fdopen

    def refuse_write(fd, *a, **kw):
        mod.os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "fdopen", refuse_write)
    assert q.enqueue(_item(track="second", ts=2000)) is True
    assert [it["track"] for it in q.pending()] == ["first", "second"]

    monkeypatch.setattr(mod.os, "fdopen", real_fdopen)
    assert q.enqueue(_item(track="third", ts=3000)) is True
    # The repair on the next successful write keeps every entry.
    on_disk = [json.loads(line) for line in (tmp_path / "q.jsonl").read_text().splitlines()]
    assert [it["track"] for it in on_disk] == ["first", "second", "third"]
