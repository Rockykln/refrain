"""MPRIS skip-control falls back across players.

Apple Music in Chromium typically exposes two MPRIS players:
- KDE's `plasma-browser-integration` — rich metadata, but
  CanGoNext / CanGoPrevious = False
- the browser's own MPRIS — CanGoNext = True but only the tab title
  as `xesam:title` (no `xesam:url`)

The metadata view picks the plasma player; skip controls have to fall
back onto the browser-native player to actually do anything.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

# dbus is a required runtime dep but these tests target the *dispatch*
# logic against an in-memory player set. Forcefully replace `dbus`
# with our stub *and* reload refrain.sources.mpris so its module-level
# `import dbus` picks up the fake — `setdefault` doesn't work here
# because earlier tests in the suite may have already pulled in the
# real `dbus` module.
_fake_dbus = MagicMock()


class _FakeDBusException(Exception):
    pass


_fake_dbus.DBusException = _FakeDBusException
_real_dbus = {name: sys.modules.get(name) for name in ("dbus", "dbus.mainloop")}
sys.modules["dbus"] = _fake_dbus
sys.modules["dbus.mainloop"] = _fake_dbus.mainloop
import refrain.sources.mpris as _mpris_mod  # noqa: E402

_mpris_mod = importlib.reload(_mpris_mod)
MPRISSource = _mpris_mod.MPRISSource
# The reloaded module keeps the stub; every later import gets real dbus
# again (dbus.service, which the MPRIS server needs, can't come from a stub).
for _name, _module in _real_dbus.items():
    if _module is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _module


@pytest.mark.parametrize(
    "url",
    [
        "itunes://music.apple.com/de/album/test",
        "itmss://music.apple.com/de/album/test",
        "itms://music.apple.com/de/album/test",
        "music://music.apple.com/de/album/test",
    ],
)
def test_normalize_apple_url_converts_deep_links(url):
    assert _mpris_mod._normalize_apple_url(url) == "https://music.apple.com/de/album/test"


def test_normalize_apple_url_is_case_insensitive():
    assert (
        _mpris_mod._normalize_apple_url("MUSIC://music.apple.com/de/album/test")
        == "https://music.apple.com/de/album/test"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://music.apple.com/de/album/test",
        "http://example.com/test",
        "not-a-url",
    ],
)
def test_normalize_apple_url_keeps_other_urls_unchanged(url):
    assert _mpris_mod._normalize_apple_url(url) == url


def test_normalize_apple_url_handles_empty_url():
    assert _mpris_mod._normalize_apple_url("") == ""
    assert _mpris_mod._normalize_apple_url(None) == ""


class _FakePlayer:
    """In-memory MPRIS player surface — exposes Can* + records method calls."""

    def __init__(
        self,
        *,
        can_pause: bool = True,
        can_go_next: bool = False,
        can_go_previous: bool = False,
        raise_on_method: str | None = None,
    ):
        self.can_pause = can_pause
        self.can_go_next = can_go_next
        self.can_go_previous = can_go_previous
        self.raise_on_method = raise_on_method
        self.calls: list[str] = []

    def get_prop(self, prop: str) -> bool:
        return {
            "CanPause": self.can_pause,
            "CanGoNext": self.can_go_next,
            "CanGoPrevious": self.can_go_previous,
        }[prop]

    def call(self, method: str) -> None:
        if self.raise_on_method == method:
            raise _FakeDBusException(f"player rejected {method}")
        self.calls.append(method)


def _wire_bus(players: dict[str, _FakePlayer]):
    """Replace MPRISSource's bus access so it routes to our fake players."""

    def fake_session_bus(*_args, **_kwargs):  # the source asks for a private connection
        bus = MagicMock()

        def get_object(name, _path, **_kw):
            obj = MagicMock()
            obj._name = name
            return obj

        bus.get_object.side_effect = get_object
        return bus

    def fake_interface(obj, iface_name):
        name = obj._name
        player = players[name]
        proxy = MagicMock()
        if iface_name == "org.freedesktop.DBus.Properties":
            proxy.Get.side_effect = lambda _ifc, prop, **_kw: player.get_prop(prop)
        elif iface_name == "org.mpris.MediaPlayer2.Player":
            proxy.PlayPause.side_effect = lambda **_kw: player.call("PlayPause")
            proxy.Next.side_effect = lambda **_kw: player.call("Next")
            proxy.Previous.side_effect = lambda **_kw: player.call("Previous")
        return proxy

    _fake_dbus.SessionBus.side_effect = fake_session_bus
    _fake_dbus.Interface.side_effect = fake_interface


