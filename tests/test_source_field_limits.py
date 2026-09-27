"""What a player reports is untrusted: length, range and control characters."""

from __future__ import annotations

import pytest

from refrain.sources.base import clean_field
from refrain.sources.bluetooth import _safe_ms as bt_ms
from refrain.sources.mpris import _FIELD_MAX, _safe_ms, _safe_str, _to_str_list


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Song\nFake log line", "SongFake log line"),  # forges a second log line
        ("Song\x1b[31m", "Song[31m"),  # terminal escape on stderr
        ("Song‮reversed", "Songreversed"),  # reverses what a reader sees
        ("Song\x07\x00", "Song"),
        ("‏عربي", "‏عربي"),  # a real right-to-left mark stays
        ("", ""),
    ],
)
def test_control_characters_are_taken_out(raw, expected):
    assert clean_field(raw) == expected


def test_a_title_is_capped():
    assert len(_safe_str("x" * 5000)) == _FIELD_MAX


def test_an_artist_list_is_capped_as_a_whole():
    """`xesam:artist` is a list; joining an uncapped one undoes the cap."""
    joined = ", ".join(_to_str_list(["y" * 900] * 2000))
    assert len(joined) < 32 * (_FIELD_MAX + 2)


@pytest.mark.parametrize("fn", [_safe_ms, bt_ms])
@pytest.mark.parametrize(
    "raw,expected",
    [
        (200_000, 200_000),
        (0, 0),
        (-5, 0),  # a position before the start
        (9_223_372_036_854_775_807, 0),  # int64 max, which Chromium reports
        (25 * 60 * 60 * 1000, 0),  # longer than a day
        ("not a number", 0),
        (None, 0),
    ],
)
def test_a_time_outside_a_day_counts_as_unknown(fn, raw, expected):
    assert fn(raw) == expected


def test_microseconds_are_converted_before_the_range_check():
    """MPRIS counts in microseconds — a 3:36 song is 216 million of them."""
    assert _safe_ms(215_914_000, 1000) == 215_914
