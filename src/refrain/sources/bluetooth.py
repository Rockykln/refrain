"""BlueZ AVRCP source: reads metadata and dispatches controls for the active player,
optionally restricted to one paired device."""

from __future__ import annotations

import contextlib
import logging
import re

import dbus
import dbus.mainloop

from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

_ADDRESS = re.compile(
    r"([0-9A-Fa-f]{2})([_:])(?:[0-9A-Fa-f]{2}\2){3}([0-9A-Fa-f]{2}\2[0-9A-Fa-f]{2})"
)


def _masked(text: object) -> str:
    """Hide all but the last two bytes of device addresses: logs get shared in bug reports."""
    return _ADDRESS.sub(lambda m: f"XX{m[2]}XX{m[2]}XX{m[2]}XX{m[2]}{m[3]}", str(text))


def _bluez_owned(bus) -> bool | None:
    """Whether org.bluez has an owner on the system bus; None when the bus didn't answer.

    Uses ``NameHasOwner`` instead of ``get_object('org.bluez', ...)`` so
    a missing bluez daemon (VMs, minimal installs) is detected in <1 ms
    instead of stalling every poll on D-Bus's 25 s service-activation timeout.
    """
    try:
        dbus_obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        dbus_iface = dbus.Interface(dbus_obj, "org.freedesktop.DBus")
        return bool(dbus_iface.NameHasOwner("org.bluez"))
    except dbus.exceptions.DBusException as e:
        log.debug("Bluetooth: NameHasOwner(org.bluez) failed: %s", e)
        return None


def _device_name(objects, player_props) -> str:
    """The name of the device a MediaPlayer1 belongs to, or ``""``.

    ``Alias`` first: it is what the user renamed the device to, and it
    falls back to ``Name`` inside BlueZ anyway.
    """
    try:
        # Pure dict lookups on an already-fetched GetManagedObjects() reply, not a D-Bus
        # call — broad on purpose, since a device can shape that reply oddly.
        device = objects.get(player_props.get("Device", ""), {}).get("org.bluez.Device1", {})
        return str(device.get("Alias", "") or device.get("Name", "") or "")
    except Exception:
        return ""


# What a phone names Apple Music over AVRCP, lower-cased and localised.
# Refrain is an Apple Music companion, so a song from another service on the
# same headphones is not one to put on Discord, in the history or on Last.fm.
_APPLE_MUSIC_APPS = frozenset(
    {
        "apple music",
        "music",
        "musik",
        "musique",
        "música",
        "musica",
        "muziek",
        "musikk",
        "muzyka",
        "hudba",
        "музыка",
        "музика",
        "müzik",
        "音楽",
        "音乐",
        "뮤직",
        "음악",
    }
)


def is_apple_music(app: str) -> bool:
    """Is Apple Music what the phone is playing?

    A phone that names no app at all still counts — AVRCP leaves the name
    optional, and an empty one is no reason to ignore the song.
    """
    name = app.strip().casefold()
    return not name or name in _APPLE_MUSIC_APPS or "apple music" in name


