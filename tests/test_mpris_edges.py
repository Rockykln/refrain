"""MPRISSource edge cases: name-listing cache purges and single-property read failures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import refrain.sources.mpris as mpris

CHROMIUM = "org.mpris.MediaPlayer2.chromium.instance10001"
FIREFOX = "org.mpris.MediaPlayer2.firefox.instance_1_42"


class _FakeDBusException(Exception):
    pass


@pytest.fixture
def fake_dbus(monkeypatch):
    """A minimal stand-in for the ``dbus`` module used inside refrain.sources.mpris."""
    monkeypatch.setattr(
        mpris, "dbus", SimpleNamespace(DBusException=_FakeDBusException, Interface=None)
    )
    return _FakeDBusException


def test_a_player_that_left_the_bus_is_dropped_from_the_identity_cache(fake_dbus, monkeypatch):
    src = mpris.MPRISSource()
    src._proxies[CHROMIUM] = object()
    src._identities[CHROMIUM] = ("Chromium", "chromium")
    src._no_get_all.add((CHROMIUM, "iface"))
    src._timeout_blacklist[CHROMIUM] = 1e9

    class _ListNamesProxy:
        def __init__(self, _obj, _iface):
            pass

        def ListNames(self):
            return [FIREFOX]

    monkeypatch.setattr(mpris.dbus, "Interface", _ListNamesProxy)
    bus = SimpleNamespace(get_object=lambda *a, **k: SimpleNamespace())

    names = src._list_players(bus)

    assert names == [FIREFOX]
    assert CHROMIUM not in src._proxies
    assert CHROMIUM not in src._identities
    # A tab that timed out once used to stay on the list for the whole run.
    assert CHROMIUM not in src._timeout_blacklist


def test_an_unexpected_getall_error_still_falls_back_to_reading_properties_one_by_one(fake_dbus):
    """Not every failure is a ``dbus.DBusException`` — a plain bug in the batch call
    must not prevent Refrain from reading the player one property at a time."""
    src = mpris.MPRISSource()
    values = {"Identity": "Chromium", "DesktopEntry": "chromium"}

    def _get_all(_iface, timeout=None):
        raise RuntimeError("boom")

    props = SimpleNamespace(GetAll=_get_all, Get=lambda _iface, key, timeout=None: values[key])

    read, complete = src._read_props(
        props, CHROMIUM, "org.mpris.MediaPlayer2", ("Identity", "DesktopEntry")
    )

    assert complete is True
    assert read == values


def test_a_property_that_hangs_blacklists_the_player_and_marks_the_read_incomplete(fake_dbus):
    src = mpris.MPRISSource()

    def _get(_iface, key, timeout=None):
        if key == "Identity":
            raise fake_dbus("org.freedesktop.DBus.Error.NoReply")
        return "chromium"

    props = SimpleNamespace(
        GetAll=lambda _iface, timeout=None: (_ for _ in ()).throw(
            fake_dbus("org.freedesktop.DBus.Error.Failed")
        ),
        Get=_get,
    )

    values, complete = src._read_props(
        props, CHROMIUM, "org.mpris.MediaPlayer2", ("Identity", "DesktopEntry")
    )

    assert complete is False
    assert values == {"DesktopEntry": "chromium"}
    assert CHROMIUM in src._timeout_blacklist


def test_a_property_whose_player_vanished_forgets_it_and_marks_the_read_incomplete(fake_dbus):
    src = mpris.MPRISSource()
    src._proxies[CHROMIUM] = object()
    src._identities[CHROMIUM] = ("Chromium", "chromium")

    def _get(_iface, key, timeout=None):
        if key == "Identity":
            raise fake_dbus("org.freedesktop.DBus.Error.ServiceUnknown: gone")
        return "chromium"

    props = SimpleNamespace(
        GetAll=lambda _iface, timeout=None: (_ for _ in ()).throw(
            fake_dbus("org.freedesktop.DBus.Error.Failed")
        ),
        Get=_get,
    )

    values, complete = src._read_props(
        props, CHROMIUM, "org.mpris.MediaPlayer2", ("Identity", "DesktopEntry")
    )

    assert complete is False
    assert CHROMIUM not in src._proxies
    assert CHROMIUM not in src._identities
