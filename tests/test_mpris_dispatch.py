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
sys.modules["dbus"] = _fake_dbus
import refrain.sources.mpris as _mpris_mod  # noqa: E402

_mpris_mod = importlib.reload(_mpris_mod)
MPRISSource = _mpris_mod.MPRISSource


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

    def fake_session_bus():
        bus = MagicMock()

        def get_object(name, _path):
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
            proxy.Get.side_effect = lambda _ifc, prop: player.get_prop(prop)
        elif iface_name == "org.mpris.MediaPlayer2.Player":
            proxy.PlayPause.side_effect = lambda: player.call("PlayPause")
            proxy.Next.side_effect = lambda: player.call("Next")
            proxy.Previous.side_effect = lambda: player.call("Previous")
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