def test_next_falls_back_to_capable_player():
    """The metadata player can't skip — fallback player must take the call."""
    plasma = _FakePlayer(can_go_next=False)
    chromium = _FakePlayer(can_go_next=True)
    _wire_bus(
        {
            "org.mpris.MediaPlayer2.plasma-browser-integration": plasma,
            "org.mpris.MediaPlayer2.chromium.instance123": chromium,
        }
    )

    src = MPRISSource()
    src._last_player_name = "org.mpris.MediaPlayer2.plasma-browser-integration"
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    assert src.next() is True
    assert plasma.calls == [], "plasma had CanGoNext=False, should be skipped"
    assert chromium.calls == ["Next"], "fallback chromium player should have got Next"


def test_previous_uses_capable_fallback_too():
    plasma = _FakePlayer(can_go_previous=False)
    chromium = _FakePlayer(can_go_previous=True)
    _wire_bus(
        {
            "org.mpris.MediaPlayer2.plasma-browser-integration": plasma,
            "org.mpris.MediaPlayer2.chromium.instance123": chromium,
        }
    )

    src = MPRISSource()
    src._last_player_name = "org.mpris.MediaPlayer2.plasma-browser-integration"
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    assert src.previous() is True
    assert chromium.calls == ["Previous"]


def test_play_pause_prefers_primary():
    """When the primary CAN handle it, the fallback must NOT be touched."""
    plasma = _FakePlayer(can_pause=True)
    chromium = _FakePlayer(can_pause=True)
    _wire_bus(
        {
            "org.mpris.MediaPlayer2.plasma-browser-integration": plasma,
            "org.mpris.MediaPlayer2.chromium.instance123": chromium,
        }
    )

    src = MPRISSource()
    src._last_player_name = "org.mpris.MediaPlayer2.plasma-browser-integration"
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    assert src.play_pause() is True
    assert plasma.calls == ["PlayPause"]
    assert chromium.calls == []


def test_play_pause_goes_to_the_apple_music_tab_itself_when_known():
    """plasma's toggle follows the page's artwork video, which plays on
    through a pause — it could pause the music but never start it again."""
    plasma = _FakePlayer(can_pause=True)
    chromium = _FakePlayer(can_pause=True)
    _wire_bus(
        {
            "org.mpris.MediaPlayer2.plasma-browser-integration": plasma,
            "org.mpris.MediaPlayer2.chromium.instance123": chromium,
        }
    )

    src = MPRISSource()
    src._last_player_name = "org.mpris.MediaPlayer2.plasma-browser-integration"
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]
    src._native_apple_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    assert src.play_pause() is True
    assert chromium.calls == ["PlayPause"]
    assert plasma.calls == [], "one click, one toggle"


# ------------------------------------------------ reading: whose state counts

PLASMA = "org.mpris.MediaPlayer2.plasma-browser-integration"
CHROMIUM = "org.mpris.MediaPlayer2.chromium.instance10001"


def _wire_read(players: dict[str, dict]):
    """A bus for `read()`: ListNames plus each player's properties."""

    def fake_session_bus(*_args, **_kwargs):
        bus = MagicMock()

        def get_object(name, _path, **_kw):
            obj = MagicMock()
            obj._name = name
            return obj

        bus.get_object.side_effect = get_object
        return bus

    def fake_interface(obj, iface_name):
        proxy = MagicMock()
        if iface_name == "org.freedesktop.DBus":
            proxy.ListNames.return_value = list(players)
        elif iface_name == "org.freedesktop.DBus.Properties":
            props = players[obj._name]
            proxy.Get.side_effect = lambda _ifc, prop, **_kw: props[prop]
        return proxy

    _fake_dbus.SessionBus.side_effect = fake_session_bus
    _fake_dbus.Interface.side_effect = fake_interface


