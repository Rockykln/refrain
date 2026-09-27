"""Which image goes in which slot of the Discord card, and where each one leads."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain.daemon import _REFRAIN_ICON_URL, _REFRAIN_URL, DaemonWorker  # noqa: E402
from tests.daemon_fakes import CLIENT_ID, FakeRPC, Player, install, make_config  # noqa: E402

ARTIST, TITLE = "Mara Keel", "Paper Satellites"
COVER = "https://is1-ssl.mzstatic.example/image/paper-lanterns.jpg"
SONG = "https://music.apple.example/de/album/paper-satellites/1?i=2"
ICON_HASH = "8565b160aaaaaaaabbbbbbbbccccccc0"
APP_ICON = f"https://cdn.discordapp.com/app-icons/{CLIENT_ID}/{ICON_HASH}.png"

KNOWN_APPLICATION = {
    "app_icon": ICON_HASH,
    "app_icon_for_id": CLIENT_ID,
    "app_name": "Apple Music",
    "app_name_for_id": CLIENT_ID,
}


@pytest.fixture
def card(monkeypatch):
    """One poll of a playing song; returns the payload Discord was handed."""

    def build(*, cover=True, song_url=True, **sections):
        clock, _ = install(monkeypatch)
        worker = DaemonWorker(make_config(**sections))
        if cover:
            worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
        if song_url:
            worker._cover_fetcher.song_urls[(ARTIST, TITLE)] = SONG
        player = Player(worker, clock)
        player.play()
        # Without a cover the first three polls are held back for one.
        player.tick(n=1 if cover else 4)
        return FakeRPC.instances[-1].last[1]

    return build


def test_the_application_icon_sits_in_the_corner_next_to_the_cover(card):
    payload = card(discord=KNOWN_APPLICATION)
    assert payload["large_image"] == COVER
    assert payload["large_url"] == SONG
    assert payload["small_image"] == APP_ICON
    assert payload["small_text"] == "Apple Music"
    # The application icon leads nowhere: it names the source, it is not a link.
    assert "small_url" not in payload


def test_without_a_cover_the_application_icon_moves_up(card):
    payload = card(cover=False, discord=KNOWN_APPLICATION)
    assert payload["large_image"] == APP_ICON
    assert "large_url" not in payload
    assert payload["small_image"] == _REFRAIN_ICON_URL
    assert payload["small_url"] == _REFRAIN_URL


def test_an_unknown_application_icon_leaves_the_corner_to_refrain(card):
    payload = card()
    assert payload["large_image"] == COVER
    assert payload["small_image"] == _REFRAIN_ICON_URL
    assert payload["small_text"] == "Refrain"
    assert payload["small_url"] == _REFRAIN_URL


def test_with_neither_cover_nor_application_icon_refrain_is_the_only_image(card):
    payload = card(cover=False)
    assert payload["large_image"] == _REFRAIN_ICON_URL
    assert payload["large_url"] == _REFRAIN_URL
    # The same icon twice would read as a rendering fault, not as a design.
    assert "small_image" not in payload


def test_the_switch_takes_the_small_icon_off_the_card(card):
    payload = card(discord=KNOWN_APPLICATION, behavior={"show_small_image": False})
    assert payload["large_image"] == COVER
    assert not [key for key in payload if key.startswith("small_")]


def test_an_icon_cached_for_another_application_is_not_used(card):
    payload = card(discord={**KNOWN_APPLICATION, "app_icon_for_id": "123450000000000009"})
    assert payload["small_image"] == _REFRAIN_ICON_URL


def test_the_song_link_lands_on_both_text_lines_and_the_cover(card):
    payload = card()
    assert payload["details_url"] == SONG
    assert payload["state_url"] == SONG
    assert payload["large_url"] == SONG


def test_a_song_without_a_link_carries_none(card):
    payload = card(song_url=False)
    assert "details_url" not in payload
    assert "state_url" not in payload
    assert "large_url" not in payload


def test_the_links_are_set_even_with_the_button_switched_off(card):
    payload = card(behavior={"show_buttons": False})
    assert "buttons" not in payload
    assert payload["details_url"] == SONG
