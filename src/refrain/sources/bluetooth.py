"""BlueZ AVRCP source — reads metadata and dispatches controls.

Auto-detects the active player path; an optional MAC filter restricts
selection to a single paired device.
"""

from __future__ import annotations

import logging

import dbus

from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)


def _bluez_owned(bus) -> bool:
    """Return True iff org.bluez currently has an owner on the system bus.

    Uses ``NameHasOwner`` instead of ``get_object('org.bluez', ...)`` so
    a missing bluez daemon is detected in <1 ms instead of triggering
    D-Bus's 25 s service-activation timeout. On VMs / minimal installs
    without bluez this previously stalled every Refrain poll cycle for
    a full 25 s waiting for an `org.bluez`-activation that would never
    succeed.
    """
    try:
        dbus_obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        dbus_iface = dbus.Interface(dbus_obj, "org.freedesktop.DBus")
        return bool(dbus_iface.NameHasOwner("org.bluez"))
    except Exception as e:
        log.debug("Bluetooth: NameHasOwner(org.bluez) failed: %s", e)
        return False


class BluetoothSource:
    def __init__(self, device_mac: str = ""):
        self._device_mac = device_mac
        self._last_player_path: str | None = None

    def set_device(self, device_mac: str) -> None:
        if device_mac != self._device_mac:
            self._last_player_path = None
        self._device_mac = device_mac

    def read(self) -> TrackInfo:
        try:
            bus = dbus.SystemBus()
        except Exception as e:
            log.debug("Bluetooth: cannot reach system bus: %s", e)
            return TrackInfo.empty()
        if not _bluez_owned(bus):
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
                duration_ms = int(track.get("Duration", 0))
            except Exception:
                duration_ms = 0
            try:
                position_ms = int(props.Get("org.bluez.MediaPlayer1", "Position"))
            except Exception:
                position_ms = 0
            try:
                raw_status = str(props.Get("org.bluez.MediaPlayer1", "Status")).lower()
            except Exception:
                raw_status = "stopped"

            status = (
                PlaybackStatus.PLAYING
                if raw_status == "playing"
                else PlaybackStatus.PAUSED
                if raw_status == "paused"
                else PlaybackStatus.STOPPED
            )

            self._last_player_path = player_path

            return TrackInfo(
                source="bluetooth",
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
                position_ms=position_ms,
                status=status,
            )
        except Exception as e:
            log.debug("Bluetooth player %s unreadable: %s", player_path, e)
            return TrackInfo.empty()

    def play_pause(self) -> bool:
        path = self._last_player_path or self._find_player_safe()
        if not path:
            return False
        # BlueZ exposes Play/Pause as separate methods, not a toggle.
        try:
            bus = dbus.SystemBus()
            obj = bus.get_object("org.bluez", path, introspect=False)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            status = str(props.Get("org.bluez.MediaPlayer1", "Status")).lower()
        except Exception as e:
            log.debug("Bluetooth status query failed: %s", e)
            status = "stopped"
        method = "Pause" if status == "playing" else "Play"
        return self._call_method(method)

    def next(self) -> bool:
        return self._call_method("Next")

    def previous(self) -> bool:
        return self._call_method("Previous")

    def _call_method(self, method: str) -> bool:
        path = self._last_player_path or self._find_player_safe()
        if not path:
            return False
        try:
            bus = dbus.SystemBus()
            if not _bluez_owned(bus):
                return False
            obj = bus.get_object("org.bluez", path, introspect=False)
            iface = dbus.Interface(obj, "org.bluez.MediaPlayer1")
            getattr(iface, method)()
            return True
        except dbus.DBusException as e:
            log.debug("BlueZ %s on %s failed: %s", method, path, e)
            self._last_player_path = None
            return False
        except Exception:
            log.exception("BlueZ %s unexpected error", method)
            return False

    def _find_player_safe(self) -> str | None:
        try:
            return self._find_player(dbus.SystemBus())
        except Exception:
            return None

    def _find_player(self, bus) -> str | None:
        try:
            obj = bus.get_object("org.bluez", "/", introspect=False)
            mgr = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
            objects = mgr.GetManagedObjects()
        except Exception as e:
            log.debug("Bluetooth: GetManagedObjects failed: %s", e)
            return None

        mac_token = self._device_mac.replace(":", "_").lower() if self._device_mac else ""

        for path, ifaces in objects.items():
            if "org.bluez.MediaPlayer1" not in ifaces:
                continue
            path_str = str(path)
            if mac_token and mac_token not in path_str.lower():
                continue
            return path_str
        return None

    @staticmethod
    def list_paired_devices() -> list[dict]:
        """Enumerate paired devices (for the settings-window picker)."""
        try:
            bus = dbus.SystemBus()
        except Exception as e:
            log.debug("Bluetooth list_paired_devices: bus connect failed: %s", e)
            return []
        if not _bluez_owned(bus):
            return []
        try:
            obj = bus.get_object("org.bluez", "/", introspect=False)
            mgr = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
            objects = mgr.GetManagedObjects()
        except Exception as e:
            log.debug("Bluetooth list_paired_devices failed: %s", e)
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
