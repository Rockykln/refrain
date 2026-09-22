"""Pins the on-disk format, key hashing, and exact boundaries for song_lengths.

Without these, a change to the key format or the agreement tolerance would
silently make every user's learned lengths useless while all other tests
still pass.
"""

from __future__ import annotations

import threading

import refrain.song_lengths as sl
from refrain.song_lengths import (
    AGREES_WITHIN_S,
    MAX_LENGTH_S,
    MIN_LENGTH_S,
    LearnedLengths,
    song_key,
)

A = ("Kite Theory", "Overexposed", "Afterimage")


def _lengths(tmp_path):
    return LearnedLengths(path=tmp_path / "lengths.txt")


# --------------------------------------------------------------- key format


def test_song_key_is_the_hash_the_format_was_built_on():
    """A golden value: changing the separator, field order or hash would
    move every key, and no other test would notice."""
    assert song_key(*A) == "ce4d07533d5133bf"
    assert len(song_key(*A)) == 16


def test_song_key_ignores_case_whitespace_and_punctuation():
    assert song_key("Kite Theory", "OVEREXPOSED", "Afterimage") == song_key(*A)
    assert song_key("Kite  Theory", " Overexposed ", "After-image") == song_key(*A)


def test_song_key_ignores_accents():
    assert song_key("Cafe", "X", "Y") == song_key("Café", "X", "Y")


def test_song_key_treats_ampersand_as_and():
    assert song_key("Simon & Garfunkel", "X", "Y") == song_key("Simon and Garfunkel", "X", "Y")


# ------------------------------------------------------------- file format


def test_the_entries_file_is_written_in_todays_exact_format(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)
    text = (tmp_path / "lengths.txt").read_text(encoding="utf-8")
    assert text == f"{song_key(*A)} 157 2\n"


def test_a_file_written_in_todays_format_loads_back(tmp_path):
    (tmp_path / "lengths.txt").write_text(f"{song_key(*A)} 157 2\n", encoding="utf-8")
    lengths = _lengths(tmp_path)
    assert lengths.get_ms(*A) == 157_000


