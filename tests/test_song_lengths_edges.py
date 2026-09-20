"""Learned song lengths: failures reading or writing the state file must cost only the cache."""

from __future__ import annotations

from refrain.song_lengths import LearnedLengths

A = ("Kite Theory", "Overexposed", "Afterimage")


def test_an_unreadable_lengths_file_starts_with_no_learned_lengths(tmp_path):
    # A directory where the file should be can never be read as text.
    broken = tmp_path / "lengths.txt"
    broken.mkdir()
    lengths = LearnedLengths(path=broken)
    assert lengths.get_ms(*A) == 0
    assert lengths._entries == {}


def test_a_save_that_cannot_be_written_does_not_lose_the_in_memory_length(tmp_path, monkeypatch):
    import refrain.song_lengths as mod

    lengths = LearnedLengths(path=tmp_path / "lengths.txt")

    def refuse(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(mod.os, "replace", refuse)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)  # confirms, and tries to persist

    assert lengths.get_ms(*A) == 157_000  # still known this session
    assert not (tmp_path / "lengths.txt.tmp").exists(), "no stale temp file left behind"
    assert not (tmp_path / "lengths.txt").exists(), "nothing was ever committed"
