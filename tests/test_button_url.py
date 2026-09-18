"""The link behind Discord's "Listen on Apple Music" button."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from refrain.daemon import button_url  # noqa: E402


def test_a_tab_address_with_spaces_is_encoded():
    """Plasma reports plain spaces; Discord rejects the activity unless they're encoded."""
    assert (
        button_url("https://music.apple.com/de/search?term=KYANU Fcuk up the Club")
        == "https://music.apple.com/de/search?term=KYANU%20Fcuk%20up%20the%20Club"
    )


def test_an_encoded_link_is_left_as_it_is():
    link = "https://music.apple.com/de/album/fremdk%C3%B6rper/1299108826?i=1299109312&uo=4"
    assert button_url(link) == link


def test_letters_beyond_ascii_are_encoded():
    assert button_url("https://music.apple.com/de/search?term=Moréau") == (
        "https://music.apple.com/de/search?term=Mor%C3%A9au"
    )


@pytest.mark.parametrize(
    "link",
    ["", "http://music.apple.com/x", "music.apple.com/x", "https://", "https://x/" + "a" * 600],
)
def test_no_button_rather_than_one_discord_refuses(link):
    assert button_url(link) == ""