def test_the_lastfm_file_is_written_in_todays_exact_format(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.add_reference(*A, 157_000)
    text = (tmp_path / "lengths_lastfm.txt").read_text(encoding="utf-8")
    assert text == f"{song_key(*A)} 157\n"


def test_a_lastfm_file_written_in_todays_format_loads_back(tmp_path):
    (tmp_path / "lengths_lastfm.txt").write_text(f"{song_key(*A)} 157\n", encoding="utf-8")
    lengths = _lengths(tmp_path)
    assert lengths._references == {song_key(*A): 157}


def test_the_lastfm_file_permissions_are_locked_down(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.add_reference(*A, 157_000)
    mode = (tmp_path / "lengths_lastfm.txt").stat().st_mode & 0o777
    assert mode == 0o600


# ----------------------------------------------------- loading stale lines


def test_stored_lengths_outside_the_allowed_range_are_dropped(tmp_path):
    key = song_key(*A)
    other = song_key("Kite Theory", "Other", "Afterimage")
    third = song_key("Kite Theory", "Third", "Afterimage")
    (tmp_path / "lengths.txt").write_text(
        f"{key} 29 2\n{other} 3601 2\n{third} 157 2\n", encoding="utf-8"
    )
    lengths = _lengths(tmp_path)
    assert key not in lengths._entries
    assert other not in lengths._entries
    assert third in lengths._entries


def test_stored_lengths_at_the_allowed_range_edges_are_kept(tmp_path):
    key = song_key(*A)
    other = song_key("Kite Theory", "Other", "Afterimage")
    (tmp_path / "lengths.txt").write_text(f"{key} 30 2\n{other} 3600 2\n", encoding="utf-8")
    lengths = _lengths(tmp_path)
    assert lengths._entries[key] == (30, 2)
    assert lengths._entries[other] == (3600, 2)


def test_a_stored_length_with_zero_confirmations_is_dropped(tmp_path):
    key = song_key(*A)
    (tmp_path / "lengths.txt").write_text(f"{key} 157 0\n", encoding="utf-8")
    lengths = _lengths(tmp_path)
    assert key not in lengths._entries
    assert lengths.get_ms(*A) == 0


def test_a_stored_length_with_one_confirmation_is_kept_but_unreleased(tmp_path):
    """One play is real data (an unconfirmed length), unlike zero."""
    key = song_key(*A)
    (tmp_path / "lengths.txt").write_text(f"{key} 157 1\n", encoding="utf-8")
    lengths = _lengths(tmp_path)
    assert lengths._entries[key] == (157, 1)
    assert lengths.get_ms(*A) == 0, "not confirmed yet"


def test_a_bad_number_line_is_skipped_not_a_stop_sign(tmp_path):
    """A corrupt line must not cut off every song listed after it."""
    other = song_key("Kite Theory", "Other", "Afterimage")
    (tmp_path / "lengths.txt").write_text(
        f"{song_key(*A)} notanumber 2\n{other} 157 2\n", encoding="utf-8"
    )
    lengths = _lengths(tmp_path)
    assert other in lengths._entries, "the corrupt line above it must not swallow it"


# -------------------------------------------------- exact tolerance edges


def test_two_plays_agree_at_exactly_the_tolerance(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.observe(*A, (157 + AGREES_WITHIN_S) * 1000) is True
    assert lengths.get_ms(*A) == 157_000


def test_two_plays_one_second_past_the_tolerance_do_not_agree(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.observe(*A, (157 + AGREES_WITHIN_S + 1) * 1000) is False
    assert lengths.get_ms(*A) == 0, "replaced, not averaged"


def test_a_challenger_agrees_at_exactly_the_tolerance(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)  # confirmed
    lengths.observe(*A, 200_000)  # first challenger
    assert lengths.get_ms(*A) == 157_000
    assert lengths.observe(*A, (200 + AGREES_WITHIN_S) * 1000) is True
    assert lengths.get_ms(*A) == 200_000


def test_a_challenger_one_second_past_the_tolerance_resets_the_challenge(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)  # confirmed
    lengths.observe(*A, 200_000)  # first challenger: 200
    assert lengths.observe(*A, (200 + AGREES_WITHIN_S + 1) * 1000) is False  # 203, replaces it
    assert lengths.get_ms(*A) == 157_000, "old length stands, new challenger recorded instead"
    # 205 agrees with the new challenger (203) but not the discarded one (200)
    assert lengths.observe(*A, (203 + AGREES_WITHIN_S) * 1000) is True
    assert lengths.get_ms(*A) == 203_000


def test_agreeing_again_clears_a_stale_challenger(tmp_path):
    """Once a challenge is dropped, it must not silently confirm later."""
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)  # confirmed
    lengths.observe(*A, 200_000)  # challenger recorded: 200
    lengths.observe(*A, 157_000)  # agrees again — the 200 challenger must be cleared
    assert lengths.observe(*A, 200_000) is False, "a fresh challenge, not the old one sneaking in"
    assert lengths.get_ms(*A) == 157_000


def test_observe_converts_whole_seconds_not_a_scaled_value(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 3_600_000)  # 3600.000 s
    lengths.observe(*A, 3_600_000)
    assert lengths.get_ms(*A) == 3_600_000


def test_observe_accepts_the_minimum_length_exactly(tmp_path):
    lengths = _lengths(tmp_path)
    assert lengths.observe(*A, MIN_LENGTH_S * 1000) is False  # one play, unconfirmed
    assert song_key(*A) in lengths._entries


def test_observe_accepts_the_maximum_length_exactly(tmp_path):
    lengths = _lengths(tmp_path)
    assert lengths.observe(*A, MAX_LENGTH_S * 1000) is False
    assert song_key(*A) in lengths._entries


def test_a_lastfm_length_confirms_at_exactly_the_tolerance(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.add_reference(*A, (157 + AGREES_WITHIN_S) * 1000) is True
    assert lengths.get_ms(*A) == 157_000


def test_a_lastfm_length_one_second_past_the_tolerance_confirms_nothing(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.add_reference(*A, (157 + AGREES_WITHIN_S + 1) * 1000) is False
    assert lengths.get_ms(*A) == 0


# ------------------------------------------------- CONFIRMATIONS_NEEDED


def test_confirmations_needed_is_the_constant_that_gates_get_ms(tmp_path, monkeypatch):
    """A length is unreleased below the constant and released at it —
    whatever the constant is set to, not a value hardcoded elsewhere."""
    monkeypatch.setattr(sl, "CONFIRMATIONS_NEEDED", 3)
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)
    assert lengths.get_ms(*A) == 0, "only 2 of 3 needed confirmations"
    lengths.observe(*A, 157_000)
    assert lengths.get_ms(*A) == 157_000


def test_confirmations_needed_gates_wants_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "CONFIRMATIONS_NEEDED", 1)
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.wants_reference(*A) is False, "already confirmed with just one play"


# ----------------------------------------------------- MAX_ENTRIES order


def test_lastfm_references_drop_the_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MAX_ENTRIES", 2)
    lengths = _lengths(tmp_path)
    lengths.add_reference("Art", "First", "", 100_000)
    lengths.add_reference("Art", "Second", "", 100_000)
    lengths.add_reference("Art", "Third", "", 100_000)
    assert len(lengths._references) == 2
    assert song_key("Art", "First", "") not in lengths._references
    assert song_key("Art", "Second", "") in lengths._references
    assert song_key("Art", "Third", "") in lengths._references


def test_max_entries_is_the_constant_that_gates_trimming(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MAX_ENTRIES", 2)
    lengths = _lengths(tmp_path)
    lengths.observe("Art", "First", "", 100_000)
    lengths.observe("Art", "Second", "", 100_000)
    lengths.observe("Art", "Third", "", 100_000)
    assert len(lengths._entries) == 2
    assert song_key("Art", "First", "") not in lengths._entries


# ------------------------------------------------------------- concurrency


def test_concurrent_observers_do_not_corrupt_state(tmp_path):
    lengths = _lengths(tmp_path)
    songs = [("Art", f"Song {i}", "") for i in range(50)]

    def worker(song):
        lengths.observe(*song, 100_000)
        lengths.observe(*song, 100_000)

    threads = [threading.Thread(target=worker, args=(s,)) for s in songs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(lengths._entries) == len(songs)
    for song in songs:
        assert lengths.get_ms(*song) == 100_000

    reloaded = _lengths(tmp_path)
    assert reloaded._entries == lengths._entries


# ------------------------------------------------------------- generation


def test_generation_starts_at_zero(tmp_path):
    assert _lengths(tmp_path).generation == 0


def test_generation_bumps_by_exactly_one_per_observed_change(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.generation == 1
    lengths.observe(*A, 157_000)
    assert lengths.generation == 2


def test_generation_bumps_by_exactly_one_per_forget(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    before = lengths.generation
    lengths.forget(*A)
    assert lengths.generation == before + 1


def test_generation_bumps_by_exactly_one_when_a_reference_confirms(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    before = lengths.generation
    lengths.add_reference(*A, 157_000)
    assert lengths.generation == before + 1


# ----------------------------------------------------------------- forget


def test_forget_removes_a_learned_length_and_persists_it(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)
    lengths.forget(*A)
    assert lengths.get_ms(*A) == 0
    assert _lengths(tmp_path).get_ms(*A) == 0, "the removal must survive a reload"


def test_forgetting_a_song_never_learned_does_nothing(tmp_path):
    lengths = _lengths(tmp_path)
    before = lengths.generation
    lengths.forget(*A)
    assert lengths.generation == before, "nothing changed, so nothing to persist"


# --------------------------------------------- the Last.fm "0 = unknown" rule


def test_agrees_locked_treats_a_zero_reference_as_unknown(tmp_path):
    """Even a reading of 0 must not agree with an unknown (0) reference."""
    lengths = _lengths(tmp_path)
    key = song_key(*A)
    lengths._references[key] = 0
    assert lengths._agrees_locked(key, 0) is False


def test_agrees_locked_treats_a_reference_of_one_second_as_known(tmp_path):
    lengths = _lengths(tmp_path)
    key = song_key(*A)
    lengths._references[key] = 1
    assert lengths._agrees_locked(key, 1) is True


def test_agrees_locked_with_no_reference_at_all_is_unknown_too(tmp_path):
    """No entry in the dict must behave exactly like a stored 0, not a stored 1."""
    lengths = _lengths(tmp_path)
    key = song_key(*A)
    assert key not in lengths._references
    assert lengths._agrees_locked(key, 1) is False


def test_add_reference_treats_a_negative_length_as_unknown(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    assert lengths.add_reference(*A, -5_000) is None
    assert lengths._references[song_key(*A)] == 0


def test_add_reference_converts_whole_seconds_not_a_scaled_value(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.add_reference(*A, 2_000_000)  # 2000.000 s
    assert lengths._references[song_key(*A)] == 2000


def test_add_reference_on_an_already_confirmed_length_does_nothing(tmp_path):
    lengths = _lengths(tmp_path)
    lengths.observe(*A, 157_000)
    lengths.observe(*A, 157_000)  # already confirmed
    before = lengths.generation
    assert lengths.add_reference(*A, 157_000) is None
    assert lengths.generation == before, "nothing left to confirm"


def test_touching_a_lastfm_reference_again_refreshes_its_recency(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MAX_ENTRIES", 2)
    lengths = _lengths(tmp_path)
    lengths.add_reference("Art", "First", "", 100_000)
    lengths.add_reference("Art", "Second", "", 100_000)
    lengths.add_reference("Art", "First", "", 100_000)  # touched again, now most recent
    lengths.add_reference("Art", "Third", "", 100_000)
    assert song_key("Art", "Second", "") not in lengths._references, "least recently touched"
    assert song_key("Art", "First", "") in lengths._references
    assert song_key("Art", "Third", "") in lengths._references


# --------------------------------------------------------------- lengths_path


def test_lengths_path_is_state_dir_song_lengths_txt(monkeypatch, tmp_path):
    monkeypatch.setattr(sl, "state_dir", lambda: tmp_path)
    assert sl.lengths_path() == tmp_path / "song_lengths.txt"