class BluetoothSource:
    def __init__(self, device_mac: str = ""):
        self._device_mac = device_mac
        self._last_player_path: str | None = None
        # Name of the device behind the player `_find_player` picked —
        # read from the same GetManagedObjects reply, so it costs no
        # extra bus call.
        self._player_name = ""
        self._ignored_app = ""  # logged once, not every poll
        self._bus = None  # see _system_bus

    def set_device(self, device_mac: str) -> None:
        if device_mac != self._device_mac:
            self._last_player_path = None
        self._device_mac = device_mac

    def _system_bus(self):
        """This source's own system-bus connection, with no main loop.

        Same reason as ``MPRISSource._session_bus``: the source runs on the
        daemon thread, and sharing ``dbus.SystemBus()`` — which the
        Settings window's device picker uses on the main thread — would put
        both threads on one dbus-glib-dispatched connection.
        """
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True, mainloop=dbus.mainloop.NULL_MAIN_LOOP)
        return self._bus

    def _drop_bus(self) -> None:
        bus, self._bus = self._bus, None
        if bus is not None:
            # Broad on purpose: closing a dying connection can fail below the
            # D-Bus protocol (socket/OS errors), and must never block opening
            # a fresh one on the next read.
            with contextlib.suppress(Exception):
                bus.close()

    def read(self) -> TrackInfo:
        try:
            bus = self._system_bus()
        except dbus.exceptions.DBusException as e:
            log.debug("Bluetooth: cannot reach system bus: %s", e)
            self._drop_bus()
            return TrackInfo.empty()
        owned = _bluez_owned(bus)
        if owned is None:
            self._drop_bus()  # a connection that died stays dead; open a new one next poll
        if not owned:
            return TrackInfo.empty()

        player_path = self._find_player(bus)
        if not player_path:
            return TrackInfo.empty()

        try:
            player = bus.get_object("org.bluez", player_path, introspect=False)
            props = dbus.Interface(player, "org.freedesktop.DBus.Properties")
            track = props.Get("org.bluez.MediaPlayer1", "Track")

            title = str(track.get("Title", "") or "")
            artist = str(track.get("Artist", "") or "")
            album = str(track.get("Album", "") or "")

            try:
                # track.get() is a plain dict lookup, not a D-Bus call — broad on
                # purpose, since a device can put anything in "Duration".
                duration_ms = int(track.get("Duration", 0))
            except Exception:
                duration_ms = 0
            try:
                position_ms = int(props.Get("org.bluez.MediaPlayer1", "Position"))
            except dbus.exceptions.DBusException:
                position_ms = 0
            try:
                raw_status = str(props.Get("org.bluez.MediaPlayer1", "Status")).lower()
            except dbus.exceptions.DBusException:
                raw_status = "stopped"
            try:
                repeat = str(props.Get("org.bluez.MediaPlayer1", "Repeat")).lower()
            except dbus.exceptions.DBusException:
                repeat = ""  # optional in AVRCP; many devices don't report it

            status = (
                PlaybackStatus.PLAYING
                if raw_status == "playing"
                else PlaybackStatus.PAUSED
                if raw_status == "paused"
                else PlaybackStatus.STOPPED
            )

            self._last_player_path = player_path

            try:
                app = str(props.Get("org.bluez.MediaPlayer1", "Name"))
            except dbus.exceptions.DBusException:
                app = ""  # optional in AVRCP
            if not is_apple_music(app):
                if app != self._ignored_app:
                    log.info("Bluetooth: %s is playing — not Apple Music, ignored", app)
                    self._ignored_app = app
                return TrackInfo.empty()
            self._ignored_app = ""

            return TrackInfo(
                source="bluetooth",
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
                position_ms=position_ms,
                status=status,
                player=self._player_name,
                loop_track=repeat == "singletrack",
            )
        # Broad on purpose: this block also runs is_apple_music() and builds the
        # TrackInfo, not just D-Bus calls.
        except Exception as e:
            log.debug("Bluetooth player %s unreadable: %s", _masked(player_path), _masked(e))
            return TrackInfo.empty()

    def play_pause(self) -> bool:
        path = self._last_player_path or self._find_player_safe()
        if not path:
            return False
        # BlueZ exposes Play/Pause as separate methods, not a toggle.
        try:
            bus = self._system_bus()
            obj = bus.get_object("org.bluez", path, introspect=False)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            status = str(props.Get("org.bluez.MediaPlayer1", "Status")).lower()
        except dbus.exceptions.DBusException as e:
            log.debug("Bluetooth status query failed: %s", _masked(e))
            status = "stopped"
        method = "Pause" if status == "playing" else "Play"
        return self._call_method(method)

    def play(self) -> bool:
        return self._call_method("Play")

    def pause(self) -> bool:
        return self._call_method("Pause")

    def next(self) -> bool:
        return self._call_method("Next")

    def previous(self) -> bool:
        return self._call_method("Previous")

    def _call_method(self, method: str) -> bool:
        path = self._last_player_path or self._find_player_safe()
        if not path:
            return False
        try:
            bus = self._system_bus()
            if not _bluez_owned(bus):
                return False
            obj = bus.get_object("org.bluez", path, introspect=False)
            iface = dbus.Interface(obj, "org.bluez.MediaPlayer1")
            getattr(iface, method)()
            return True
        except dbus.exceptions.DBusException as e:
            log.debug("BlueZ %s on %s failed: %s", method, _masked(path), _masked(e))
            self._last_player_path = None
            return False
        except Exception:
            log.exception("BlueZ %s unexpected error", method)
            return False

    def _find_player_safe(self) -> str | None:
        try:
            # Broad on purpose: _find_player() also walks and ranks the
            # GetManagedObjects() reply, not just the D-Bus call.
            return self._find_player(self._system_bus())
        except Exception:
            return None

    def _find_player(self, bus) -> str | None:
        try:
            obj = bus.get_object("org.bluez", "/", introspect=False)
            mgr = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
            objects = mgr.GetManagedObjects()
        except dbus.exceptions.DBusException as e:
            log.debug("Bluetooth: GetManagedObjects failed: %s", _masked(e))
            return None

        mac_token = self._device_mac.replace(":", "_").lower() if self._device_mac else ""

        # With two devices connected, the first player listed may be the idle one.
        rank = {"playing": 0, "paused": 1}
        best: tuple[int, str, dict] | None = None
        for path, ifaces in objects.items():
            if "org.bluez.MediaPlayer1" not in ifaces:
                continue
            path_str = str(path)
            if mac_token and mac_token not in path_str.lower():
                continue
            props = ifaces["org.bluez.MediaPlayer1"]
            score = rank.get(str(props.get("Status", "")).lower(), 2)
            if best is None or score < best[0]:
                best = (score, path_str, props)
        if best is None:
            return None
        self._player_name = _device_name(objects, best[2])
        return best[1]

    @staticmethod
    def list_paired_devices() -> list[dict]:
        """Enumerate paired devices (for the settings-window picker)."""
        try:
            bus = dbus.SystemBus()
        except dbus.exceptions.DBusException as e:
            log.debug("Bluetooth list_paired_devices: bus connect failed: %s", e)
            return []
        if not _bluez_owned(bus):
            return []
        try:
            obj = bus.get_object("org.bluez", "/", introspect=False)
            mgr = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
            objects = mgr.GetManagedObjects()
        except dbus.exceptions.DBusException as e:
            log.debug("Bluetooth list_paired_devices failed: %s", _masked(e))
            return []

        devices: list[dict] = []
        for _, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            devices.append(
                {
                    "address": str(dev.get("Address", "")),
                    "name": str(dev.get("Name", "") or dev.get("Alias", "") or ""),
                    "connected": bool(dev.get("Connected", False)),
                    "paired": bool(dev.get("Paired", False)),
                }
            )
        return devices
