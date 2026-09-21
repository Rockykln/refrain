"""Fake MPRIS player for integration tests. Real dbus-python service on a private bus.

Commands as JSON lines on stdin: {"metadata": {...}, "status": "Playing", "position_us": N}.
Method calls (Play/Pause/PlayPause/Next/Previous) are appended, one per line, to --calls-file.
"QUIT" on stdin exits cleanly.
"""

from __future__ import annotations

import argparse
import json
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from PySide6.QtCore import QCoreApplication, QSocketNotifier

_ROOT_IFACE = "org.mpris.MediaPlayer2"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"


def _to_metadata(raw: dict) -> dbus.Dictionary:
    md = dbus.Dictionary({}, signature="sv", variant_level=1)
    for key, value in raw.items():
        if key == "mpris:length":
            md[key] = dbus.Int64(value)
        elif key == "xesam:artist":
            md[key] = dbus.Array([dbus.String(a) for a in value], signature="s")
        else:
            md[key] = dbus.String(value)
    return md


class FakePlayer(dbus.service.Object):
    def __init__(self, bus, path, identity, desktop_entry, calls_file):
        super().__init__(bus, path)
        self.identity = identity
        self.desktop_entry = desktop_entry
        self.calls_file = calls_file
        self.status = "Stopped"
        self.metadata = dbus.Dictionary({}, signature="sv")
        self.position = dbus.Int64(0)

    def _log_call(self, method: str) -> None:
        if not self.calls_file:
            return
        with open(self.calls_file, "a", encoding="utf-8") as f:
            f.write(method + "\n")

    def _emit_changed(self, changed: dict) -> None:
        self.PropertiesChanged(
            _PLAYER_IFACE, dbus.Dictionary(changed, signature="sv"), dbus.Array([], signature="s")
        )

    # org.mpris.MediaPlayer2.Player — real method calls a controller would make.
    @dbus.service.method(_PLAYER_IFACE)
    def Play(self):
        self._log_call("Play")
        self.status = "Playing"
        self._emit_changed({"PlaybackStatus": self.status})

    @dbus.service.method(_PLAYER_IFACE)
    def Pause(self):
        self._log_call("Pause")
        self.status = "Paused"
        self._emit_changed({"PlaybackStatus": self.status})

    @dbus.service.method(_PLAYER_IFACE)
    def PlayPause(self):
        self._log_call("PlayPause")
        self.status = "Paused" if self.status == "Playing" else "Playing"
        self._emit_changed({"PlaybackStatus": self.status})

    @dbus.service.method(_PLAYER_IFACE)
    def Stop(self):
        self._log_call("Stop")
        self.status = "Stopped"
        self._emit_changed({"PlaybackStatus": self.status})

    @dbus.service.method(_PLAYER_IFACE)
    def Next(self):
        self._log_call("Next")

    @dbus.service.method(_PLAYER_IFACE)
    def Previous(self):
        self._log_call("Previous")

    # org.freedesktop.DBus.Properties
    @dbus.service.method(_PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.GetAll(interface)[prop]

    @dbus.service.method(_PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface == _ROOT_IFACE:
            return dbus.Dictionary(
                {
                    "Identity": dbus.String(self.identity),
                    "DesktopEntry": dbus.String(self.desktop_entry),
                    "CanQuit": dbus.Boolean(False),
                    "CanRaise": dbus.Boolean(False),
                },
                signature="sv",
            )
        if interface == _PLAYER_IFACE:
            return dbus.Dictionary(
                {
                    "PlaybackStatus": dbus.String(self.status),
                    "Metadata": self.metadata,
                    "Position": self.position,
                    "CanPlay": dbus.Boolean(True),
                    "CanPause": dbus.Boolean(True),
                    "CanGoNext": dbus.Boolean(True),
                    "CanGoPrevious": dbus.Boolean(True),
                },
                signature="sv",
            )
        return dbus.Dictionary({}, signature="sv")

    @dbus.service.method(_PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        pass

    @dbus.service.signal(_PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def apply_command(self, cmd: dict) -> None:
        changed = {}
        if "status" in cmd:
            self.status = cmd["status"]
            changed["PlaybackStatus"] = self.status
        if "metadata" in cmd:
            self.metadata = _to_metadata(cmd["metadata"])
            changed["Metadata"] = self.metadata
        if "position_us" in cmd:
            self.position = dbus.Int64(cmd["position_us"])
        if changed:
            self._emit_changed(changed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True, help="suffix after org.mpris.MediaPlayer2.")
    parser.add_argument("--identity", required=True)
    parser.add_argument("--desktop-entry", default="")
    parser.add_argument("--calls-file", default="")
    args = parser.parse_args()

    DBusGMainLoop(set_as_default=True)
    threads_init()
    app = QCoreApplication([])
    bus = dbus.SessionBus()
    player = FakePlayer(
        bus, "/org/mpris/MediaPlayer2", args.identity, args.desktop_entry, args.calls_file
    )
    # Must stay referenced on something long-lived: an unassigned BusName is
    # garbage-collected right away, which releases the name before any client sees it.
    player.bus_name = dbus.service.BusName(f"org.mpris.MediaPlayer2.{args.suffix}", bus)

    def on_stdin_readable() -> None:
        line = sys.stdin.readline()
        if not line:
            app.quit()
            return
        line = line.strip()
        if not line:
            return
        if line == "QUIT":
            app.quit()
            return
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            return
        player.apply_command(cmd)

    notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read)
    notifier.activated.connect(lambda _fd: on_stdin_readable())

    sys.stdout.write("READY\n")
    sys.stdout.flush()
    app.exec()


if __name__ == "__main__":
    main()
