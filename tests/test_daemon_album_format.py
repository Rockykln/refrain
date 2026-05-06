"""Album-name cleanup for the third Discord RPC line.

The MPRIS metadata sometimes embeds the artist into the album name
('W&W & Scooter - Sun Rise') or duplicates the title; without filtering
the third line just echoes what's already on line 2.
"""

from __future__ import annotations

from refrain.daemon import _format_album_for_display


def test_strips_artist_prefix_with_dash():
    assert _format_album_for_display("W&W & Scooter - Sun Rise", "W&W & Scooter", "Sun Rise (Dauner Remix)") == "Sun Rise"


def test_strips_artist_prefix_with_em_dash():
    assert _format_album_for_display("Daft Punk — Random Access Memories", "Daft Punk", "Get Lucky") == "Random Access Memories"


def test_strips_artist_suffix():
    assert _format_album_for_display("Random Access Memories - Daft Punk", "Daft Punk", "Get Lucky") == "Random Access Memories"


def test_returns_empty_when_album_equals_title():
    assert _format_album_for_display("Get Lucky", "Daft Punk", "Get Lucky") == ""


def test_case_insensitive_dedup_against_title():
    assert _format_album_for_display("get lucky", "Daft Punk", "Get Lucky") == ""


def test_returns_album_unchanged_when_no_artist_prefix():
    assert _format_album_for_display("Random Access Memories", "Daft Punk", "Get Lucky") == "Random Access Memories"


def test_handles_no_artist():
    """No artist context means nothing to strip — just trim."""
    assert _format_album_for_display("  Some Album  ", "", "Track") == "Some Album"


def test_handles_empty_album():
    assert _format_album_for_display("", "Daft Punk", "Get Lucky") == ""


def test_keeps_album_when_only_partial_artist_match():
    """Don't strip when the artist appears mid-album (not as a prefix/suffix)."""
    assert _format_album_for_display("Hits with Daft Punk", "Daft Punk", "Get Lucky") == "Hits with Daft Punk"
