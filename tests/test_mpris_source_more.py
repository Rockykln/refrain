"""MPRISSource against a fake session bus: player choice, property quirks and control errors."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import refrain.sources.mpris as mpris
from refrain.sources.base import PlaybackStatus

PLASMA = "org.mpris.MediaPlayer2.plasma-browser-integration"
CHROMIUM = "org.mpris.MediaPlayer2.chromium.instance10001"
FIREFOX = "org.mpris.MediaPlayer2.firefox.instance_1_42"
ALBUM_URL = "https://music.apple.com/de/album/glass-tides/12345"
ROOT_IFACE = "org.mpris.MediaPlayer2"
ROOT_PROPS = ("Identity", "DesktopEntry")


class FakeDBusException(Exception):
    pass


class FakeBus:
    """Players are dicts of MPRIS properties; an Exception value is raised on Get."""

    def __init__(self, players, *, list_error=None, missing=()):
        self.players = players
        self.list_error = list_error
        self.missing = set(missing)
        self.gets: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.close_error = None
        self.listings = 0
        # Which process owns a name; get_object binds a proxy to the owner of that moment.
        self.owners: dict[str, int] = {}
        self.resolved: list[str] = []

    def get_object(self, name, _path, **_kw):
        if name in self.missing:
            raise FakeDBusException(f"org.freedesktop.DBus.Error.ServiceUnknown: {name}")
        self.resolved.append(name)
        return SimpleNamespace(bus=self, name=name, owner=self.owners.get(name, 1))

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class _Proxy:
    def __init__(self, obj, iface):
        self.bus = obj.bus
        self.name = obj.name
        self.owner = obj.owner
        self.iface = iface

    def ListNames(self):
        self.bus.listings += 1
        if self.bus.list_error:
            raise self.bus.list_error
        return list(self.bus.players)

    def _props(self):
        if (
            self.name in self.bus.missing
            or self.name not in self.bus.players
            or self.owner != self.bus.owners.get(self.name, 1)
        ):
            raise FakeDBusException(f"org.freedesktop.DBus.Error.ServiceUnknown: {self.name}")
        return self.bus.players[self.name]

    def Get(self, _iface, prop, timeout=None):
        self.bus.gets.append((self.name, prop))
        props = self._props()
        if prop not in props:
            raise FakeDBusException("org.freedesktop.DBus.Error.UnknownProperty")
        value = props[prop]
        if isinstance(value, Exception):
            raise value
        return value

    def GetAll(self, iface, timeout=None):
        """Like a real player: one bad property can fail the whole call."""
        self.bus.gets.append((self.name, f"GetAll {iface}"))
        props = self._props()
        if isinstance(props.get("GetAll"), Exception):
            raise props["GetAll"]
        wanted = {k: v for k, v in props.items() if (k in ROOT_PROPS) == (iface == ROOT_IFACE)}
        errors = [v for v in wanted.values() if isinstance(v, Exception)]
        for error in errors:
            if "NoReply" in str(error):
                raise error
        if errors:
            raise FakeDBusException("org.freedesktop.DBus.Error.Failed")
        return wanted

    def __getattr__(self, method):
        def call(timeout=None):
            failure = self.bus.players[self.name].get(f"fail:{method}")
            if failure:
                raise failure
            self.bus.calls.append((self.name, method))

        return call


@pytest.fixture
def bus(monkeypatch):
    """Installs a fake `dbus` module into the source; returns a setter for the players."""
    state = {"bus": None, "opened": [], "error": None}

    def session_bus(**kw):
        if state["error"]:
            raise state["error"]
        state["opened"].append(kw)
        return state["bus"]

    fake = SimpleNamespace(
        DBusException=FakeDBusException,
        SessionBus=session_bus,
        Interface=_Proxy,
        mainloop=SimpleNamespace(NULL_MAIN_LOOP="null-main-loop"),
    )
    monkeypatch.setattr(mpris, "dbus", fake)

    def install(players, **kw):
        state["bus"] = FakeBus(players, **kw)
        return state["bus"]

    install.state = state
    return install


def _player(
    status="Playing",
    *,
    identity="Chromium",
    title="Glass Tides",
    artist=("Neon Harbor",),
    album="Low Light",
    url=ALBUM_URL,
    length=215_914_000,
    position=63_000_000,
):
    meta = {"xesam:title": title, "xesam:artist": list(artist), "xesam:album": album}
    if url is not None:
        meta["xesam:url"] = url
    if length is not None:
        meta["mpris:length"] = length
    return {
        "Identity": identity,
        "DesktopEntry": identity.lower(),
        "PlaybackStatus": status,
        "Position": position,
        "Metadata": meta,
        "CanPlay": True,
        "CanPause": True,
        "CanGoNext": True,
        "CanGoPrevious": True,
    }


def test_the_private_connection_is_opened_once_and_reused(bus):
    bus({CHROMIUM: _player()})
    src = mpris.MPRISSource()
    src.read()
    src.read()
    assert bus.state["opened"] == [{"private": True, "mainloop": "null-main-loop"}]


def test_a_failing_name_listing_drops_the_connection(bus):
    first = bus({CHROMIUM: _player()}, list_error=FakeDBusException("Disconnected"))
    src = mpris.MPRISSource()
    assert src.read().has_track is False
    assert first.closed is True

    bus({CHROMIUM: _player()})
    assert src.read().title == "Glass Tides"
    assert len(bus.state["opened"]) == 2


def test_an_error_while_closing_a_dead_connection_is_ignored(bus):
    dead = bus({}, list_error=FakeDBusException("Disconnected"))
    dead.close_error = OSError("already gone")
    src = mpris.MPRISSource()
    assert src.read().has_track is False
    assert src._bus is None


def test_foreign_names_and_refrains_own_player_are_not_read(bus):
    fake = bus(
        {
            ":1.42": {},
            "org.freedesktop.Notifications": {},
            "org.mpris.MediaPlayer2.refrain": _player(),
        }
    )
    assert mpris.MPRISSource().read().has_track is False
    assert fake.gets == []


def test_a_player_that_is_not_a_browser_is_ignored(bus):
    bus({"org.mpris.MediaPlayer2.mpv": _player(identity="mpv")})
    src = mpris.MPRISSource()
    assert src.read().has_track is False
    assert src._control_fallback_names == []


def test_custom_browser_hints_decide_what_counts_as_a_browser(bus):
    bus({"org.mpris.MediaPlayer2.qutebrowser": _player(identity="qutebrowser")})
    src = mpris.MPRISSource(browser_hints=["qutebrowser"])
    assert src.read().title == "Glass Tides"

    src.set_browser_hints([])
    assert src._browser_hints == ["qutebrowser"]
    src.set_browser_hints(["firefox"])
    assert src.read().has_track is False


def test_a_deep_link_url_counts_as_apple_music(bus):
    bus({FIREFOX: _player(identity="Firefox", url="music://music.apple.com/de/album/12345")})
    track = mpris.MPRISSource().read()
    assert track.url == "https://music.apple.com/de/album/12345"
    assert track.player == "Firefox"


def test_position_and_length_are_read_in_milliseconds(bus):
    bus({CHROMIUM: _player()})
    track = mpris.MPRISSource().read()
    assert (track.position_ms, track.duration_ms) == (63_000, 215_914)
    assert track.source == "mpris"


def test_unreadable_position_and_length_become_zero(bus):
    bus({CHROMIUM: _player(length="unknown", position="unknown")})
    track = mpris.MPRISSource().read()
    assert (track.position_ms, track.duration_ms) == (0, 0)
    assert track.title == "Glass Tides"


def test_a_property_error_other_than_a_timeout_only_defaults_that_property(bus):
    player = _player(identity="Chromium")
    player["DesktopEntry"] = FakeDBusException("org.freedesktop.DBus.Error.Failed")
    player["Position"] = RuntimeError("bad reply")
    bus({CHROMIUM: player})
    src = mpris.MPRISSource()
    track = src.read()
    assert (track.title, track.player, track.position_ms) == ("Glass Tides", "Chromium", 0)
    assert src._timeout_blacklist == {}


def test_a_timed_out_player_is_skipped_until_its_cooldown_ends(bus):
    player = _player()
    player["Identity"] = FakeDBusException("org.freedesktop.DBus.Error.NoReply")
    fake = bus({CHROMIUM: player})
    src = mpris.MPRISSource()
    src.read()
    assert CHROMIUM in src._timeout_blacklist

    reads = len(fake.gets)
    assert src.read().has_track is False
    assert len(fake.gets) == reads

    src._timeout_blacklist[CHROMIUM] = 0.0
    player["Identity"] = "Chromium"
    assert src.read().title == "Glass Tides"


def test_a_player_that_vanished_does_not_hide_the_others(bus):
    bus({FIREFOX: _player(), CHROMIUM: _player(title="Ferrous")}, missing={FIREFOX})
    assert mpris.MPRISSource().read().title == "Ferrous"


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(mpris, "time", SimpleNamespace(monotonic=lambda: now.t))
    return now


def test_a_steady_poll_asks_each_browser_once_and_lists_names_rarely(bus, clock):
    fake = bus(
        {
            PLASMA: _plasma("Playing"),
            CHROMIUM: _tab("Playing"),
            "org.mpris.MediaPlayer2.playerctld": _player(identity="Refrain", url=None),
        }
    )
    src = mpris.MPRISSource()
    src.read()
    fake.gets.clear()
    fake.listings = 0
    for _ in range(4):
        clock.t += 0.25
        assert src.read().title == "Overexposed"
    assert fake.listings == 0
    assert sorted(fake.gets) == sorted(
        [(PLASMA, "GetAll org.mpris.MediaPlayer2.Player")] * 4
        + [(CHROMIUM, "GetAll org.mpris.MediaPlayer2.Player")] * 4
    )

    clock.t += src._LIST_REFRESH_S
    src.read()
    assert fake.listings == 1


def test_each_player_is_looked_up_once_and_other_apps_are_not_asked_again(bus, clock):
    fake = bus(
        {
            CHROMIUM: _player(),
            "org.mpris.MediaPlayer2.mpv": _player(identity="mpv"),
        }
    )
    src = mpris.MPRISSource()
    for _ in range(3):
        src.read()
        clock.t += 0.25
    players = [n for n in fake.resolved if n != "org.freedesktop.DBus"]
    assert sorted(players) == [CHROMIUM, "org.mpris.MediaPlayer2.mpv"]
    assert [g for g in fake.gets if g[0] != CHROMIUM] == [
        ("org.mpris.MediaPlayer2.mpv", "GetAll org.mpris.MediaPlayer2")
    ]


def test_a_new_owner_of_the_same_name_is_read_afresh(bus, clock):
    fake = bus({PLASMA: _player(identity="Chromium", title="Ferrous")})
    src = mpris.MPRISSource()
    assert src.read().player == "Chromium"

    fake.owners[PLASMA] = 2
    fake.players[PLASMA] = _player(identity="Firefox", title="Northbound")
    clock.t += 0.25
    track = src.read()
    assert (track.title, track.player) == ("Northbound", "Firefox")


def test_a_player_whose_getall_fails_is_read_property_by_property_from_then_on(bus, clock):
    player = _player()
    player["GetAll"] = FakeDBusException("org.freedesktop.DBus.Error.Failed")
    fake = bus({CHROMIUM: player})
    src = mpris.MPRISSource()
    assert src.read().title == "Glass Tides"
    fake.gets.clear()
    clock.t += 0.25
    assert src.read().title == "Glass Tides"
    assert fake.gets == [
        (CHROMIUM, "PlaybackStatus"),
        (CHROMIUM, "Metadata"),
        (CHROMIUM, "Position"),
    ]


def test_a_new_player_shows_up_with_the_next_name_listing(bus, clock):
    fake = bus({FIREFOX: _player("Paused", identity="Firefox", title="Northbound")})
    src = mpris.MPRISSource()
    assert src.read().title == "Northbound"

    fake.players[CHROMIUM] = _player(title="Ferrous")
    clock.t += 0.25
    assert src.read().title == "Northbound"
    clock.t += src._LIST_REFRESH_S
    assert src.read().title == "Ferrous"


def test_without_any_player_the_names_are_listed_every_poll(bus, clock):
    fake = bus({})
    src = mpris.MPRISSource()
    src.read()
    clock.t += 0.25
    src.read()
    assert fake.listings == 2


def test_a_player_that_disappears_is_forgotten_at_once(bus, clock):
    fake = bus({CHROMIUM: _player(title="Ferrous"), FIREFOX: _player(identity="Firefox")})
    src = mpris.MPRISSource()
    src.read()
    fake.missing.add(CHROMIUM)
    del fake.players[CHROMIUM]
    clock.t += 0.25
    assert src.read().title == "Glass Tides"
    assert CHROMIUM not in src._identities
    clock.t += 0.25
    src.read()
    assert fake.listings == 2
    assert src._player_names == [FIREFOX]


def test_a_player_hanging_on_getall_is_skipped_until_its_cooldown_ends(bus, clock):
    player = _player()
    player["GetAll"] = FakeDBusException("org.freedesktop.DBus.Error.NoReply")
    fake = bus({CHROMIUM: player})
    src = mpris.MPRISSource()
    assert src.read().has_track is False
    assert src._timeout_blacklist[CHROMIUM] == clock.t + src._BLACKLIST_S

    calls = len(fake.gets)
    clock.t += 1.0
    assert src.read().has_track is False
    assert len(fake.gets) == calls

    del player["GetAll"]
    clock.t += src._BLACKLIST_S
    assert src.read().title == "Glass Tides"


def test_a_player_with_garbled_metadata_is_skipped(bus):
    broken = _player()
    broken["Metadata"] = ["not", "a", "dict"]
    bus({FIREFOX: broken, CHROMIUM: _player(title="Northbound", artist=["The Quiet Hours"])})
    track = mpris.MPRISSource().read()
    assert (track.title, track.artist) == ("Northbound", "The Quiet Hours")


def test_the_playing_player_wins_and_the_paused_one_becomes_a_fallback(bus):
    bus(
        {
            FIREFOX: _player("Paused", identity="Firefox", title="Late Train Home"),
            CHROMIUM: _player("Playing", title="Salt Flats", artist=["Wren & Ash"]),
        }
    )
    src = mpris.MPRISSource()
    track = src.read()
    assert (track.title, track.status) == ("Salt Flats", PlaybackStatus.PLAYING)
    assert src._last_player_name == CHROMIUM
    assert src._control_fallback_names == [FIREFOX]


def test_an_unknown_playback_state_reads_as_stopped(bus):
    bus({CHROMIUM: _player("Buffering")})
    assert mpris.MPRISSource().read().status == PlaybackStatus.STOPPED


def test_several_artists_are_joined(bus):
    bus({CHROMIUM: _player(artist=["Wren & Ash", "", "Velvet Static"])})
    assert mpris.MPRISSource().read().artist == "Wren & Ash, Velvet Static"


def test_an_artist_value_that_is_not_a_list_is_dropped(bus):
    bus({CHROMIUM: _player(artist=())})
    bus.state["bus"].players[CHROMIUM]["Metadata"]["xesam:artist"] = 12345
    assert mpris.MPRISSource().read().artist == ""


def test_a_single_artist_string_is_not_split_into_letters(bus):
    bus({CHROMIUM: _player(artist=())})
    bus.state["bus"].players[CHROMIUM]["Metadata"]["xesam:artist"] = "Kite Theory"
    assert mpris.MPRISSource().read().artist == "Kite Theory"


@pytest.mark.parametrize(
    ("album", "title", "expected"),
    [
        ("Salt Flats - Single", "Salt Flats", ""),
        ("Afterimage (Deluxe Edition)", "Overexposed", "Afterimage"),
        ("Iron Garden – EP", "Ferrous", "Iron Garden"),  # noqa: RUF001
        ("Paper Satellites", "Paper Satellites (Remastered)", ""),
        ("Low Light", "Glass Tides", "Low Light"),
        ("", "Glass Tides", ""),
    ],
)
def test_the_album_is_cleaned_of_release_tags(album, title, expected):
    assert mpris._clean_album(album, title) == expected


def _plasma(status):
    return _player(status, title="Overexposed", artist=["Kite Theory"], album="Afterimage")


def _tab(status):
    tab = _player(status, title="‎Afterimage\xa0– Album von Kite Theory\xa0– Apple\xa0Music")
    del tab["Metadata"]["xesam:url"]
    return tab


def test_the_tab_overruling_plasma_is_logged_once_per_change(bus, caplog):
    fake = bus({PLASMA: _plasma("Playing"), CHROMIUM: _tab("Paused")})
    src = mpris.MPRISSource()
    caplog.set_level(logging.DEBUG, logger="refrain.sources.mpris")
    for _ in range(3):
        assert src.read().status == PlaybackStatus.PAUSED
    notes = [r for r in caplog.records if "per the Apple Music tab" in r.getMessage()]
    assert len(notes) == 1

    fake.players[CHROMIUM]["PlaybackStatus"] = "Playing"
    assert src.read().status == PlaybackStatus.PLAYING
    assert src._overruled is None


def test_a_stopped_blip_from_plasma_is_not_overruled(bus):
    bus({PLASMA: _plasma("Stopped"), CHROMIUM: _tab("Playing")})
    assert mpris.MPRISSource().read().status == PlaybackStatus.STOPPED


def test_without_an_apple_music_tab_browser_players_stay_available_for_controls(bus):
    video = _player(title="Some Video - YouTube", url="https://video.example/12345")
    bus({CHROMIUM: video, FIREFOX: _player("Stopped", identity="Firefox", url=None)})
    src = mpris.MPRISSource()
    assert src.read().has_track is False
    assert src._control_fallback_names == [CHROMIUM]
    assert src._native_apple_names == []


def test_controls_report_failure_when_the_bus_is_unreachable(bus):
    bus.state["error"] = FakeDBusException("no session bus")
    src = mpris.MPRISSource()
    src._last_player_name = CHROMIUM
    assert src.play_pause() is False
    assert src.next() is False
    assert src._bus is None


def test_a_failing_capability_probe_counts_as_cannot(bus):
    player = _player()
    player["CanGoPrevious"] = FakeDBusException("org.freedesktop.DBus.Error.NoReply")
    fake = bus({CHROMIUM: player})
    src = mpris.MPRISSource()
    src._last_player_name = CHROMIUM
    assert src.previous() is False
    assert fake.calls == []


def test_an_unexpected_control_error_moves_on_to_the_next_player(bus, caplog):
    broken = _player()
    broken["fail:Next"] = RuntimeError("proxy exploded")
    fake = bus({CHROMIUM: broken, FIREFOX: _player(identity="Firefox")})
    src = mpris.MPRISSource()
    src._last_player_name = CHROMIUM
    src._control_fallback_names = [FIREFOX]
    with caplog.at_level(logging.ERROR, logger="refrain.sources.mpris"):
        assert src.next() is True
    assert fake.calls == [(FIREFOX, "Next")]
    assert any("unexpected error" in r.getMessage() for r in caplog.records)


def test_next_goes_to_the_browser_before_plasma(bus):
    fake = bus({PLASMA: _plasma("Playing"), CHROMIUM: _player()})
    src = mpris.MPRISSource()
    src._last_player_name = PLASMA
    src._control_fallback_names = [CHROMIUM]
    assert src.next() is True
    assert fake.calls == [(CHROMIUM, "Next")]


def test_play_pause_keeps_plasma_first_when_it_is_the_primary(bus):
    fake = bus({PLASMA: _plasma("Playing"), CHROMIUM: _player()})
    src = mpris.MPRISSource()
    src._last_player_name = PLASMA
    src._control_fallback_names = [CHROMIUM]
    assert src.play_pause() is True
    assert fake.calls == [(PLASMA, "PlayPause")]


def test_read_then_control_reaches_the_player_it_read(bus):
    fake = bus(
        {FIREFOX: _player(identity="Firefox", title="Silk Road Radio", artist=["Ilse Moreau"])}
    )
    src = mpris.MPRISSource()
    assert src.read().artist == "Ilse Moreau"
    assert src.play_pause() is True
    assert fake.calls == [(FIREFOX, "PlayPause")]


def test_a_play_pause_the_primary_rejects_is_handed_to_the_next_player(bus):
    primary = _player()
    primary["fail:PlayPause"] = FakeDBusException("org.freedesktop.DBus.Error.NotSupported")
    fake = bus({CHROMIUM: primary, FIREFOX: _player(identity="Firefox")})
    src = mpris.MPRISSource()
    src._last_player_name = CHROMIUM
    src._control_fallback_names = [FIREFOX]
    assert src.play_pause() is True
    assert fake.calls == [(FIREFOX, "PlayPause")]


@pytest.mark.parametrize(("control", "method"), [("play", "Play"), ("pause", "Pause")])
def test_play_and_pause_are_no_toggles(bus, control, method):
    fake = bus(
        {FIREFOX: _player(identity="Firefox", title="Silk Road Radio", artist=["Ilse Moreau"])}
    )
    src = mpris.MPRISSource()
    src.read()
    assert getattr(src, control)() is True
    assert fake.calls == [(FIREFOX, method)]


def test_two_equal_tabs_keep_the_one_shown_before(bus):
    bus(
        {
            FIREFOX: _player("Paused", title="Late Train Home", artist=["Velvet Static"]),
            CHROMIUM: _player("Paused", title="Salt Flats", artist=["Wren & Ash"]),
        }
    )
    src = mpris.MPRISSource()
    first = src.read().title
    shown = src._last_player_name
    for _ in range(3):
        src._names_stale = True
        assert src.read().title == first
    src._last_player_name = FIREFOX if shown == CHROMIUM else CHROMIUM
    assert src.read().title != first