def _plasma(status):
    return {
        "Identity": "Chromium",
        "DesktopEntry": "chromium",
        "PlaybackStatus": status,
        "Position": 2_000_000,
        "Metadata": {
            "xesam:title": "Eifersucht",
            "xesam:artist": ["Rammstein"],
            "xesam:album": "Sehnsucht",
            "xesam:url": "https://music.apple.com/de/album/sehnsucht/1390562159",
            "mpris:length": 10_416_000,  # the artwork video, not the song
        },
    }


# As the page spells it: left-to-right mark, no-break spaces, en dashes.
def _tab(status, title="‎Sehnsucht\xa0– Album von Rammstein\xa0– Apple\xa0Music"):  # noqa: RUF001
    return {
        "Identity": "Chromium",
        "DesktopEntry": "",
        "PlaybackStatus": status,
        "Position": 63_000_000,
        "Metadata": {"xesam:title": title, "mpris:length": 215_914_000},
    }


def test_a_pause_is_read_from_the_apple_music_tab_not_plasma():
    """Measured: paused in the browser, plasma kept saying Playing."""
    _wire_read({PLASMA: _plasma("Playing"), CHROMIUM: _tab("Paused")})
    src = MPRISSource()
    track = src.read()
    assert (track.title, track.status) == ("Eifersucht", _mpris_mod.PlaybackStatus.PAUSED)
    assert src._native_apple_names == [CHROMIUM]


def test_playing_is_read_from_the_apple_music_tab_too():
    # The artwork video can stop (a background tab) while the music plays.
    _wire_read({PLASMA: _plasma("Paused"), CHROMIUM: _tab("Playing")})
    assert MPRISSource().read().status == _mpris_mod.PlaybackStatus.PLAYING


def test_another_tab_playing_says_nothing_about_apple_music():
    _wire_read({PLASMA: _plasma("Playing"), CHROMIUM: _tab("Paused", title="Some Video - YouTube")})
    src = MPRISSource()
    assert src.read().status == _mpris_mod.PlaybackStatus.PLAYING
    assert src._native_apple_names == []


def test_the_page_title_is_not_a_song():
    """Measured: with no song loaded, plasma reported the page title as the
    track — "Apple Music – Webplayer", no artist — and it "played"."""
    page = _plasma("Playing")
    page["Metadata"] = {
        "xesam:title": "‎Apple Music\xa0– Webplayer",  # noqa: RUF001
        "xesam:url": "https://music.apple.com/de/home",
    }
    _wire_read({PLASMA: page})
    assert MPRISSource().read().has_track is False


def test_returns_false_when_no_player_can():
    """Both players reject the action — caller learns nothing was done."""
    plasma = _FakePlayer(can_go_next=False)
    chromium = _FakePlayer(can_go_next=False)
    _wire_bus(
        {
            "org.mpris.MediaPlayer2.plasma-browser-integration": plasma,
            "org.mpris.MediaPlayer2.chromium.instance123": chromium,
        }
    )

    src = MPRISSource()
    src._last_player_name = "org.mpris.MediaPlayer2.plasma-browser-integration"
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    # Last-resort attempt against primary still fires (some players lie about
    # Can*) — but since the fake refuses too, the result is False.
    plasma.raise_on_method = "Next"
    chromium.raise_on_method = "Next"

    assert src.next() is False


def test_no_primary_yet_uses_fallbacks_only():
    """`read()` hasn't run, so primary is unset. Fallback alone wins."""
    chromium = _FakePlayer(can_go_next=True)
    _wire_bus({"org.mpris.MediaPlayer2.chromium.instance123": chromium})

    src = MPRISSource()
    src._last_player_name = None
    src._control_fallback_names = ["org.mpris.MediaPlayer2.chromium.instance123"]

    assert src.next() is True
    assert chromium.calls == ["Next"]


def test_only_apple_musics_own_page_titles_count_as_its_tab():
    from refrain.sources.mpris import _is_apple_music_page_title

    assert _is_apple_music_page_title(
        "\u200eSehnsucht\xa0\u2013 Album von Rammstein\xa0\u2013 Apple\xa0Music"
    )
    assert _is_apple_music_page_title("\u200eApple\xa0Music\xa0\u2013 Webplayer")
    assert _is_apple_music_page_title("Listen Now - Apple Music")
    assert not _is_apple_music_page_title("Apple Music review: worth it in 2026? - YouTube")
    assert not _is_apple_music_page_title("Why I left Apple Music")
