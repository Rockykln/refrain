"""How the scrobble queue keeps its file: appends, compaction, older files."""

from __future__ import annotations

import json
import os

import refrain.scrobble_queue as mod
from refrain.scrobble_queue import ScrobbleQueue

PLAYS = [
    ("Neon Harbor", "Glass Tides", "Low Light"),
    ("Marlow Vance", "Paper Satellites", "Signals"),
    ("The Quiet Hours", "Northbound", ""),
    ("Ilse Moreau", "Silk Road Radio", "Caravan"),
    ("Kite Theory", "Overexposed", "Afterimage (Deluxe Edition)"),
    ("Juniper Lane", "Rooftop Weather", "Rooftop Weather"),
    ("Oskar Lind", "Ferrous", "Iron Garden"),
    ("Velvet Static", "Late Train Home", "Commuter"),
]


def _play(i: int, account: str | None = "listener") -> dict:
    artist, track, album = PLAYS[i % len(PLAYS)]
    item = {
        "artist": artist,
        "track": track,
        "album": album,
        "timestamp": 1_700_000_000 + i * 240,
        "duration": 214,
    }
    if account:
        item["account"] = account
    return item


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tracks(items) -> list[tuple[str, int]]:
    return [(it["track"], it["timestamp"]) for it in items]


def test_a_new_play_is_appended_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path)
    q.enqueue(_play(0))
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(mod.os, "replace", lambda *a: (replaced.append(a), real_replace(*a))[1])
    for i in range(1, 5):
        assert q.enqueue(_play(i)) is True
    assert replaced == []
    assert _tracks(_rows(path)) == _tracks(q.pending())
    assert _tracks(ScrobbleQueue(path=path).pending()) == _tracks(q.pending())


def test_the_file_stays_owner_only(tmp_path):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path)
    for i in range(3):
        q.enqueue(_play(i))
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_full_queue_is_compacted_and_reloads_the_newest(tmp_path):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path, max_entries=10)
    for i in range(40):
        q.enqueue(_play(i))
    kept = _tracks(q.pending())
    assert kept == _tracks(_play(i) for i in range(30, 40))
    # Never more than the cap plus its slack on disk.
    assert len(_rows(path)) <= 11
    assert _tracks(ScrobbleQueue(path=path, max_entries=10).pending()) == kept


def test_a_play_dropped_by_the_cap_can_be_queued_again(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl", max_entries=3)
    for i in range(4):
        q.enqueue(_play(i))
    assert q.enqueue(_play(0)) is True
    assert q.enqueue(_play(3)) is False


def test_dedup_survives_a_reload_and_a_drain(tmp_path):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path)
    for i in range(4):
        q.enqueue(_play(i))
    q2 = ScrobbleQueue(path=path)
    assert q2.enqueue(_play(2)) is False
    q2.drain(lambda batch: len(batch), batch_size=2)
    assert len(q2) == 0
    assert q2.enqueue(_play(2)) is True
    assert _tracks(ScrobbleQueue(path=path).pending()) == _tracks([_play(2)])


def test_dropping_another_accounts_plays_keeps_dedup_right(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    q.enqueue(_play(0, account="listener"))
    q.enqueue(_play(1, account="other"))
    assert q.drop_other_accounts("listener") == 1
    assert q.enqueue(_play(1, account="other")) is True
    assert q.enqueue(_play(0, account="listener")) is False


def test_an_older_queue_file_loads_and_keeps_its_rows(tmp_path):
    """Written by earlier versions: no account, loose spacing, a duplicate
    play, a torn last line."""
    path = tmp_path / "q.jsonl"
    old = [
        {"artist": " Neon Harbor ", "track": "Glass Tides", "timestamp": 1_700_000_000},
        {
            "artist": "Marlow Vance",
            "track": "Paper Satellites",
            "album": "Signals",
            "timestamp": "1700000240",
            "duration": 187,
        },
        {
            "artist": "Marlow Vance",
            "track": "Paper Satellites",
            "album": "Signals",
            "timestamp": 1_700_000_240,
            "duration": 187,
        },
    ]
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in old) + '{"artist": "Ilse Mor', encoding="utf-8"
    )
    q = ScrobbleQueue(path=path)
    assert [(it["artist"], it["track"], it["timestamp"]) for it in q.pending()] == [
        ("Neon Harbor", "Glass Tides", 1_700_000_000),
        ("Marlow Vance", "Paper Satellites", 1_700_000_240),
        ("Marlow Vance", "Paper Satellites", 1_700_000_240),
    ]
    assert "account" not in q.pending()[0]
    assert q.enqueue(_play(3)) is True
    # The torn line is gone rather than glued to the new row.
    rows = _rows(path)
    assert len(rows) == 4
    assert rows[-1]["track"] == "Silk Road Radio"
    assert len(ScrobbleQueue(path=path)) == 4


def test_a_duplicate_in_an_old_file_still_dedups_after_one_copy_goes(tmp_path):
    path = tmp_path / "q.jsonl"
    row = json.dumps(_play(1, account=None))
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    q = ScrobbleQueue(path=path)
    q.drain(lambda batch: len(batch), batch_size=1)
    assert len(q) == 0
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    q = ScrobbleQueue(path=path)
    calls = {"n": 0}

    def once(batch):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("offline")
        return len(batch)

    q.drain(once, batch_size=1)
    assert len(q) == 1
    assert q.enqueue(_play(1, account=None)) is False


def test_a_file_removed_underneath_gets_the_whole_queue_back(tmp_path):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path)
    for i in range(3):
        q.enqueue(_play(i))
    path.unlink()
    q.enqueue(_play(3))
    assert _tracks(_rows(path)) == _tracks(q.pending())
    assert len(q) == 4


def test_a_failed_append_is_repaired_by_the_next_write(tmp_path, monkeypatch):
    path = tmp_path / "q.jsonl"
    q = ScrobbleQueue(path=path)
    q.enqueue(_play(0))
    real_open = os.open

    def refuse(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "open", refuse)
    assert q.enqueue(_play(1)) is True
    monkeypatch.setattr(mod.os, "open", real_open)
    assert q.enqueue(_play(2)) is True
    assert _tracks(_rows(path)) == _tracks(_play(i) for i in range(3))
