"""BluetoothSource against a fake BlueZ on a fake system bus."""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

import refrain.sources.bluetooth as bluetooth  # noqa: E402
from refrain.sources.base import PlaybackStatus  # noqa: E402

MAC = "12:34:56:78:9A:BC"
OTHER_MAC = "AB:CD:EF:01:23:45"


def _dev(mac):
    return "/org/bluez/hci0/dev_" + mac.replace(":", "_")


def _player(mac):
    return _dev(mac) + "/avrcp/player0"


class _Obj:
    def __init__(self, bus, path):
        self._bus = bus
        self._path = path

    def NameHasOwner(self, name):  # noqa: N802
        if self._bus.dead:
            raise bluetooth.dbus.DBusException("Disconnected")
        return self._bus.owned

    def GetManagedObjects(self):  # noqa: N802
        if self._bus.dead:
            raise bluetooth.dbus.DBusException("Disconnected")
        return self._bus.objects

    def Get(self, iface, prop):  # noqa: N802
        assert iface == "org.bluez.MediaPlayer1"
        try:
            return self._bus.props[self._path][prop]
        except KeyError:
            # Real BlueZ raises this for an optional AVRCP property the device
            # doesn't implement, not a plain KeyError.
            raise bluetooth.dbus.DBusException(f"No such property {prop}") from None

    def __getattr__(self, method):
        def call():
            if self._bus.fail_calls:
                raise bluetooth.dbus.DBusException("org.bluez.Error.Failed")
            self._bus.calls.append((self._path, method))

        return call


class _Bus:
    def __init__(self, objects=None, props=None, owned=True):
        self.objects = objects or {}
        self.props = props or {}
        self.owned = owned
        self.dead = False
        self.fail_calls = False
        self.calls = []
        self.closed = False

    def get_object(self, name, path, introspect=True):
        return _Obj(self, path)

    def close(self):
        self.closed = True


def _objects(*players):
    objects = {}
    for mac, status, alias, name in players:
        objects[_dev(mac)] = {
            "org.bluez.Device1": {
                "Address": mac,
                "Alias": alias,
                "Name": name,
                "Connected": True,
                "Paired": True,
            }
        }
        objects[_player(mac)] = {"org.bluez.MediaPlayer1": {"Status": status, "Device": _dev(mac)}}
    return objects


def _track_props(status="playing", **over):
    props = {
        "Track": {
            "Title": "Paper Satellites",
            "Artist": "The Quiet Hours",
            "Album": "Low Tide",
            "Duration": 201_000,
        },
        "Position": 42_000,
        "Status": status,
        "Repeat": "off",
        "Name": "Music",
    }
    props.update(over)
    return props


def _bluez_fails(bus):
    """Every call to org.bluez itself fails; the bus daemon still answers."""
    real_get_object = bus.get_object

    def get_object(name, path, introspect=True):
        if name == "org.bluez":
            raise bluetooth.dbus.DBusException("org.freedesktop.DBus.Error.NoReply")
        return real_get_object(name, path)

    bus.get_object = get_object
    return bus


@pytest.fixture
def system_bus(monkeypatch):
    """Every dbus.SystemBus() hands out the next queued fake (or raises it)."""
    queue = []
    opened = []

    def _system_bus(**kwargs):
        opened.append(kwargs)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(bluetooth.dbus, "SystemBus", _system_bus)
    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: obj)
    return queue, opened


def _one_player_bus(status="playing", **over):
    return _Bus(
        _objects((MAC, status, "Desk Speaker", "Speaker 5000")),
        {_player(MAC): _track_props(status, **over)},
    )


def test_read_returns_the_avrcp_track(system_bus):
    queue, opened = system_bus
    queue.append(_one_player_bus())
    track = bluetooth.BluetoothSource().read()
    assert (track.source, track.title, track.artist, track.album) == (
        "bluetooth",
        "Paper Satellites",
        "The Quiet Hours",
        "Low Tide",
    )
    assert (track.duration_ms, track.position_ms) == (201_000, 42_000)
    assert track.status == PlaybackStatus.PLAYING
    assert track.player == "Desk Speaker"
    assert track.loop_track is False


