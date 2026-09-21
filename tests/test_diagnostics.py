"""The system report tells a maintainer what runs, and nothing about the user."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from refrain.config import Config
from refrain.diagnostics import report


@pytest.fixture
def filled(xdg_tmp):
    config = Config()
    config.discord.client_id = "1234567890123456789"
    config.discord.client_id_bluetooth = "1234567890123456780"
    config.sources.bluetooth_device = "12:34:56:78:9A:BC"
    config.lastfm.session_key = "12345session"
    config.lastfm.username = "refrain_demo"
    config.lastfm.api_key = "12345apikey"
    config.lastfm.shared_secret = "12345secret"
    return config


def test_the_report_says_what_refrain_runs_on(filled):
    text = report(filled)
    for line in ("Refrain ", "OS:", "Python:", "PySide6:", "Desktop:", "Sources:", "Privacy:"):
        assert line in text, f"{line!r} missing"
    assert "crash reports:" in text


def test_the_report_gives_nothing_away(filled):
    text = report(filled)
    for secret in (
        "1234567890123456789",  # the Discord Application ID
        "1234567890123456780",
        "12:34:56:78:9A:BC",  # the Bluetooth address
        "12345session",
        "12345apikey",
        "12345secret",
        "refrain_demo",  # the Last.fm account
    ):
        assert secret not in text, f"{secret!r} leaked into the report"
    # It still says whether each of them is set.
    assert "Application ID set yes" in text
    assert "device chosen: yes" in text
    assert "connected yes" in text


def test_the_report_keeps_home_and_the_time_zone_out_of_it(filled):
    from pathlib import Path

    filled.advanced.time_zone = "Europe/Berlin"
    filled.advanced.time_format = "12h"
    text = report(filled)
    assert str(Path.home()) not in text, "the home directory carries the user's name"
    assert "Europe/Berlin" not in text, "the time zone says where someone lives"
    assert "config.toml" in text
    assert "clock: 12h" in text


def test_a_path_under_home_is_written_with_a_tilde():
    from pathlib import Path

    from refrain.diagnostics import _short

    assert _short(Path.home() / ".config/refrain/config.toml") == "~/.config/refrain/config.toml"
    assert _short(Path("/etc/refrain.toml")) == "/etc/refrain.toml"


def test_an_empty_setup_reads_as_empty(xdg_tmp):
    text = report(Config())
    assert "Application ID set no" in text
    assert "connected no" in text


def test_a_machine_without_os_release_still_reports_something(monkeypatch):
    from pathlib import Path

    from refrain import diagnostics

    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("no")))
    assert diagnostics._os_name()


def test_versions_and_install_type_survive_a_missing_piece(monkeypatch):
    from refrain import diagnostics

    monkeypatch.setitem(sys.modules, "PySide6", None)
    assert diagnostics._qt_versions() == ("—", "—")
    monkeypatch.setattr(
        "refrain.updater.detect_install_type",
        lambda: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    assert diagnostics._install_type() == "unknown"


# ---------------------------------------------------------------- MPRIS players


class _FakeBus:
    """Players are dicts: {"identity": str, "metadata": {"xesam:url": str}}."""

    def __init__(self, players, list_error=None, missing=()):
        self.players = players
        self.list_error = list_error
        self.missing = set(missing)
        self.closed = False

    def get_object(self, name, _path, **_kw):
        return SimpleNamespace(bus=self, name=name)

    def close(self):
        self.closed = True


class _FakeRoot:
    """org.freedesktop.DBus — only ListNames is used."""

    def __init__(self, obj, _iface):
        self._bus = obj.bus

    def ListNames(self):
        if self._bus.list_error:
            raise self._bus.list_error
        return list(self._bus.players)


class _FakeProps:
    """org.freedesktop.DBus.Properties on one player object."""

    def __init__(self, obj, _iface):
        self._bus = obj.bus
        self._name = obj.name

    def Get(self, iface, prop, timeout=None):
        if self._name in self._bus.missing:
            raise RuntimeError(f"{self._name} is gone")
        entry = self._bus.players[self._name]
        if iface == "org.mpris.MediaPlayer2" and prop == "Identity":
            return entry.get("identity", "")
        if iface == "org.mpris.MediaPlayer2.Player" and prop == "Metadata":
            return entry.get("metadata", {})
        raise AssertionError(f"unexpected Get({iface}, {prop})")


def _fake_interface(obj, iface):
    return _FakeRoot(obj, iface) if iface == "org.freedesktop.DBus" else _FakeProps(obj, iface)


def _install_fake_bus(monkeypatch, players, list_error=None, missing=(), session_bus_error=None):
    bus = _FakeBus(players, list_error=list_error, missing=missing)

    def session_bus(**_kw):
        if session_bus_error:
            raise session_bus_error
        return bus

    fake_dbus = types.SimpleNamespace(
        SessionBus=session_bus,
        Interface=_fake_interface,
        mainloop=types.SimpleNamespace(NULL_MAIN_LOOP=object()),
    )
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)
    monkeypatch.setitem(sys.modules, "dbus.mainloop", fake_dbus.mainloop)
    return bus


VIVALDI = "org.mpris.MediaPlayer2.vivaldi.instance12345"
APPLE_URL = "https://music.apple.com/de/album/glass-tides/12345"


def test_the_report_lists_bus_name_identity_and_apple_music_yes(monkeypatch):
    _install_fake_bus(
        monkeypatch,
        {VIVALDI: {"identity": "Vivaldi", "metadata": {"xesam:url": APPLE_URL}}},
    )
    text = report(Config())
    assert f"{VIVALDI} — Vivaldi" in text
    assert "xesam:url: yes" in text
    assert "Apple Music: yes" in text
    assert APPLE_URL not in text, "the URL itself must never leak into the report"


def test_a_non_apple_url_counts_as_a_url_but_not_apple_music(monkeypatch):
    _install_fake_bus(
        monkeypatch,
        {VIVALDI: {"identity": "Vivaldi", "metadata": {"xesam:url": "https://example.com/watch"}}},
    )
    text = report(Config())
    assert "xesam:url: yes" in text
    assert "Apple Music: no" in text


def test_a_player_without_a_url_is_marked_no_no(monkeypatch):
    _install_fake_bus(monkeypatch, {VIVALDI: {"identity": "Vivaldi", "metadata": {}}})
    text = report(Config())
    assert "xesam:url: no" in text
    assert "Apple Music: no" in text


def test_refrains_own_mpris_server_is_not_listed(monkeypatch):
    _install_fake_bus(
        monkeypatch,
        {
            "org.mpris.MediaPlayer2.refrain": {"identity": "Refrain"},
            VIVALDI: {"identity": "Vivaldi", "metadata": {}},
        },
    )
    text = report(Config())
    assert "org.mpris.MediaPlayer2.refrain" not in text
    assert VIVALDI in text


def test_no_players_found_says_so(monkeypatch):
    _install_fake_bus(monkeypatch, {})
    text = report(Config())
    assert "MPRIS players: none found" in text


def test_a_broken_session_bus_yields_one_line_and_does_not_break_the_report(monkeypatch):
    _install_fake_bus(monkeypatch, {}, session_bus_error=RuntimeError("no session bus"))
    text = report(Config())
    assert "MPRIS: could not read the session bus" in text
    for line in ("Refrain ", "OS:", "Discord:"):
        assert line in text


def test_a_failed_listnames_yields_one_line_and_does_not_break_the_report(monkeypatch):
    _install_fake_bus(monkeypatch, {}, list_error=RuntimeError("timeout"))
    text = report(Config())
    assert "MPRIS: could not list players" in text
    assert "Discord:" in text


def test_a_player_that_vanishes_mid_read_still_reports_a_line(monkeypatch):
    _install_fake_bus(
        monkeypatch,
        {VIVALDI: {"identity": "Vivaldi", "metadata": {"xesam:url": APPLE_URL}}},
        missing=(VIVALDI,),
    )
    text = report(Config())
    assert f"{VIVALDI} — —" in text
    assert "xesam:url: no" in text
