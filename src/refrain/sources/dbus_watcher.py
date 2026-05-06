"""D-Bus PropertiesChanged listener — fires when MPRIS / BlueZ updates.

The poll loop in `daemon.py` runs at 1 Hz; that's fine for source
*discovery* (a new browser tab showing up on the session bus, a phone
connecting over Bluetooth), but it adds up to a one-second worst case
for track-change reactions. By subscribing to
`org.freedesktop.DBus.Properties.PropertiesChanged` on the chosen
player, we can poke the daemon to re-read immediately when something
actually changes — track switches, pauses, seeks all become instant
without raising the polling rate.

Implemented with `QDBusConnection` (Qt's own D-Bus binding) instead of
`dbus-python` because Qt's binding integrates natively with the
QApplication event loop, no separate GLib mainloop required.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal
from PySide6.QtDBus import QDBusConnection

log = logging.getLogger(__name__)


class _PropertiesChangedWatcher(QObject):
    """Subscribe to `PropertiesChanged` on a single (service, path) pair.

    Emits ``changed`` whenever the signal fires. The subscription list is
    flat — if you need to track multiple paths, hold one instance per
    pair (this is what the source-specific subclasses below do).
    """

    changed = Signal()

    _PROPS_IFACE = "org.freedesktop.DBus.Properties"
    _PROPS_SIGNAL = "PropertiesChanged"

    def __init__(self, bus: QDBusConnection, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._subscribed: tuple[str, str] | None = None

    def subscribe(self, service: str, path: str) -> None:
        if self._subscribed == (service, path):
            return
        self.unsubscribe()
        ok = self._bus.connect(
            service,
            path,
            self._PROPS_IFACE,
            self._PROPS_SIGNAL,
            self._on_props_changed,
        )
        if ok:
            self._subscribed = (service, path)
            log.debug("DBus watcher subscribed: %s %s", service, path)
        else:
            log.debug("DBus watcher subscribe failed: %s %s", service, path)

    def unsubscribe(self) -> None:
        if not self._subscribed:
            return
        service, path = self._subscribed
        self._bus.disconnect(
            service,
            path,
            self._PROPS_IFACE,
            self._PROPS_SIGNAL,
            self._on_props_changed,
        )
        self._subscribed = None

    def _on_props_changed(self, *_args) -> None:
        # We don't actually need the changed-properties payload here —
        # the daemon re-reads everything fresh anyway. Suppressing the
        # arguments keeps the slot signature flexible across Qt versions.
        self.changed.emit()


class MPRISWatcher(QObject):
    """Tracks the *currently active* MPRIS player by bus name.

    The daemon picks a player during each poll — when that pick changes
    (browser tab switched, etc.), call ``set_target()`` to re-subscribe.
    Emits ``changed`` whenever the active player publishes a property
    update.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._w = _PropertiesChangedWatcher(QDBusConnection.sessionBus(), self)
        self._w.changed.connect(self.changed)

    def set_target(self, bus_name: str | None) -> None:
        if not bus_name:
            self._w.unsubscribe()
            return
        self._w.subscribe(bus_name, "/org/mpris/MediaPlayer2")


class BluetoothWatcher(QObject):
    """Tracks a set of BlueZ ``MediaPlayer1`` object paths on the system bus.

    BlueZ exposes one player per connected AVRCP device; the manager bag
    enumerates them at poll time. We re-call ``set_targets()`` with the
    fresh path list on every poll so newly-connected devices start
    delivering signals immediately.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = QDBusConnection.systemBus()
        self._watchers: dict[str, _PropertiesChangedWatcher] = {}

    def set_targets(self, paths: Iterable[str]) -> None:
        wanted = set(paths)
        # Drop subscriptions we no longer need.
        for path in list(self._watchers):
            if path not in wanted:
                self._watchers[path].unsubscribe()
                del self._watchers[path]
        # Add new ones.
        for path in wanted:
            if path in self._watchers:
                continue
            w = _PropertiesChangedWatcher(self._bus, self)
            w.changed.connect(self.changed)
            w.subscribe("org.bluez", path)
            self._watchers[path] = w