def test_the_source_opens_a_private_bus_without_main_loop(system_bus):
    queue, opened = system_bus
    queue.append(_one_player_bus())
    src = bluetooth.BluetoothSource()
    src.read()
    src.read()
    assert opened == [{"private": True, "mainloop": bluetooth.dbus.mainloop.NULL_MAIN_LOOP}]


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("Playing", PlaybackStatus.PLAYING),
        ("paused", PlaybackStatus.PAUSED),
        ("stopped", PlaybackStatus.STOPPED),
        ("forward-seek", PlaybackStatus.STOPPED),
    ],
)
def test_avrcp_status_maps_to_playback_status(system_bus, raw, status):
    system_bus[0].append(_one_player_bus(raw))
    assert bluetooth.BluetoothSource().read().status == status


def test_repeat_singletrack_marks_the_track_as_looping(system_bus):
    system_bus[0].append(_one_player_bus(Repeat="singletrack"))
    assert bluetooth.BluetoothSource().read().loop_track is True


def test_missing_optional_properties_fall_back_to_defaults(system_bus):
    bus = _one_player_bus()
    props = bus.props[_player(MAC)]
    for key in ("Position", "Status", "Repeat", "Name"):
        del props[key]
    props["Track"]["Duration"] = "not a number"
    system_bus[0].append(bus)
    track = bluetooth.BluetoothSource().read()
    assert track.title == "Paper Satellites"
    assert (track.duration_ms, track.position_ms) == (0, 0)
    assert track.status == PlaybackStatus.STOPPED
    assert track.loop_track is False


def test_an_unreadable_track_is_empty(system_bus):
    bus = _one_player_bus()
    del bus.props[_player(MAC)]["Track"]
    system_bus[0].append(bus)
    assert bluetooth.BluetoothSource().read().has_track is False


def test_no_bluez_on_the_bus_reads_nothing(system_bus):
    bus = _one_player_bus()
    bus.owned = False
    system_bus[0].append(bus)
    assert bluetooth.BluetoothSource().read().has_track is False


def test_no_media_player_reads_nothing(system_bus):
    objects = _objects((MAC, "playing", "Desk Speaker", ""))
    del objects[_player(MAC)]
    system_bus[0].append(_Bus(objects))
    assert bluetooth.BluetoothSource().read().has_track is False


def test_a_failing_object_manager_reads_nothing(system_bus):
    system_bus[0].append(_bluez_fails(_one_player_bus()))
    assert bluetooth.BluetoothSource().read().has_track is False


def test_a_playing_device_wins_over_an_idle_one(system_bus):
    objects = _objects(
        (OTHER_MAC, "paused", "Kitchen Radio", ""),
        (MAC, "playing", "Desk Speaker", ""),
    )
    bus = _Bus(objects, {_player(MAC): _track_props(), _player(OTHER_MAC): _track_props()})
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    assert src.read().player == "Desk Speaker"
    assert src._last_player_path == _player(MAC)


def test_a_paused_device_wins_over_a_stopped_one(system_bus):
    objects = _objects(
        (OTHER_MAC, "stopped", "Kitchen Radio", ""),
        (MAC, "paused", "Desk Speaker", ""),
    )
    system_bus[0].append(_Bus(objects))
    src = bluetooth.BluetoothSource()
    assert src._find_player(src._system_bus()) == _player(MAC)


def test_the_device_name_falls_back_to_name_without_alias(system_bus):
    system_bus[0].append(_Bus(_objects((MAC, "playing", "", "Speaker 5000"))))
    src = bluetooth.BluetoothSource()
    src._find_player(src._system_bus())
    assert src._player_name == "Speaker 5000"


def test_a_player_without_device_has_no_name():
    assert bluetooth._device_name({}, {"Device": "/org/bluez/hci0/dev_gone"}) == ""
    assert bluetooth._device_name(None, {}) == ""


def test_the_mac_filter_restricts_to_that_device(system_bus):
    objects = _objects(
        (MAC, "playing", "Desk Speaker", ""),
        (OTHER_MAC, "paused", "Kitchen Radio", ""),
    )
    system_bus[0].append(_Bus(objects))
    src = bluetooth.BluetoothSource(device_mac=OTHER_MAC.lower())
    assert src._find_player(src._system_bus()) == _player(OTHER_MAC)
    assert src._player_name == "Kitchen Radio"


