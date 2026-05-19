"""Persistent offline scrobble queue: dedup, cap, persistence, drain."""

from __future__ import annotations

import json

import pytest

from refrain.scrobble_queue import ScrobbleQueue, dedup_key


def _item(track="T", ts=1000, artist="A", album="Al", duration=200):
    return {
        "artist": artist,
        "track": track,
        "album": album,
        "timestamp": ts,
        "duration": duration,
    }


def _q(tmp_path, **kw):
    return ScrobbleQueue(path=tmp_path / "q.jsonl", **kw)


def test_enqueue_and_persist(tmp_path):
    q = _q(tmp_path)
    assert q.enqueue(_item()) is True
    assert len(q) == 1
    # Reload from disk → survives.
    q2 = _q(tmp_path)
    assert len(q2) == 1
    assert q2.pending()[0]["track"] == "T"


def test_dedup_same_track_same_timestamp(tmp_path):
    q = _q(tmp_path)
    assert q.enqueue(_item(track="Song", ts=500)) is True
    assert q.enqueue(_item(track="Song", ts=500)) is False  # duplicate
    assert q.enqueue(_item(track="Song", ts=501)) is True  # different ts → ok
    assert len(q) == 2


def test_normalize_rejects_unusable(tmp_path):
    q = _q(tmp_path)
    assert q.enqueue({"artist": "", "track": "T", "timestamp": 1}) is False
    assert q.enqueue({"artist": "A", "track": "", "timestamp": 1}) is False
    assert q.enqueue({"artist": "A", "track": "T", "timestamp": 0}) is False
    assert len(q) == 0


def test_cap_drops_oldest(tmp_path):
    q = _q(tmp_path, max_entries=3)
    for i in range(5):
        q.enqueue(_item(track=f"t{i}", ts=1000 + i))
    pending = q.pending()
    assert len(pending) == 3
    # Oldest two (t0, t1) dropped; newest kept in order.
    assert [p["track"] for p in pending] == ["t2", "t3", "t4"]


def test_load_tolerates_corrupt_lines(tmp_path):
    p = tmp_path / "q.jsonl"
    good = json.dumps(_item(track="ok", ts=42))
    p.write_text(f"{good}\nnot-json{{\n\n", encoding="utf-8")
    q = ScrobbleQueue(path=p)
    assert len(q) == 1
    assert q.pending()[0]["track"] == "ok"


def test_drain_success_empties_queue(tmp_path):
    q = _q(tmp_path)
    for i in range(3):
        q.enqueue(_item(track=f"t{i}", ts=1000 + i))
    seen = []

    def submit(batch):
        seen.extend(batch)
        return len(batch)

    n = q.drain(submit, batch_size=2)
    assert n == 3
    assert len(q) == 0
    assert [s["track"] for s in seen] == ["t0", "t1", "t2"]
    # Persisted-empty too.
    assert len(_q(tmp_path)) == 0


def test_drain_keeps_queue_on_failure(tmp_path):
    q = _q(tmp_path)
    for i in range(4):
        q.enqueue(_item(track=f"t{i}", ts=1000 + i))

    calls = {"n": 0}

    def submit(batch):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("offline")
        return len(batch)

    n = q.drain(submit, batch_size=2)
    assert n == 2  # first batch went through
    assert len(q) == 2  # second batch kept
    assert [p["track"] for p in q.pending()] == ["t2", "t3"]
    # And it persisted the partial state.
    assert [p["track"] for p in _q(tmp_path).pending()] == ["t2", "t3"]


def test_drain_empty_is_noop(tmp_path):
    q = _q(tmp_path)
    assert q.drain(lambda b: len(b)) == 0


def test_dedup_key_stable_and_distinct():
    a = dedup_key({"artist": "X", "track": "Y", "timestamp": 5})
    assert a == dedup_key({"artist": "X", "track": "Y", "timestamp": 5})
    assert a != dedup_key({"artist": "X", "track": "Y", "timestamp": 6})
    assert len(a) == 64  # sha256 hexdigest


def test_save_failure_does_not_raise(tmp_path, monkeypatch):
    q = _q(tmp_path)
    # Simulate a read-only state dir: os.replace blows up.
    import refrain.scrobble_queue as mod

    def _boom(*a, **kw):
        raise OSError("read-only fs")

    monkeypatch.setattr(mod.os, "replace", _boom)
    # enqueue must still succeed in-memory and NOT raise.
    assert q.enqueue(_item()) is True
    assert len(q) == 1


@pytest.mark.parametrize("bad", [None, "string", 123, []])
def test_load_ignores_non_dict_rows(tmp_path, bad):
    p = tmp_path / "q.jsonl"
    p.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    assert len(ScrobbleQueue(path=p)) == 0
