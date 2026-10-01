"""Put a line on the desktop when Refrain has no window to say it in."""

from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


def show(title: str, text: str) -> None:
    # Started from the menu, stderr goes nowhere; this is the only thing the user sees.
    if notify := shutil.which("notify-send"):
        subprocess.run([notify, "-a", "Refrain", title, text], check=False)
        return
    try:
        import dbus

        bus = dbus.SessionBus()
        server = bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
        dbus.Interface(server, "org.freedesktop.Notifications").Notify(
            "Refrain", 0, "", title, text, [], {}, -1
        )
    except Exception as e:
        log.warning("Could not show the notification either: %s", e)