def test_the_mac_filter_finds_nothing_for_an_absent_device(system_bus):
    system_bus[0].append(_Bus(_objects((MAC, "playing", "Desk Speaker", ""))))
    src = bluetooth.BluetoothSource(device_mac=OTHER_MAC)
    assert src._find_player(src._system_bus()) is None


def test_changing_the_device_forgets_the_last_player():
    src = bluetooth.BluetoothSource(device_mac=MAC)
    src._last_player_path = _player(MAC)
    src.set_device(MAC)
    assert src._last_player_path == _player(MAC)
    src.set_device(OTHER_MAC)
    assert src._last_player_path is None
    assert src._device_mac == OTHER_MAC


def test_an_unreachable_system_bus_is_retried_on_the_next_read(system_bus):
    queue, opened = system_bus
    queue.extend([bluetooth.dbus.DBusException("no system bus"), _one_player_bus()])
    src = bluetooth.BluetoothSource()
    assert src.read().has_track is False
    assert src._bus is None
    assert src.read().title == "Paper Satellites"
    assert len(opened) == 2


def test_a_dead_cached_bus_is_replaced_by_a_new_connection(system_bus):
    queue, opened = system_bus
    dead = _one_player_bus()
    queue.extend([dead, _one_player_bus()])
    src = bluetooth.BluetoothSource()
    assert src.read().has_track is True
    dead.dead = True
    src.read()
    assert src.read().title == "Paper Satellites"


def test_drop_bus_closes_the_connection():
    src = bluetooth.BluetoothSource()
    bus = _Bus()
    src._bus = bus
    src._drop_bus()
    assert bus.closed and src._bus is None


def test_drop_bus_survives_a_failing_close():
    src = bluetooth.BluetoothSource()
    bus = _Bus()

    def boom():
        raise OSError("already gone")

    bus.close = boom
    src._bus = bus
    src._drop_bus()
    assert src._bus is None


def test_bluez_owned_is_unknown_when_the_query_fails(monkeypatch):
    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: obj)
    bus = _Bus()
    bus.dead = True
    assert bluetooth._bluez_owned(bus) is None


@pytest.mark.parametrize(("status", "method"), [("playing", "Pause"), ("paused", "Play")])
def test_play_pause_calls_the_matching_bluez_method(system_bus, status, method):
    bus = _one_player_bus(status)
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    assert src.play_pause() is True
    assert bus.calls == [(_player(MAC), method)]


def test_play_pause_plays_when_the_status_is_unreadable(system_bus):
    bus = _one_player_bus()
    del bus.props[_player(MAC)]["Status"]
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    assert src.play_pause() is True
    assert bus.calls == [(_player(MAC), "Play")]


@pytest.mark.parametrize("control", ["next", "previous"])
def test_skip_controls_use_the_last_read_player(system_bus, control):
    bus = _one_player_bus()
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    src.read()
    assert getattr(src, control)() is True
    assert bus.calls == [(_player(MAC), control.capitalize())]


def test_controls_without_a_player_do_nothing(system_bus):
    system_bus[0].append(_Bus())
    src = bluetooth.BluetoothSource()
    assert src.next() is False
    assert src.play_pause() is False


def test_controls_without_a_bus_do_nothing(system_bus):
    system_bus[0].append(bluetooth.dbus.DBusException("no system bus"))
    assert bluetooth.BluetoothSource().previous() is False


def test_controls_without_bluez_do_nothing(system_bus):
    bus = _one_player_bus()
    bus.owned = False
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    src._last_player_path = _player(MAC)
    assert src.next() is False
    assert bus.calls == []


def test_a_failed_control_forgets_the_player(system_bus):
    bus = _one_player_bus()
    bus.fail_calls = True
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    src.read()
    assert src.next() is False
    assert src._last_player_path is None


def test_an_unexpected_control_error_is_not_raised(system_bus, monkeypatch):
    system_bus[0].append(_one_player_bus())
    src = bluetooth.BluetoothSource()
    src.read()
    monkeypatch.setattr(
        bluetooth.dbus,
        "Interface",
        lambda obj, iface: object() if iface == "org.bluez.MediaPlayer1" else obj,
    )
    assert src.next() is False
    assert src._last_player_path == _player(MAC)


def test_list_paired_devices_reports_every_device(system_bus):
    objects = _objects(
        (MAC, "playing", "Desk Speaker", "Speaker 5000"),
        (OTHER_MAC, "paused", "Kitchen Radio", ""),
    )
    objects[_dev(OTHER_MAC)]["org.bluez.Device1"].update(Connected=False, Paired=False)
    system_bus[0].append(_Bus(objects))
    devices = bluetooth.BluetoothSource.list_paired_devices()
    assert sorted(devices, key=lambda d: d["address"]) == [
        {"address": MAC, "name": "Speaker 5000", "connected": True, "paired": True},
        {"address": OTHER_MAC, "name": "Kitchen Radio", "connected": False, "paired": False},
    ]


def test_list_paired_devices_uses_the_shared_system_bus(system_bus):
    queue, opened = system_bus
    queue.append(_Bus())
    assert bluetooth.BluetoothSource.list_paired_devices() == []
    assert opened == [{}]


def test_list_paired_devices_is_empty_without_a_bus(system_bus):
    system_bus[0].append(bluetooth.dbus.DBusException("no system bus"))
    assert bluetooth.BluetoothSource.list_paired_devices() == []


def test_list_paired_devices_is_empty_without_bluez(system_bus):
    bus = _one_player_bus()
    bus.owned = False
    system_bus[0].append(bus)
    assert bluetooth.BluetoothSource.list_paired_devices() == []


def test_list_paired_devices_is_empty_when_bluez_fails(system_bus):
    system_bus[0].append(_bluez_fails(_one_player_bus()))
    assert bluetooth.BluetoothSource.list_paired_devices() == []


def test_a_non_music_app_is_logged_once_not_every_poll(system_bus, caplog):
    system_bus[0].append(_one_player_bus(Name="Twitch"))
    src = bluetooth.BluetoothSource()
    with caplog.at_level("INFO", logger=bluetooth.__name__):
        for _ in range(3):
            assert src.read().has_track is False
    assert [r.getMessage() for r in caplog.records].count(
        "Bluetooth: Twitch is playing — not Apple Music, ignored"
    ) == 1


@pytest.mark.parametrize(("control", "method"), [("play", "Play"), ("pause", "Pause")])
@pytest.mark.parametrize("status", ["playing", "paused"])
def test_play_and_pause_are_sent_as_asked_whatever_the_status(system_bus, control, method, status):
    bus = _one_player_bus(status)
    system_bus[0].append(bus)
    src = bluetooth.BluetoothSource()
    assert getattr(src, control)() is True
    assert bus.calls == [(_player(MAC), method)]


def test_debug_logs_never_carry_a_whole_device_address():
    masked = bluetooth._masked(
        "/org/bluez/hci0/dev_12_34_56_78_9A_BC/player0 gone, 12:34:56:78:9A:BC unreachable"
    )
    assert (
        masked
        == "/org/bluez/hci0/dev_XX_XX_XX_XX_9A_BC/player0 gone, XX:XX:XX:XX:9A:BC unreachable"
    )
    assert bluetooth._masked("/org/bluez/hci0") == "/org/bluez/hci0"


def test_bluez_owned_lets_a_programming_error_through(monkeypatch):
    """A DBusException means "can't tell"; anything else is a bug and must not be hidden."""

    class BrokenIface:
        def NameHasOwner(self, name):
            raise TypeError("boom")

    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: BrokenIface())
    with pytest.raises(TypeError):
        bluetooth._bluez_owned(_Bus())


def test_find_player_lets_a_programming_error_through(monkeypatch):
    class BrokenMgr:
        def GetManagedObjects(self):  # noqa: N802
            raise TypeError("boom")

    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, iface: BrokenMgr())
    with pytest.raises(TypeError):
        bluetooth.BluetoothSource()._find_player(_Bus())


def test_read_stops_using_a_field_once_its_getter_has_a_bug(system_bus, monkeypatch):
    """A DBusException from Position's Get() falls back to a default (see
    test_missing_optional_properties_fall_back_to_defaults); a bug there must not."""
    bus = _one_player_bus()
    system_bus[0].append(bus)

    real_get = _Obj.Get

    def broken_get(self, iface, prop):
        if prop == "Position":
            raise TypeError("boom")
        return real_get(self, iface, prop)

    monkeypatch.setattr(_Obj, "Get", broken_get)
    assert bluetooth.BluetoothSource().read().has_track is False
